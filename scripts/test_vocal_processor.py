"""Тест VocalProcessor — проверка применения разметки громкости к вокальной дорожке.

Запуск:
    uv run python scripts/test_vocal_processor.py <vocal_file> [output_file]

Пример:
    uv run python scripts/test_vocal_processor.py tracks/my_track/vocals.mp3

Скрипт:
1. Строит тестовую разметку громкости (имитирует chorus/non-chorus сегменты).
2. Выводит ffmpeg-фильтр, который будет применён.
3. Запускает VocalProcessor.process() и сохраняет результат.
4. Выводит размер входного и выходного файлов для сравнения.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path, чтобы импортировать app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.vocal_processor import VocalProcessor
from app.chorus_detector import VolumeSegment

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_test_segments(duration_sec: float) -> list[VolumeSegment]:
    """Построить тестовую разметку громкости.

    Имитирует типичный трек:
    - 0–30 сек: non-chorus, громкость 0.4
    - 30–60 сек: chorus, громкость 1.0
    - 60–90 сек: non-chorus, громкость 0.4
    - 90–120 сек: chorus, громкость 1.0
    - 120–end: non-chorus, громкость 0.4

    Parameters
    ----------
    duration_sec:
        Длительность трека в секундах.

    Returns
    -------
    list[VolumeSegment]
        Список сегментов громкости, покрывающих весь трек.
    """
    segments: list[VolumeSegment] = []
    chorus_volume = 1.0
    non_chorus_volume = 0.4

    # Сегменты по 30 секунд, чередуя non-chorus / chorus
    pos = 0.0
    is_chorus = False
    while pos < duration_sec:
        end = min(pos + 30.0, duration_sec)
        vol = chorus_volume if is_chorus else non_chorus_volume
        seg_type = "chorus" if is_chorus else "non-chorus"
        segments.append(
            VolumeSegment(
                start=pos,
                end=end,
                volume=vol,
                segment_type=seg_type,
            )
        )
        pos = end
        is_chorus = not is_chorus

    return segments


async def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Использование: uv run python scripts/test_vocal_processor.py "
            "<vocal_file> [output_file]"
        )
        sys.exit(1)

    vocal_file = sys.argv[1]
    vocal_path = Path(vocal_file)

    if not vocal_path.exists():
        print(f"Ошибка: файл не найден: {vocal_file}")
        sys.exit(1)

    # Определяем путь к выходному файлу
    if len(sys.argv) >= 3:
        output_file = sys.argv[2]
    else:
        output_file = str(vocal_path.parent / f"{vocal_path.stem}_processed_test.mp3")

    # Получаем длительность файла через ffprobe
    duration_sec = await _get_duration(vocal_file)
    logger.info("Длительность входного файла: %.2f сек", duration_sec)

    # Строим тестовую разметку громкости
    segments = build_test_segments(duration_sec)
    logger.info("Построено %d сегментов громкости:", len(segments))
    for seg in segments:
        logger.info(
            "  [%.1f – %.1f] %s vol=%.2f",
            seg.start,
            seg.end,
            seg.segment_type or "?",
            seg.volume,
        )

    # Показываем ffmpeg-фильтр, который будет применён
    processor = VocalProcessor()
    volume_filter = processor._build_volume_filter(segments)
    print(f"\nffmpeg volume filter:\n  {volume_filter}\n")

    # Запускаем обработку
    logger.info("Запуск VocalProcessor.process()...")
    result = await processor.process(
        vocal_file=vocal_file,
        volume_segments=segments,
        output_file=output_file,
    )

    # Сравниваем размеры файлов
    input_size = vocal_path.stat().st_size
    output_size = Path(result).stat().st_size
    print(f"\nРезультат:")
    print(f"  Входной файл:  {vocal_file} ({input_size:,} байт)")
    print(f"  Выходной файл: {result} ({output_size:,} байт)")
    print(f"\nГотово! Проверьте выходной файл — громкость должна меняться каждые 30 сек.")
    print(f"  non-chorus (0–30, 60–90, 120–...): громкость 40%")
    print(f"  chorus     (30–60, 90–120, ...):   громкость 100%")


async def _get_duration(audio_file: str) -> float:
    """Получить длительность аудиофайла через ffprobe."""
    import asyncio

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        audio_file,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        import json
        info = json.loads(stdout.decode("utf-8"))
        return float(info["format"]["duration"])
    except Exception as exc:
        logger.warning("Не удалось получить длительность через ffprobe: %s", exc)
        # Fallback: предполагаем 3 минуты
        return 180.0


if __name__ == "__main__":
    asyncio.run(main())
