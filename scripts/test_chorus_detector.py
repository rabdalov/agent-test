"""Тест ChorusDetector на трёх вариантах аудиофайла и трёх бэкендах.

Запуск:
    uv run python scripts/test_chorus_detector.py

    # Тест конкретного бэкенда:
    uv run python scripts/test_chorus_detector.py --backend librosa
    uv run python scripts/test_chorus_detector.py --backend msaf
    uv run python scripts/test_chorus_detector.py --backend hybrid

Тестирует определение припевов на:
1. Полный трек: data_exapmles/Иракли - Лондон-Париж.mp3
2. Только вокал: data_exapmles/Иракли - Лондон-Париж_(Vocals).mp3
3. Только инструментал: data_exapmles/Иракли - Лондон-Париж_(Instrumental).mp3

Выводит найденные сегменты и сравнивает результаты между вариантами и бэкендами.
"""

import argparse
import logging
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для импорта app
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_chorus_detector")

BACKENDS = ["msaf", "librosa", "hybrid"]


def format_time(seconds: float) -> str:
    """Форматировать секунды в MM:SS.mmm."""
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"{minutes:02d}:{secs:06.3f}"


def print_segments(label: str, segments: list[tuple[float, float]]) -> None:
    """Вывести найденные сегменты в читаемом формате."""
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    if not segments:
        print("  ⚠️  Припевы не найдены (пустой список)")
        return
    total_duration = sum(end - start for start, end in segments)
    print(f"  Найдено сегментов: {len(segments)}")
    print(f"  Суммарная длительность: {total_duration:.1f} сек ({format_time(total_duration)})")
    print()
    for i, (start, end) in enumerate(segments, 1):
        duration = end - start
        print(f"  [{i}] {format_time(start)} → {format_time(end)}  ({duration:.1f} сек)")


def print_segments_with_info(label: str, segments: list) -> None:
    """Вывести найденные сегменты с расширенной информацией (SegmentInfo)."""
    from app.chorus_detector import SegmentInfo

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    if not segments:
        print("  ⚠️  Сегменты не найдены (пустой список)")
        return

    chorus_segs = [s for s in segments if isinstance(s, SegmentInfo) and s.segment_type == "chorus"]
    non_chorus_segs = [s for s in segments if isinstance(s, SegmentInfo) and s.segment_type != "chorus"]

    total_chorus_dur = sum(s.duration for s in chorus_segs)
    print(f"  Всего сегментов: {len(segments)}  (припевов: {len(chorus_segs)}, не-припевов: {len(non_chorus_segs)})")
    if chorus_segs:
        print(f"  Суммарная длительность припевов: {total_chorus_dur:.1f} сек ({format_time(total_chorus_dur)})")
    print()

    for i, seg in enumerate(segments, 1):
        if not isinstance(seg, SegmentInfo):
            continue
        marker = "🎵" if seg.segment_type == "chorus" else "  "
        print(f"  {marker} [{i}] {format_time(seg.start)} → {format_time(seg.end)}  "
              f"({seg.duration:.1f} сек)  [{seg.segment_type}]  backend={seg.backend}")
        if seg.scores:
            scores_str = "  ".join(
                f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                for k, v in seg.scores.items()
            )
            print(f"         scores: {scores_str}")


def compare_results(
    results: dict[str, list[tuple[float, float]]],
) -> None:
    """Сравнить результаты между тремя вариантами."""
    print(f"\n{'='*60}")
    print("  СРАВНЕНИЕ РЕЗУЛЬТАТОВ")
    print(f"{'='*60}")

    labels = list(results.keys())
    counts = {lbl: len(segs) for lbl, segs in results.items()}
    durations = {
        lbl: sum(end - start for start, end in segs)
        for lbl, segs in results.items()
    }

    print(f"\n  {'Вариант':<30} {'Сегментов':>10} {'Длит. (сек)':>12}")
    print(f"  {'-'*54}")
    for lbl in labels:
        print(f"  {lbl:<30} {counts[lbl]:>10} {durations[lbl]:>12.1f}")

    # Проверяем совпадение сегментов между полным треком и вокалом
    if "Полный трек" in results and "Вокал" in results:
        full = results["Полный трек"]
        vocal = results["Вокал"]
        if full and vocal:
            # Считаем пересечение по временным отрезкам (с допуском 2 сек)
            matches = 0
            for fs, fe in full:
                for vs, ve in vocal:
                    overlap = min(fe, ve) - max(fs, vs)
                    if overlap > 0:
                        matches += 1
                        break
            print(f"\n  Совпадений сегментов (Полный ↔ Вокал): {matches}/{len(full)}")

    if "Полный трек" in results and "Инструментал" in results:
        full = results["Полный трек"]
        instr = results["Инструментал"]
        if full and instr:
            matches = 0
            for fs, fe in full:
                for is_, ie in instr:
                    overlap = min(fe, ie) - max(fs, is_)
                    if overlap > 0:
                        matches += 1
                        break
            print(f"  Совпадений сегментов (Полный ↔ Инструментал): {matches}/{len(full)}")

    # Рекомендация по выбору входного файла
    print(f"\n  РЕКОМЕНДАЦИЯ:")
    print(f"  В пайплайне используется полный трек (track_source) для анализа.")
    print(f"  Полный трек даёт наиболее стабильные результаты для всех бэкендов.")
    if "Полный трек" in results and results["Полный трек"]:
        n = len(results["Полный трек"])
        print(f"  Найдено {n} сегментов припева на полном треке.")


def compare_backends(
    backend_results: dict[str, dict[str, list[tuple[float, float]]]],
    audio_label: str,
) -> None:
    """Сравнить результаты разных бэкендов для одного аудиофайла."""
    print(f"\n{'='*60}")
    print(f"  СРАВНЕНИЕ БЭКЕНДОВ для: {audio_label}")
    print(f"{'='*60}")

    print(f"\n  {'Бэкенд':<15} {'Сегментов':>10} {'Длит. (сек)':>12}")
    print(f"  {'-'*40}")
    for backend, segs in backend_results.items():
        total_dur = sum(e - s for s, e in segs)
        print(f"  {backend:<15} {len(segs):>10} {total_dur:>12.1f}")

    # Показываем сегменты каждого бэкенда
    for backend, segs in backend_results.items():
        if segs:
            print(f"\n  [{backend}]")
            for i, (s, e) in enumerate(segs, 1):
                print(f"    [{i}] {format_time(s)} → {format_time(e)}  ({e-s:.1f} сек)")


def run_single_backend(backend: str, test_files: dict[str, Path]) -> None:
    """Запустить тест для одного бэкенда на всех файлах."""
    from app.chorus_detector import ChorusDetector, SegmentInfo

    print(f"\n{'#'*60}")
    print(f"  БЭКЕНД: {backend.upper()}")
    print(f"{'#'*60}")

    detector = ChorusDetector(backend=backend, min_duration_sec=15.0, max_duration_sec=60.0)
    # Для сравнения используем только chorus-сегменты (tuple)
    results: dict[str, list[tuple[float, float]]] = {}

    for label, path in test_files.items():
        print(f"\n[...] Анализирую: {label} ({path.name}) [backend={backend}]...")
        try:
            segment_infos = detector.detect_with_info(str(path))
            # Выводим расширенную информацию
            print_segments_with_info(f"{label} [{backend}]", segment_infos)
            # Для сравнения сохраняем только chorus-сегменты
            chorus_segs = [
                (s.start, s.end) for s in segment_infos
                if isinstance(s, SegmentInfo) and s.segment_type == "chorus"
            ]
            results[label] = chorus_segs
        except Exception as exc:
            print(f"  [!!] Ошибка при анализе '{label}': {exc}")
            results[label] = []

    compare_results(results)

    print(f"\n{'='*60}")
    print(f"  ИТОГ [{backend}]")
    print(f"{'='*60}")
    if all(len(segs) > 0 for segs in results.values()):
        print(f"  [OK] Все три варианта успешно проанализированы [{backend}]")
    elif any(len(segs) > 0 for segs in results.values()):
        print(f"  [~]  Часть вариантов не дала результатов [{backend}]")
    else:
        print(f"  [!!] Ни один вариант не дал результатов [{backend}]")
    print()


def run_all_backends(test_files: dict[str, Path]) -> None:
    """Запустить тест для всех бэкендов и сравнить результаты."""
    from app.chorus_detector import ChorusDetector, SegmentInfo

    # Собираем результаты по всем бэкендам для каждого файла
    all_results: dict[str, dict[str, list[tuple[float, float]]]] = {
        label: {} for label in test_files
    }

    for backend in BACKENDS:
        print(f"\n{'#'*60}")
        print(f"  БЭКЕНД: {backend.upper()}")
        print(f"{'#'*60}")

        detector = ChorusDetector(backend=backend, min_duration_sec=15.0, max_duration_sec=60.0)

        for label, path in test_files.items():
            print(f"\n[...] Анализирую: {label} ({path.name}) [backend={backend}]...")
            try:
                segment_infos = detector.detect_with_info(str(path))
                # Выводим расширенную информацию
                print_segments_with_info(f"{label} [{backend}]", segment_infos)
                # Для сравнения сохраняем только chorus-сегменты
                chorus_segs = [
                    (s.start, s.end) for s in segment_infos
                    if isinstance(s, SegmentInfo) and s.segment_type == "chorus"
                ]
                all_results[label][backend] = chorus_segs
            except Exception as exc:
                print(f"  [!!] Ошибка при анализе '{label}': {exc}")
                all_results[label][backend] = []

    # Сравниваем бэкенды для каждого файла
    for label in test_files:
        compare_backends(all_results[label], label)

    # Итоговая сводка
    print(f"\n{'='*60}")
    print("  ИТОГОВАЯ СВОДКА")
    print(f"{'='*60}")
    print(f"\n  {'Файл':<30} {'msaf':>8} {'librosa':>10} {'hybrid':>8}")
    print(f"  {'-'*60}")
    for label in test_files:
        counts = {b: len(all_results[label].get(b, [])) for b in BACKENDS}
        print(
            f"  {label:<30} {counts['msaf']:>8} {counts['librosa']:>10} {counts['hybrid']:>8}"
        )
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Тест ChorusDetector")
    parser.add_argument(
        "--backend",
        choices=BACKENDS + ["all"],
        default="all",
        help="Бэкенд для тестирования (по умолчанию: all — тестирует все три)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Включить DEBUG-логирование",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("app.chorus_detector").setLevel(logging.DEBUG)

    data_dir = project_root / "data_exapmles"

    test_files = {
        "Полный трек": data_dir / "Иракли - Лондон-Париж.mp3",
        "Вокал": data_dir / "Иракли - Лондон-Париж_(Vocals).mp3",
        "Инструментал": data_dir / "Иракли - Лондон-Париж_(Instrumental).mp3",
    }

    # Проверяем наличие файлов
    print("\n[*] Проверка тестовых файлов:")
    for label, path in test_files.items():
        exists = path.exists()
        size_mb = path.stat().st_size / 1024 / 1024 if exists else 0
        status = f"[OK] {size_mb:.1f} MB" if exists else "[!!] НЕ НАЙДЕН"
        print(f"  {label:<30} {status}")

    missing = [lbl for lbl, p in test_files.items() if not p.exists()]
    if missing:
        print(f"\n[!!] Отсутствуют файлы: {', '.join(missing)}")
        print("   Убедитесь, что файлы находятся в директории data_exapmles/")
        sys.exit(1)

    print(f"\n[*] Запуск анализа структуры трека...")
    print(f"   Бэкенд: {args.backend}")
    print("   (это может занять несколько минут для каждого файла)\n")

    if args.backend == "all":
        run_all_backends(test_files)
    else:
        run_single_backend(args.backend, test_files)


if __name__ == "__main__":
    main()
