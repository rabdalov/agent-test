"""TrackVisualizer — графическая визуализация сегментирования трека.

Модуль является **автономным**: не зависит от состояния пайплайна,
не импортирует другие модули ``app/``, работает только с переданными
путями к файлам-артефактам.

Использование::

    from app.track_visualizer import TrackVisualizer
    from pathlib import Path

    visualizer = TrackVisualizer()
    visualizer.generate(
        output_path=Path("track_timeline.png"),
        volume_segments_file=Path("track/volume_segments.json"),
        aligned_lyrics_file=Path("track/aligned_lyrics.json"),
        track_title="Artist - Song",
    )
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Цветовая схема сегментов
# ---------------------------------------------------------------------------

_SEGMENT_COLORS: dict[str, str] = {
    "chorus": "#FF6B6B",        # красный — припев
    "verse": "#4ECDC4",         # бирюзовый — куплет
    "bridge": "#45B7D1",        # голубой — бридж
    "intro": "#96CEB4",         # зелёный — интро
    "outro": "#FFEAA7",         # жёлтый — аутро
    "instrumental": "#DDA0DD",  # сиреневый — инструментал
    "unknown": "#CCCCCC",       # серый — неизвестный тип
}

# Цвета для слоёв транскрипции и выровненного текста
_TRANSCRIPTION_COLOR = "#A8D8EA"    # голубой
_CORRECTED_COLOR = "#AA96DA"        # фиолетовый
_ALIGNED_COLOR = "#A8E6CF"          # зелёный
_K_FONT = 1.0 #коэффициент моноширины


# Цвета метрик (для сегментов)
_METRIC_COLORS: dict[str, str] = {
    "vocal_energy": "#FF6B6B",
    "sim_score": "#4ECDC4",
    "hpss_score": "#45B7D1",
}

# Цвета для детальных метрик (более светлые/прозрачные)
_DETAILED_METRIC_COLORS: dict[str, str] = {
    "vocal_energy": "#FFB3B3",  # светло-красный
    "chroma_variance": "#B3E6E6",  # светло-бирюзовый
    "hpss_score": "#A3D8F0",  # светло-голубой
}

# LRC timestamp pattern: [MM:SS.xx] or [MM:SS.xxx]
_LRC_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{2})\.(\d{2,3})\]")


def _wrap_text(text: str, chars_per_line: int) -> str:
    """Разбить текст на строки по словам с заданной шириной.

    Parameters
    ----------
    text:
        Исходный текст.
    chars_per_line:
        Максимальное количество символов в строке.

    Returns
    -------
    str
        Текст с переносами строк ``\\n``.
    """
    words = text.split()
    if not words:
        return text

    lines: list[str] = []
    current_line: list[str] = []
    current_len = 0

    for word in words:
        word_len = len(word)
        if current_line and current_len + 1 + word_len > chars_per_line:
            lines.append(" ".join(current_line))
            current_line = [word]
            current_len = word_len
        else:
            if current_line:
                current_len += 1 + word_len
            else:
                current_len = word_len
            current_line.append(word)

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TrackVisualizer
# ---------------------------------------------------------------------------


class TrackVisualizer:
    """Генерирует графический timeline сегментирования трека.

    Принимает пути к файлам-артефактам пайплайна и формирует PNG-изображение
    с временной шкалой, метриками сегментов и потоками данных.

    Модуль автономен: не импортирует ничего из ``app/``.

    Parameters
    ----------
    width_px:
        Ширина изображения в пикселях (по умолчанию 3840).
    height_px:
        Высота изображения в пикселях (по умолчанию 1080).
    dpi:
        Разрешение изображения (по умолчанию 150).
    """

    def __init__(
        self,
        width_px: int = 3840,
        height_px: int = 1080,
        dpi: int = 150,
    ) -> None:
        self._width_px = width_px
        self._height_px = height_px
        self._dpi = dpi

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        output_path: Path,
        transcribe_json_file: Path | None = None,
        corrected_transcribe_json_file: Path | None = None,
        aligned_lyrics_file: Path | None = None,
        source_lyrics_file: Path | None = None,
        volume_segments_file: Path | None = None,
        detailed_metrics_file: Path | None = None,
        track_title: str = "",
    ) -> None:
        """Сгенерировать PNG-файл с визуализацией timeline трека.

        Parameters
        ----------
        output_path:
            Путь к выходному PNG-файлу.
        transcribe_json_file:
            JSON с результатом транскрибации (шаг TRANSCRIBE).
        corrected_transcribe_json_file:
            Скорректированный JSON транскрипции (шаг CORRECT_TRANSCRIPT).
        aligned_lyrics_file:
            JSON с выровненным текстом и таймкодами (шаг ALIGN).
        source_lyrics_file:
            TXT или LRC с текстом песни (шаг GET_LYRICS).
        volume_segments_file:
            JSON с разметкой сегментов (шаг DETECT_CHORUS).
        detailed_metrics_file:
            JSON с детальными метриками (1-секундные точки, шаг DETECT_CHORUS).
        track_title:
            Название трека для заголовка визуализации.

        Raises
        ------
        ValueError
            Если ни один входной файл не передан или все отсутствуют на диске.
        """
        import matplotlib  # type: ignore[import]
        matplotlib.use("Agg")  # non-interactive backend
        import matplotlib.pyplot as plt  # type: ignore[import]
        import matplotlib.patches as mpatches  # type: ignore[import]

        # --- Загрузка данных ---
        volume_segments: list[dict] = []
        if volume_segments_file and volume_segments_file.exists():
            volume_segments = self._load_volume_segments(volume_segments_file)
            logger.debug(
                "TrackVisualizer: loaded %d volume segments from '%s'",
                len(volume_segments),
                volume_segments_file,
            )

        detailed_metrics: list[dict] = []
        if detailed_metrics_file and detailed_metrics_file.exists():
            detailed_metrics = self._load_detailed_metrics(detailed_metrics_file)
            logger.debug(
                "TrackVisualizer: loaded %d detailed metrics from '%s'",
                len(detailed_metrics),
                detailed_metrics_file,
            )

        transcription_segments: list[dict] = []
        transcription_words: list[dict] = []
        if transcribe_json_file and transcribe_json_file.exists():
            transcription_segments, transcription_words = self._load_transcription_segments(
                transcribe_json_file
            )
            logger.debug(
                "TrackVisualizer: loaded %d transcription segments from '%s'",
                len(transcription_segments),
                transcribe_json_file,
            )

        corrected_segments: list[dict] = []
        corrected_words: list[dict] = []
        if corrected_transcribe_json_file and corrected_transcribe_json_file.exists():
            corrected_segments, corrected_words = self._load_transcription_segments(
                corrected_transcribe_json_file
            )
            logger.debug(
                "TrackVisualizer: loaded %d corrected transcription segments from '%s'",
                len(corrected_segments),
                corrected_transcribe_json_file,
            )

        aligned_segments: list[dict] = []
        aligned_words: list[dict] = []
        if aligned_lyrics_file and aligned_lyrics_file.exists():
            aligned_segments, aligned_words = self._load_aligned_lyrics(aligned_lyrics_file)
            logger.debug(
                "TrackVisualizer: loaded %d aligned segments from '%s'",
                len(aligned_segments),
                aligned_lyrics_file,
            )

        source_lyrics_lines: list[str] = []
        if source_lyrics_file and source_lyrics_file.exists():
            source_lyrics_lines = self._load_source_lyrics(source_lyrics_file)
            logger.debug(
                "TrackVisualizer: loaded %d source lyrics lines from '%s'",
                len(source_lyrics_lines),
                source_lyrics_file,
            )

        # Проверяем, что хотя бы что-то загружено
        has_data = any([
            volume_segments,
            transcription_segments,
            corrected_segments,
            aligned_segments,
        ])
        if not has_data:
            raise ValueError(
                "TrackVisualizer.generate: ни один входной файл не передан "
                "или все файлы отсутствуют на диске"
            )

        # --- Вычисление длительности ---
        duration = self._compute_duration(
            volume_segments=volume_segments,
            transcription_segments=transcription_segments,
            aligned_segments=aligned_segments,
        )
        if duration <= 0:
            duration = 300.0  # fallback: 5 минут
            logger.warning(
                "TrackVisualizer: could not determine track duration, using fallback %.1fs",
                duration,
            )

        # --- Определяем активные слои ---
        has_segments = bool(volume_segments)
        has_transcription = bool(transcription_segments)
        has_corrected = bool(corrected_segments)
        has_aligned = bool(aligned_segments)
        has_metrics = has_segments and any(
            seg.get("scores") for seg in volume_segments
        )

        # Считаем количество активных слоёв для компоновки
        active_layers = sum([
            has_segments,
            has_transcription,
            has_corrected,
            has_aligned,
            has_metrics,
        ])
        if active_layers == 0:
            active_layers = 1

        # --- Создание Figure ---
        fig_width = self._width_px / self._dpi
        fig_height = self._height_px / self._dpi
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))

        # Настройка осей
        ax.set_xlim(0, duration)
        ax.set_ylim(0, 1)
        ax.set_xlabel("Время (секунды)", fontsize=10)
        ax.set_facecolor("#1a1a2e")
        fig.patch.set_facecolor("#16213e")

        # Форматирование оси X: метки каждые 10 секунд в формате MM:SS
        tick_interval = 10.0

        xticks = np.arange(0, duration + tick_interval, tick_interval)
        ax.set_xticks(xticks)
        ax.set_xticklabels(
            [self._format_mmss(t) for t in xticks],
            fontsize=8,
            color="#cccccc",
        )
        ax.tick_params(axis="x", colors="#cccccc")
        ax.tick_params(axis="y", left=False, labelleft=False)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color("#444444")

        # Вертикальные линии сетки
        for xt in xticks:
            ax.axvline(x=xt, color="#333355", linewidth=0.5, zorder=0)

        # --- Компоновка слоёв (Y-позиции) ---
        # Распределяем пространство снизу вверх:
        # [0.0 - 0.35] метрики (если есть)
        # [0.35 - 0.50] выровненный текст (если есть)
        # [0.50 - 0.65] скорр. транскрипция (если есть)
        # [0.65 - 0.80] транскрипция (если есть)
        # [0.80 - 0.95] сегменты (если есть)
        # [0.95 - 1.00] заголовок

        layer_height = 0.13
        layer_gap = 0.02
        current_y = 0.02  # начинаем снизу

        # Слой метрик (нижний)
        metrics_y_bottom = current_y
        metrics_height = 0.30 if has_metrics else 0.0
        if has_metrics:
            current_y += metrics_height + layer_gap

        # Слой выровненного текста
        aligned_y_bottom = current_y
        aligned_height = layer_height if has_aligned else 0.0
        if has_aligned:
            current_y += aligned_height + layer_gap

        # Слой скорр. транскрипции
        corrected_y_bottom = current_y
        corrected_height = layer_height if has_corrected else 0.0
        if has_corrected:
            current_y += corrected_height + layer_gap

        # Слой транскрипции
        transcription_y_bottom = current_y
        transcription_height = layer_height if has_transcription else 0.0
        if has_transcription:
            current_y += transcription_height + layer_gap

        # Слой сегментов (верхний)
        segments_y_bottom = current_y
        segments_height = layer_height + 0.02 if has_segments else 0.0

        # --- Отрисовка слоёв ---
        if has_metrics:
            self._draw_metrics_layer(
                ax=ax,
                segments=volume_segments,
                duration=duration,
                y_bottom=metrics_y_bottom,
                height=metrics_height,
                detailed_metrics=detailed_metrics if detailed_metrics else None,
            )

        if has_aligned:
            self._draw_aligned_layer(
                ax=ax,
                segments=aligned_segments,
                duration=duration,
                y_bottom=aligned_y_bottom,
                height=aligned_height,
            )

        if has_corrected:
            self._draw_transcription_layer(
                ax=ax,
                segments=corrected_segments,
                duration=duration,
                y_bottom=corrected_y_bottom,
                height=corrected_height,
                label="Скорр. транскрипция",
                color=_CORRECTED_COLOR,
            )

        if has_transcription:
            self._draw_transcription_layer(
                ax=ax,
                segments=transcription_segments,
                duration=duration,
                y_bottom=transcription_y_bottom,
                height=transcription_height,
                label="Транскрипция",
                color=_TRANSCRIPTION_COLOR,
            )

        if has_segments:
            self._draw_segments_layer(
                ax=ax,
                segments=volume_segments,
                duration=duration,
                y_bottom=segments_y_bottom,
                height=segments_height,
            )

        # --- Подписи слоёв (слева) ---
        label_x = -duration * 0.01
        if has_metrics:
            ax.text(
                label_x,
                metrics_y_bottom + metrics_height / 2,
                "Метрики",
                ha="right",
                va="center",
                fontsize=7,
                color="#aaaaaa",
                clip_on=False,
            )
        if has_aligned:
            ax.text(
                label_x,
                aligned_y_bottom + aligned_height / 2,
                "Выровн. текст",
                ha="right",
                va="center",
                fontsize=7,
                color="#aaaaaa",
                clip_on=False,
            )
        if has_corrected:
            ax.text(
                label_x,
                corrected_y_bottom + corrected_height / 2,
                "Скорр. транскр.",
                ha="right",
                va="center",
                fontsize=7,
                color="#aaaaaa",
                clip_on=False,
            )
        if has_transcription:
            ax.text(
                label_x,
                transcription_y_bottom + transcription_height / 2,
                "Транскрипция",
                ha="right",
                va="center",
                fontsize=7,
                color="#aaaaaa",
                clip_on=False,
            )
        if has_segments:
            ax.text(
                label_x,
                segments_y_bottom + segments_height / 2,
                "Сегменты",
                ha="right",
                va="center",
                fontsize=7,
                color="#aaaaaa",
                clip_on=False,
            )

        # --- Заголовок ---
        title_text = track_title or "Track Timeline"
        duration_str = self._format_mmss(duration)
        ax.set_title(
            f"{title_text}  [{duration_str}]",
            fontsize=13,
            color="#ffffff",
            pad=12,
        )

        # --- Легенда сегментов ---
        if has_segments:
            legend_patches = []
            seen_types: set[str] = set()
            for seg in volume_segments:
                seg_type = seg.get("segment_type") or "unknown"
                if seg_type not in seen_types:
                    seen_types.add(seg_type)
                    color = _SEGMENT_COLORS.get(seg_type, _SEGMENT_COLORS["unknown"])
                    legend_patches.append(
                        mpatches.Patch(color=color, label=seg_type)
                    )
            if has_metrics:
                for metric_name, metric_color in _METRIC_COLORS.items():
                    import matplotlib.lines as mlines  # type: ignore[import]
                    legend_patches.append(
                        mlines.Line2D(
                            [], [],
                            color=metric_color,
                            linewidth=1.5,
                            label=metric_name,
                        )
                    )
            if legend_patches:
                ax.legend(
                    handles=legend_patches,
                    loc="upper right",
                    fontsize=7,
                    framealpha=0.3,
                    facecolor="#222244",
                    edgecolor="#444466",
                    labelcolor="#cccccc",
                    ncol=min(len(legend_patches), 7),
                )

        # --- Сохранение ---
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            str(output_path),
            dpi=self._dpi,
            bbox_inches="tight",
            facecolor=fig.get_facecolor(),
        )
        plt.close(fig)

        logger.info(
            "TrackVisualizer: saved PNG to '%s' (duration=%.1fs, layers: segments=%s, "
            "transcription=%s, corrected=%s, aligned=%s, metrics=%s)",
            output_path,
            duration,
            has_segments,
            has_transcription,
            has_corrected,
            has_aligned,
            has_metrics,
        )

    # ------------------------------------------------------------------
    # Private: data loaders
    # ------------------------------------------------------------------

    def _load_volume_segments(self, path: Path) -> list[dict]:
        """Загрузить сегменты из volume_segments_file.

        Now scores is always list, format unified.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                logger.warning(
                    "TrackVisualizer._load_volume_segments: expected list, got %s in '%s'",
                    type(data).__name__,
                    path,
                )
                return []
            return data
        except Exception as exc:
            logger.warning(
                "TrackVisualizer._load_volume_segments: failed to load '%s': %s",
                path,
                exc,
            )
            return []

    def _load_detailed_metrics(self, path: Path) -> list[dict]:
        """Загрузить детальные метрики из metrics_file.

        Формат: плоский массив объектов с полями time, vocal_energy, chroma_variance, hpss_score.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                logger.warning(
                    "TrackVisualizer._load_detailed_metrics: expected list, got %s in '%s'",
                    type(data).__name__,
                    path,
                )
                return []
            return data
        except Exception as exc:
            logger.warning(
                "TrackVisualizer._load_detailed_metrics: failed to load '%s': %s",
                path,
                exc,
            )
            return []

    def _load_transcription_segments(
        self, path: Path
    ) -> tuple[list[dict], list[dict]]:
        """Загрузить segments и words из Whisper/speeches.ai JSON.

        Поддерживает оба формата полей времени:
        - ``start`` / ``end`` (Whisper verbose_json)
        - ``start_time`` / ``end_time`` (speeches.ai)

        Parameters
        ----------
        path:
            Путь к JSON-файлу транскрипции.

        Returns
        -------
        tuple[list[dict], list[dict]]
            Кортеж ``(segments, words)``, где каждый элемент — словарь
            с нормализованными полями ``text``, ``start``, ``end``.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "TrackVisualizer._load_transcription_segments: failed to load '%s': %s",
                path,
                exc,
            )
            return [], []

        def _normalize_time_fields(item: dict) -> dict:
            """Нормализовать поля времени к start/end."""
            start = float(
                item.get("start", item.get("start_time", 0.0))
            )
            end = float(
                item.get("end", item.get("end_time", 0.0))
            )
            return {
                "text": item.get("text", "").strip(),
                "start": start,
                "end": end,
            }

        # Загружаем segments
        raw_segments = raw.get("segments", [])
        segments = [_normalize_time_fields(s) for s in raw_segments if isinstance(s, dict)]

        # Загружаем words (top-level или из segments)
        raw_words = raw.get("words", [])
        if raw_words and isinstance(raw_words, list):
            words = [_normalize_time_fields(w) for w in raw_words if isinstance(w, dict)]
        else:
            # Извлекаем из segments
            words = []
            for seg in raw_segments:
                if not isinstance(seg, dict):
                    continue
                for w in seg.get("words", []):
                    if isinstance(w, dict):
                        words.append(_normalize_time_fields(w))

        return segments, words

    def _load_aligned_lyrics(
        self, path: Path
    ) -> tuple[list[dict], list[dict]]:
        """Загрузить segments и words из aligned_lyrics JSON.

        Формат файла (AlignedLyricsResult):
        ``{"words": [{"word": "...", "start_time": 1.23, "end_time": 1.78}],
           "segments": [{"text": "...", "start_time": 1.23, "end_time": 3.45}]}``

        Parameters
        ----------
        path:
            Путь к JSON-файлу выровненного текста.

        Returns
        -------
        tuple[list[dict], list[dict]]
            Кортеж ``(segments, words)`` с нормализованными полями
            ``text``, ``start``, ``end``.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "TrackVisualizer._load_aligned_lyrics: failed to load '%s': %s",
                path,
                exc,
            )
            return [], []

        def _normalize(item: dict, text_key: str = "text") -> dict:
            start = float(item.get("start_time", item.get("start", 0.0)))
            end = float(item.get("end_time", item.get("end", 0.0)))
            text = item.get(text_key, item.get("word", "")).strip()
            return {"text": text, "start": start, "end": end}

        raw_segments = raw.get("segments", [])
        segments = [_normalize(s) for s in raw_segments if isinstance(s, dict)]

        raw_words = raw.get("words", [])
        words = [_normalize(w, text_key="word") for w in raw_words if isinstance(w, dict)]

        return segments, words

    def _load_source_lyrics(self, path: Path) -> list[str]:
        """Загрузить строки текста из TXT или LRC файла.

        Для LRC-файлов парсит таймкоды и возвращает строки с временными метками
        в формате ``[MM:SS] текст``. Для обычного TXT возвращает строки как есть.

        Parameters
        ----------
        path:
            Путь к файлу с текстом песни.

        Returns
        -------
        list[str]
            Список непустых строк текста.
        """
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(
                "TrackVisualizer._load_source_lyrics: failed to load '%s': %s",
                path,
                exc,
            )
            return []

        lines: list[str] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            # Парсим LRC-таймкод
            m = _LRC_TAG_RE.match(stripped)
            if m:
                minutes = int(m.group(1))
                seconds = int(m.group(2))
                line_text = stripped[m.end():].strip()
                if line_text:
                    lines.append(f"[{minutes:02d}:{seconds:02d}] {line_text}")
            else:
                lines.append(stripped)

        return lines

    # ------------------------------------------------------------------
    # Private: duration computation
    # ------------------------------------------------------------------

    def _compute_duration(
        self,
        volume_segments: list[dict],
        transcription_segments: list[dict],
        aligned_segments: list[dict],
    ) -> float:
        """Определить общую длительность трека из доступных данных.

        Берёт максимальное значение ``end`` из всех переданных сегментов.

        Parameters
        ----------
        volume_segments:
            Список сегментов из volume_segments_file.
        transcription_segments:
            Список сегментов из transcription JSON.
        aligned_segments:
            Список сегментов из aligned_lyrics JSON.

        Returns
        -------
        float
            Длительность трека в секундах. 0.0 если данных нет.
        """
        max_end = 0.0

        for seg in volume_segments:
            end = float(seg.get("end", 0.0))
            if end > max_end:
                max_end = end

        for seg in transcription_segments:
            end = float(seg.get("end", 0.0))
            if end > max_end:
                max_end = end

        for seg in aligned_segments:
            end = float(seg.get("end", 0.0))
            if end > max_end:
                max_end = end

        return max_end

    # ------------------------------------------------------------------
    # Private: layer drawing
    # ------------------------------------------------------------------

    def _draw_segments_layer(
        self,
        ax: Any,
        segments: list[dict],
        duration: float,
        y_bottom: float,
        height: float,
    ) -> None:
        """Нарисовать цветные прямоугольники сегментов с подписями.

        Для каждого сегмента рисует цветной прямоугольник по временному
        диапазону и добавляет текстовую подпись с типом.

        Now scores is always list, format unified.

        Parameters
        ----------
        ax:
            Matplotlib Axes для отрисовки.
        segments:
            Список словарей сегментов с полями ``start``, ``end``,
            ``segment_type``, ``volume``, ``scores``.
        duration:
            Общая длительность трека в секундах.
        y_bottom:
            Нижняя граница слоя в нормализованных координатах [0, 1].
        height:
            Высота слоя в нормализованных координатах.
        """
        import matplotlib.patches as mpatches  # type: ignore[import]

        # Высота полосы подсегментов (нижняя часть слоя)
        subseg_strip_height = height * 0.22
        # Высота основного прямоугольника группы (верхняя часть слоя)
        group_rect_height = height - subseg_strip_height

        for seg in segments:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            seg_type = seg.get("segment_type") or "unknown"
            volume = float(seg.get("volume", 0.0))
            scores = seg.get("scores") or []

            # Now scores is always list
            seg_id = seg.get("id", 0)
            
            # For metrics, compute average from scores list
            if isinstance(scores, list) and scores:
                vocal_energy = float(np.mean([s.get("vocal_energy", 0.0) for s in scores]))
                sim_score = float(np.mean([s.get("sim_score", 0.0) for s in scores]))
                hpss_score = float(np.mean([s.get("hpss_score", 0.0) for s in scores]))
                # Range of IDs: "#1-3"
                ids = [s.get("id", 0) for s in scores]
                if ids:
                    id_range = f"#{min(ids)}-{max(ids)}"
                else:
                    id_range = f"#{seg_id}"
            else:
                # No scores or empty list
                vocal_energy = sim_score = hpss_score = 0.0
                id_range = f"#{seg_id}" if seg_id else ""

            color = _SEGMENT_COLORS.get(seg_type, _SEGMENT_COLORS["unknown"])
            seg_width = end - start

            if seg_width <= 0:
                continue

            # Group rectangle (main part of layer, above subsegment strip)
            group_y_bottom = y_bottom + subseg_strip_height
            rect = mpatches.FancyBboxPatch(
                (start, group_y_bottom),
                seg_width,
                group_rect_height,
                boxstyle="round,pad=0.001",
                facecolor=color,
                edgecolor="#ffffff",
                linewidth=0.5,
                alpha=0.85,
                zorder=2,
            )
            ax.add_patch(rect)

            # Form label text (type and volume only)
            label_lines = [
                seg_type,
                f"vol:{volume:.2f}",
            ]
            label = "\n".join(label_lines)

            # Show label only if segment is wide enough
            min_width_for_label = duration * 0.04
            if seg_width >= min_width_for_label:
                ax.text(
                    start + seg_width / 2,
                    group_y_bottom + group_rect_height / 2,
                    label,
                    ha="center",
                    va="center",
                    fontsize=6,
                    color="#000000",
                    fontweight="bold",
                    zorder=3,
                    clip_on=True,
                    wrap=False,
                )

            # Sequential number of group/segment in top-left corner
            if id_range:
                ax.text(
                    start + duration * 0.003,
                    group_y_bottom + group_rect_height * 0.92,
                    id_range,
                    ha="left",
                    va="top",
                    fontsize=6,
                    color="#111111",
                    fontweight="bold",
                    zorder=4,
                    clip_on=True,
                )

            # Time labels on segment boundaries
            ax.text(
                start + 1,
                group_y_bottom + group_rect_height * 0.05,
                self._format_mmss(start),
                ha="left",
                va="bottom",
                fontsize=5,
                color="#333333",
                zorder=3,
                clip_on=True,
            )

            # --- Subsegment strip (lower part of layer) ---
            # Draw subsegment rectangles from scores with id number in center
            if isinstance(scores, list) and scores:
                seg_duration = end - start
                sub_count = len(scores)
                sub_width = seg_duration / sub_count
                for i, sub_score in enumerate(scores):
                    sub_id = sub_score.get("id", i + 1)
                    sub_start = start + i * sub_width
                    sub_end = sub_start + sub_width

                    # Subsegment rectangle
                    sub_rect = mpatches.Rectangle(
                        (sub_start, y_bottom),
                        sub_width,
                        subseg_strip_height,
                        facecolor=color,
                        edgecolor="#ffffff",
                        linewidth=0.3,
                        alpha=0.55,
                        zorder=2,
                    )
                    ax.add_patch(sub_rect)

                    # Subsegment number in center (only if enough space)
                    min_sub_width_for_label = duration * 0.005
                    if sub_width >= min_sub_width_for_label:
                        ax.text(
                            sub_start + sub_width / 2,
                            y_bottom + subseg_strip_height / 2,
                            str(sub_id),
                            ha="center",
                            va="center",
                            fontsize=4,
                            color="#111111",
                            fontweight="bold",
                            zorder=4,
                            clip_on=True,
                        )

    def _draw_transcription_layer(
        self,
        ax: Any,
        segments: list[dict],
        duration: float,
        y_bottom: float,
        height: float,
        label: str,
        color: str,
    ) -> None:
        """Нарисовать сегменты транскрипции с текстом.

        Для каждого сегмента рисует прямоугольник с заливкой и обрезанным
        текстом сегмента.

        Parameters
        ----------
        ax:
            Matplotlib Axes для отрисовки.
        segments:
            Список словарей сегментов с полями ``text``, ``start``, ``end``.
        duration:
            Общая длительность трека в секундах.
        y_bottom:
            Нижняя граница слоя.
        height:
            Высота слоя.
        label:
            Название слоя (для подписи).
        color:
            Цвет заливки прямоугольников.
        """
        import matplotlib.patches as mpatches  # type: ignore[import]

        for seg in segments:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            text = seg.get("text", "").strip()
            seg_width = end - start

            if seg_width <= 0:
                continue

            # Прямоугольник сегмента
            rect = mpatches.Rectangle(
                (start, y_bottom),
                seg_width,
                height,
                facecolor=color,
                edgecolor="#ffffff",
                linewidth=0.3,
                alpha=0.7,
                zorder=2,
            )
            ax.add_patch(rect)

            # Текст сегмента (многострочный с переносом по словам)
            if text and seg_width >= duration * 0.02:
                # Вычисляем количество символов в строке через реальную ширину сегмента:
                # ширина сегмента в пикселях / примерная ширина символа в пикселях
                # При fontsize=5 и dpi=150 ширина символа ≈ 3.5 px
                seg_width_px = seg_width / duration * self._width_px
                char_width_px = self._dpi * 5 / 72 * _K_FONT
                chars_per_line = max(10, int(seg_width_px / char_width_px))
                display_text = _wrap_text(text, chars_per_line)
                ax.text(
                    start + seg_width / 2,
                    y_bottom + height / 2,
                    display_text,
                    ha="center",
                    va="center",
                    fontsize=5,
                    color="#000000",
                    zorder=3,
                    clip_on=True,
                    multialignment="center",
                )

    def _draw_aligned_layer(
        self,
        ax: Any,
        segments: list[dict],
        duration: float,
        y_bottom: float,
        height: float,
    ) -> None:
        """Нарисовать строки выровненного текста.

        Для каждой строки рисует прямоугольник и текст с таймкодами.

        Parameters
        ----------
        ax:
            Matplotlib Axes для отрисовки.
        segments:
            Список словарей сегментов с полями ``text``, ``start``, ``end``.
        duration:
            Общая длительность трека в секундах.
        y_bottom:
            Нижняя граница слоя.
        height:
            Высота слоя.
        """
        import matplotlib.patches as mpatches  # type: ignore[import]

        for seg in segments:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            text = seg.get("text", "").strip()
            seg_width = end - start

            if seg_width <= 0:
                continue

            # Прямоугольник строки
            rect = mpatches.Rectangle(
                (start, y_bottom),
                seg_width,
                height,
                facecolor=_ALIGNED_COLOR,
                edgecolor="#ffffff",
                linewidth=0.3,
                alpha=0.75,
                zorder=2,
            )
            ax.add_patch(rect)

            # Текст строки (многострочный с переносом по словам)
            if text and seg_width >= duration * 0.005:
                seg_width_px = seg_width / duration * self._width_px
                char_width_px = self._dpi * 5 / 72 * 0.6#_K_FONT
                chars_per_line = max(10, int(seg_width_px / char_width_px))
                display_text = _wrap_text(text, chars_per_line)
                ax.text(
                    start + seg_width / 2,
                    y_bottom + height / 2,
                    display_text,
                    ha="center",
                    va="center",
                    fontsize=5,
                    color="#000000",
                    zorder=3,
                    clip_on=True,
                    multialignment="center",
                )

    def _draw_metrics_layer(
        self,
        ax: Any,
        segments: list[dict],
        duration: float,
        y_bottom: float,
        height: float,
        detailed_metrics: list[dict] | None = None,
    ) -> None:
        """Нарисовать ступенчатые графики метрик сегментов и детальные линии.

        Рисует три ступенчатых графика: ``vocal_energy``, ``sim_score``,
        ``hpss_score``. Каждая метрика — отдельная линия с подписью.
        
        Если переданы detailed_metrics, также рисует плавные линии
        детальных метрик с шагом 1 секунда.

        Now scores is always list, format unified.

        Parameters
        ----------
        ax:
            Matplotlib Axes для отрисовки.
        segments:
            Список словарей сегментов с полями ``start``, ``end``, ``scores``.
        duration:
            Общая длительность трека в секундах.
        y_bottom:
            Нижняя граница слоя.
        height:
            Высота слоя.
        detailed_metrics:
            Опциональный список детальных метрик с полями
            ``time``, ``vocal_energy``, ``chroma_variance``, ``hpss_score``.
        """
        if not segments:
            return

        # Собираем данные для ступенчатых графиков
        # Каждая метрика: список (x_start, x_end, value)
        metrics_data: dict[str, list[tuple[float, float, float]]] = {
            "vocal_energy": [],
            "sim_score": [],
            "hpss_score": [],
        }

        for seg in segments:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
            scores = seg.get("scores") or []

            if end <= start:
                continue

            # Now scores is always list
            if isinstance(scores, list) and scores:
                # Scores is array, draw metrics for each element
                # Split time interval into subintervals for each scores element
                seg_duration = end - start
                sub_duration = seg_duration / len(scores)
                for i, sub_score in enumerate(scores):
                    sub_start = start + i * sub_duration
                    sub_end = sub_start + sub_duration
                    vocal_energy = float(sub_score.get("vocal_energy", 0.0))
                    sim_score = float(sub_score.get("sim_score", 0.0))
                    hpss_score = float(sub_score.get("hpss_score", 0.0))
                    metrics_data["vocal_energy"].append((sub_start, sub_end, vocal_energy))
                    metrics_data["sim_score"].append((sub_start, sub_end, sim_score))
                    metrics_data["hpss_score"].append((sub_start, sub_end, hpss_score))
            else:
                # Empty scores
                metrics_data["vocal_energy"].append((start, end, 0.0))
                metrics_data["sim_score"].append((start, end, 0.0))
                metrics_data["hpss_score"].append((start, end, 0.0))

        # Draw metrics layer background
        import matplotlib.patches as mpatches  # type: ignore[import]
        bg_rect = mpatches.Rectangle(
            (0, y_bottom),
            duration,
            height,
            facecolor="#0d0d1a",
            edgecolor="none",
            alpha=0.5,
            zorder=1,
        )
        ax.add_patch(bg_rect)

        # Horizontal grid lines for metrics
        for grid_val in [0.25, 0.5, 0.75, 1.0]:
            grid_y = y_bottom + grid_val * height
            ax.axhline(
                y=grid_y,
                color="#333355",
                linewidth=0.3,
                zorder=1,
                xmin=0,
                xmax=1,
            )

        # Draw step plots
        for metric_name, data_points in metrics_data.items():
            if not data_points:
                continue

            color = _METRIC_COLORS.get(metric_name, "#ffffff")

            # Build step plot
            x_vals: list[float] = []
            y_vals: list[float] = []

            for start, end, value in data_points:
                # Normalize value to range [y_bottom, y_bottom + height]
                y_norm = y_bottom + value * height * 0.9  # 90% height for margin
                x_vals.extend([start, end])
                y_vals.extend([y_norm, y_norm])

            if x_vals:
                ax.plot(
                    x_vals,
                    y_vals,
                    color=color,
                    linewidth=1.5,
                    alpha=0.85,
                    zorder=3,
                    label=metric_name,
                )

        # Draw detailed metrics lines (1-second resolution)
        if detailed_metrics:
            detailed_metrics_map: dict[str, list[tuple[float, float]]] = {
                "vocal_energy": [],
                "chroma_variance": [],
                "hpss_score": [],
            }
            
            for point in detailed_metrics:
                time_val = float(point.get("time", 0.0))
                detailed_metrics_map["vocal_energy"].append(
                    (time_val, float(point.get("vocal_energy", 0.0)))
                )
                detailed_metrics_map["chroma_variance"].append(
                    (time_val, float(point.get("chroma_variance", 0.0)))
                )
                detailed_metrics_map["hpss_score"].append(
                    (time_val, float(point.get("hpss_score", 0.0)))
                )
            
            # Sort by time
            for metric_name in detailed_metrics_map:
                detailed_metrics_map[metric_name].sort(key=lambda x: x[0])
            
            # Draw detailed lines (thinner, more transparent)
            for metric_name, data_points in detailed_metrics_map.items():
                if not data_points:
                    continue
                
                color = _DETAILED_METRIC_COLORS.get(metric_name, "#cccccc")
                
                x_vals = [p[0] for p in data_points]
                y_vals = [y_bottom + p[1] * height * 0.9 for p in data_points]
                
                if x_vals:
                    ax.plot(
                        x_vals,
                        y_vals,
                        color=color,
                        linewidth=0.8,
                        alpha=0.5,
                        zorder=2,
                        linestyle="-",
                    )

        # Metric labels on the right
        metric_labels = [
            ("vocal_energy", _METRIC_COLORS["vocal_energy"]),
            ("sim_score", _METRIC_COLORS["sim_score"]),
            ("hpss_score", _METRIC_COLORS["hpss_score"]),
        ]
        for i, (metric_name, color) in enumerate(metric_labels):
            y_label = y_bottom + height * (0.85 - i * 0.28)
            ax.text(
                duration * 1.001,
                y_label,
                metric_name,
                ha="left",
                va="center",
                fontsize=6,
                color=color,
                clip_on=False,
            )

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_mmss(seconds: float) -> str:
        """Форматировать секунды в строку MM:SS.

        Parameters
        ----------
        seconds:
            Время в секундах.

        Returns
        -------
        str
            Строка в формате ``MM:SS``.
        """
        total_s = int(seconds)
        m = total_s // 60
        s = total_s % 60
        return f"{m:02d}:{s:02d}"
