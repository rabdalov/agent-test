"""Скрипт для ручной генерации визуализации timeline трека.

Читает ``state.json`` из папки трека, извлекает пути к файлам-артефактам
и вызывает :class:`app.track_visualizer.TrackVisualizer` для генерации PNG.

Использование::

    uv run python scripts/visualize_track.py <path_to_track_dir>

Пример::

    uv run python scripts/visualize_track.py "tracks/Godsmack - Nothing Else Matters"

Если ``state.json`` не найден, скрипт пытается найти артефакты по стандартным
именам файлов в папке трека.

Выходной PNG сохраняется в папку трека как ``<track_stem>_timeline.png``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _find_file_by_suffix(track_dir: Path, suffixes: list[str]) -> Path | None:
    """Найти файл в папке трека по суффиксу имени.

    Parameters
    ----------
    track_dir:
        Папка трека.
    suffixes:
        Список суффиксов для поиска (например, ``["_volume_segments.json"]``).

    Returns
    -------
    Path | None
        Путь к найденному файлу или ``None``.
    """
    for suffix in suffixes:
        for f in track_dir.iterdir():
            if f.name.endswith(suffix):
                return f
    return None


def _resolve_path(track_dir: Path, value: str | None) -> Path | None:
    """Разрешить путь к файлу артефакта.

    Если ``value`` — абсолютный путь и файл существует, возвращает его.
    Иначе пробует найти файл относительно ``track_dir``.

    Parameters
    ----------
    track_dir:
        Папка трека.
    value:
        Значение поля из ``state.json`` (путь к файлу).

    Returns
    -------
    Path | None
        Разрешённый путь или ``None``.
    """
    if not value:
        return None
    p = Path(value)
    if p.exists():
        return p
    # Пробуем относительно папки трека
    rel = track_dir / p.name
    if rel.exists():
        return rel
    return None


def main() -> None:
    """Точка входа скрипта."""
    if len(sys.argv) < 2:
        print(
            "Использование: uv run python scripts/visualize_track.py <path_to_track_dir>",
            file=sys.stderr,
        )
        sys.exit(1)

    track_dir = Path(sys.argv[1])
    if not track_dir.exists():
        print(f"Ошибка: папка трека не найдена: {track_dir}", file=sys.stderr)
        sys.exit(1)
    if not track_dir.is_dir():
        print(f"Ошибка: указанный путь не является папкой: {track_dir}", file=sys.stderr)
        sys.exit(1)

    # --- Читаем state.json ---
    state_file = track_dir / "state.json"
    state: dict = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            print(f"Загружен state.json: {state_file}")
        except Exception as exc:
            print(f"Предупреждение: не удалось прочитать state.json: {exc}", file=sys.stderr)
    else:
        print(f"Предупреждение: state.json не найден в {track_dir}", file=sys.stderr)

    # --- Извлекаем пути к артефактам ---
    volume_segments_file = _resolve_path(
        track_dir, state.get("volume_segments_file")
    ) or _find_file_by_suffix(track_dir, ["_volume_segments.json"])

    transcribe_json_file = _resolve_path(
        track_dir, state.get("transcribe_json_file")
    ) or _find_file_by_suffix(track_dir, ["_transcribe.json", "_transcription.json"])

    corrected_transcribe_json_file = _resolve_path(
        track_dir, state.get("corrected_transcribe_json_file")
    ) or _find_file_by_suffix(track_dir, ["_corrected_transcribe.json", "_corrected.json"])

    aligned_lyrics_file = _resolve_path(
        track_dir, state.get("aligned_lyrics_file")
    ) or _find_file_by_suffix(track_dir, ["_aligned.json", "_aligned_lyrics.json"])

    source_lyrics_file = _resolve_path(
        track_dir, state.get("source_lyrics_file")
    ) or _find_file_by_suffix(track_dir, [".lrc", "_lyrics.txt"])

    # --- Определяем название трека ---
    track_title = state.get("track_stem") or track_dir.name

    # --- Определяем путь к выходному PNG ---
    output_path = track_dir / f"{track_dir.name}_timeline.png"

    # --- Выводим информацию о найденных файлах ---
    print(f"\nТрек: {track_title}")
    print(f"Папка: {track_dir}")
    print(f"Выходной файл: {output_path}")
    print("\nАртефакты:")
    print(f"  volume_segments_file:           {volume_segments_file or '—'}")
    print(f"  transcribe_json_file:           {transcribe_json_file or '—'}")
    print(f"  corrected_transcribe_json_file: {corrected_transcribe_json_file or '—'}")
    print(f"  aligned_lyrics_file:            {aligned_lyrics_file or '—'}")
    print(f"  source_lyrics_file:             {source_lyrics_file or '—'}")

    # Проверяем, что хотя бы один файл найден
    found_files = [
        f for f in [
            volume_segments_file,
            transcribe_json_file,
            corrected_transcribe_json_file,
            aligned_lyrics_file,
        ]
        if f is not None
    ]
    if not found_files:
        print(
            "\nОшибка: не найдено ни одного файла-артефакта. "
            "Убедитесь, что пайплайн был выполнен хотя бы частично.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Импортируем TrackVisualizer ---
    # Добавляем корень проекта в sys.path
    project_root = Path(__file__).resolve().parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from app.track_visualizer import TrackVisualizer
    except ImportError as exc:
        print(f"Ошибка импорта TrackVisualizer: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Генерируем визуализацию ---
    print("\nГенерация визуализации...")
    visualizer = TrackVisualizer(width_px=3840, height_px=1080, dpi=150)

    try:
        visualizer.generate(
            output_path=output_path,
            transcribe_json_file=transcribe_json_file,
            corrected_transcribe_json_file=corrected_transcribe_json_file,
            aligned_lyrics_file=aligned_lyrics_file,
            source_lyrics_file=source_lyrics_file,
            volume_segments_file=volume_segments_file,
            track_title=track_title,
        )
        print(f"\n✅ Визуализация сохранена: {output_path}")
    except ValueError as exc:
        print(f"\nОшибка: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\nНепредвиденная ошибка: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
