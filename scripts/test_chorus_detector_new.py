"""Тестовый скрипт для нового двухфайлового ChorusDetector (итерация 36).

Проверяет:
1. Двухфайловый режим: detector.detect(full_track, vocal_file=vocal_path)
2. Однофайловый режим: detector.detect(full_track)
3. Построение volume_segments и сохранение в JSON
4. Загрузку volume_segments из JSON и проверку полей
5. Сводную таблицу по типам сегментов

Пути к файлам прописаны хардкодом для конкретного трека.
Запуск:
uv run -m scripts.test_chorus_detector_new
"""

from __future__ import annotations

import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для импорта app.*
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.chorus_detector import (
    ChorusDetector,
    SegmentInfo,
    VolumeSegment,
    build_volume_segments,
    load_volume_segments,
    save_volume_segments,
)

# ---------------------------------------------------------------------------
# Пути к тестовым файлам
# ---------------------------------------------------------------------------

data_dir = (
    Path("\\\\192.168.0.200")
    / "docker"
    / "karaoke"
    / "music"
    / "Godsmack - Nothing Else Matters"
)

full_track_path = data_dir / "Godsmack - Nothing Else Matters.mp3"
vocal_path = data_dir / "Godsmack - Nothing Else Matters_(Vocals).mp3"
volume_segments_file = data_dir / "Godsmack - Nothing Else Matters_volume_segments.json"

# Параметры детектора
CHORUS_VOLUME = 0.3
DEFAULT_VOLUME = 0.4


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def print_separator(title: str = "") -> None:
    width = 80
    if title:
        pad = (width - len(title) - 2) // 2
        print("=" * pad + f" {title} " + "=" * (width - pad - len(title) - 2))
    else:
        print("=" * width)


def print_segment_table(segments: list[SegmentInfo]) -> None:
    """Вывести таблицу сегментов с признаками."""
    header = f"{'#':>3}  {'Start':>8}  {'End':>8}  {'Dur':>6}  {'Type':<14}  {'VocEnergy':>10}  {'SimScore':>9}  {'HpssScore':>10}  {'TempoScore':>11}"
    print(header)
    print("-" * len(header))
    for i, seg in enumerate(segments):
        scores = seg.scores
        vocal_energy = scores.get("vocal_energy", 0.0)
        sim_score = scores.get("sim_score", 0.0)
        hpss_score = scores.get("hpss_score", 0.0)
        tempo_score = scores.get("tempo_score", 0.0)
        print(
            f"{i:>3}  {seg.start:>8.1f}  {seg.end:>8.1f}  {seg.duration:>6.1f}  "
            f"{seg.segment_type:<14}  {vocal_energy:>10.4f}  {sim_score:>9.4f}  "
            f"{hpss_score:>10.4f}  {tempo_score:>11.4f}"
        )


def print_summary_table(segments: list[SegmentInfo]) -> None:
    """Вывести сводную таблицу по типам сегментов."""
    from collections import defaultdict

    type_count: dict[str, int] = defaultdict(int)
    type_duration: dict[str, float] = defaultdict(float)

    for seg in segments:
        type_count[seg.segment_type] += 1
        type_duration[seg.segment_type] += seg.duration

    all_types = sorted(type_count.keys())
    print(f"{'Type':<14}  {'Count':>6}  {'Total Duration':>15}")
    print("-" * 40)
    for t in all_types:
        print(
            f"{t:<14}  {type_count[t]:>6}  {type_duration[t]:>14.1f}s"
        )
    print("-" * 40)
    total_dur = sum(type_duration.values())
    print(f"{'TOTAL':<14}  {sum(type_count.values()):>6}  {total_dur:>14.1f}s")


def check_volume_segments_fields(segments: list[VolumeSegment]) -> bool:
    """Проверить, что каждый сегмент содержит все обязательные поля."""
    required_fields = ["start", "end", "volume", "segment_type", "backend", "scores"]
    all_ok = True
    for i, seg in enumerate(segments):
        missing = []
        if seg.start is None:
            missing.append("start")
        if seg.end is None:
            missing.append("end")
        if seg.volume is None:
            missing.append("volume")
        if seg.segment_type is None:
            missing.append("segment_type")
        if seg.backend is None:
            missing.append("backend")
        if seg.scores is None:
            missing.append("scores")
        if missing:
            print(f"  ❌ Segment {i}: missing fields: {missing}")
            all_ok = False
    return all_ok


# ---------------------------------------------------------------------------
# Основной тест
# ---------------------------------------------------------------------------


def run_dual_file_test() -> list[SegmentInfo]:
    """Тест 1: Двухфайловый режим."""
    print_separator("ТЕСТ 1: Двухфайловый режим (dual_file)")

    if not full_track_path.exists():
        print(f"❌ Файл трека не найден: {full_track_path}")
        return []
    if not vocal_path.exists():
        print(f"❌ Файл вокала не найден: {vocal_path}")
        return []

    print(f"  Трек:  {full_track_path}")
    print(f"  Вокал: {vocal_path}")
    print()

    detector = ChorusDetector(
        min_duration_sec=5.0,
        vocal_silence_threshold=0.05,
        boundary_merge_tolerance_sec=2.0,
    )

    print("  Запуск detector.detect(full_track, vocal_file=vocal_path)...")
    segments = detector.detect(str(full_track_path), vocal_file=str(vocal_path))

    if not segments:
        print("  ⚠️  Детектор не вернул сегментов")
        return []

    print(f"\n  Найдено сегментов: {len(segments)}")
    print()
    print_segment_table(segments)
    print()
    print("  Сводная таблица по типам:")
    print_summary_table(segments)

    return segments


def run_single_file_test() -> list[SegmentInfo]:
    """Тест 2: Однофайловый режим."""
    print_separator("ТЕСТ 2: Однофайловый режим (single_file)")

    if not full_track_path.exists():
        print(f"❌ Файл трека не найден: {full_track_path}")
        return []

    print(f"  Трек: {full_track_path}")
    print()

    detector = ChorusDetector(
        min_duration_sec=5.0,
        vocal_silence_threshold=0.05,
        boundary_merge_tolerance_sec=2.0,
    )

    print("  Запуск detector.detect(full_track) без vocal_file...")
    segments = detector.detect(str(full_track_path))

    if not segments:
        print("  ⚠️  Детектор не вернул сегментов")
        return []

    print(f"\n  Найдено сегментов: {len(segments)}")
    print()
    print_segment_table(segments)
    print()
    print("  Сводная таблица по типам:")
    print_summary_table(segments)

    return segments


def run_volume_segments_test(segment_infos: list[SegmentInfo]) -> None:
    """Тест 3: Построение, сохранение и загрузка volume_segments."""
    print_separator("ТЕСТ 3: Volume segments (build → save → load)")

    if not segment_infos:
        print("  ⚠️  Нет сегментов для построения volume_segments")
        return

    # Определяем длительность трека из последнего сегмента
    audio_duration = max(seg.end for seg in segment_infos)

    # Строим volume_segments
    chorus_segs = [(s.start, s.end) for s in segment_infos if s.segment_type == "chorus"]
    volume_segments = build_volume_segments(
        chorus_segments=chorus_segs,
        audio_duration=audio_duration,
        chorus_volume=CHORUS_VOLUME,
        default_volume=DEFAULT_VOLUME,
        segment_infos=segment_infos,
    )

    print(f"  Построено volume_segments: {len(volume_segments)}")

    # Сохраняем в файл
    save_volume_segments(volume_segments, volume_segments_file)
    print(f"  Сохранено в: {volume_segments_file}")

    # Загружаем обратно
    loaded_segments = load_volume_segments(volume_segments_file)
    print(f"  Загружено из файла: {len(loaded_segments)} сегментов")

    # Проверяем поля
    print("\n  Проверка полей каждого сегмента...")
    all_ok = check_volume_segments_fields(loaded_segments)
    if all_ok:
        print("  ✅ Все сегменты содержат обязательные поля")
    else:
        print("  ❌ Некоторые сегменты не содержат обязательных полей")

    # Выводим первые 5 сегментов для проверки
    print("\n  Первые 5 сегментов из файла:")
    for i, seg in enumerate(loaded_segments[:5]):
        print(
            f"    [{i}] {seg.start:.1f}–{seg.end:.1f}s  "
            f"type={seg.segment_type}  vol={seg.volume:.2f}  "
            f"backend={seg.backend}  scores={seg.scores}"
        )


def run_comparison_test(
    dual_segments: list[SegmentInfo],
    single_segments: list[SegmentInfo],
) -> None:
    """Тест 4: Сравнение двухфайлового и однофайлового режимов."""
    print_separator("ТЕСТ 4: Сравнение dual_file vs single_file")

    if not dual_segments and not single_segments:
        print("  ⚠️  Нет данных для сравнения")
        return

    print(f"  dual_file:   {len(dual_segments)} сегментов")
    print(f"  single_file: {len(single_segments)} сегментов")

    if dual_segments:
        dual_chorus = sum(1 for s in dual_segments if s.segment_type == "chorus")
        dual_instrumental = sum(1 for s in dual_segments if s.segment_type == "instrumental")
        print(f"\n  dual_file:   chorus={dual_chorus}, instrumental={dual_instrumental}")

    if single_segments:
        single_chorus = sum(1 for s in single_segments if s.segment_type == "chorus")
        single_instrumental = sum(1 for s in single_segments if s.segment_type == "instrumental")
        print(f"  single_file: chorus={single_chorus}, instrumental={single_instrumental}")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    print_separator()
    print("  ChorusDetector — тест итерации 36 (двухфайловый подход)")
    print_separator()
    print()

    # Тест 1: Двухфайловый режим
    dual_segments = run_dual_file_test()
    print()

    # Тест 2: Однофайловый режим
    single_segments = run_single_file_test()
    print()

    # Тест 3: Volume segments
    if dual_segments:
        run_volume_segments_test(dual_segments)
        print()

    # Тест 4: Сравнение
    run_comparison_test(dual_segments, single_segments)
    print()

    print_separator()
    print("  Тестирование завершено")
    print_separator()


if __name__ == "__main__":
    main()
