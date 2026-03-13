"""ChorusDetector — определение временных отрезков припевов в аудиофайле.

Поддерживает три бэкенда (управляется через конфигурацию `CHORUS_DETECTOR_BACKEND`):

- ``msaf``    — текущий подход через `msaf.process()` (spectral clustering).
- ``librosa`` — новый подход на основе признаков `librosa`:
                chroma, self-similarity matrix, tempogram stability, HPSS energy.
- ``hybrid``  — объединяет результаты обоих подходов для повышения точности.

Метод :meth:`detect` возвращает список кортежей ``(start_sec, end_sec)``
для каждого найденного припева.

Метод :meth:`detect_with_info` возвращает список :class:`SegmentInfo` с расширенной
информацией о каждом сегменте: тип сегмента и характеристики детекторов.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Тип для сегмента с меткой
_Segment = tuple[float, float, int]


@dataclass
class SegmentInfo:
    """Расширенная информация о сегменте, найденном детектором.

    Attributes
    ----------
    start:
        Начало сегмента в секундах.
    end:
        Конец сегмента в секундах.
    segment_type:
        Тип сегмента: ``"chorus"`` — припев, ``"non-chorus"`` — не припев.
    backend:
        Бэкенд, которым был найден сегмент: ``"msaf"``, ``"librosa"`` или ``"hybrid"``.
    scores:
        Словарь с характеристиками детектора для данного сегмента.
        Набор ключей зависит от бэкенда:

        - ``msaf``: ``{"label": int, "label_count": int, "label_duration": float}``
        - ``librosa``: ``{"sim_score": float, "hpss_score": float,
          "tempo_score": float, "total_score": float}``
        - ``hybrid``: ``{"confirmed_by": str}`` — источник подтверждения
          (``"librosa"``, ``"msaf"`` или ``"librosa_fallback"``).
    """

    start: float
    end: float
    segment_type: str  # "chorus" | "non-chorus"
    backend: str       # "msaf" | "librosa" | "hybrid"
    scores: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        """Длительность сегмента в секундах."""
        return self.end - self.start


@dataclass
class VolumeSegment:
    """Временной сегмент с заданной громкостью вокала.

    Является результатом шага детектирования припевов: содержит временные
    границы, громкость вокала и расширенную информацию от детектора.

    Attributes
    ----------
    start:
        Начало сегмента в секундах.
    end:
        Конец сегмента в секундах.
    volume:
        Громкость вокала в данном сегменте (0.0–1.0, где 1.0 = 100%).
    segment_type:
        Тип сегмента из детектора: ``"chorus"``, ``"non-chorus"`` или ``None``
        (если информация о типе недоступна).
    backend:
        Бэкенд детектора, которым был найден сегмент: ``"msaf"``, ``"librosa"``,
        ``"hybrid"`` или ``None``.
    scores:
        Характеристики детектора для данного сегмента (зависят от бэкенда).
        Пустой словарь, если информация недоступна.
    """
    start: float
    end: float
    volume: float
    segment_type: str | None = None
    backend: str | None = None
    scores: dict[str, Any] = field(default_factory=dict)


def build_volume_segments(
    chorus_segments: list[tuple[float, float]],
    audio_duration: float,
    chorus_volume: float,
    default_volume: float,
    segment_infos: list[SegmentInfo] | None = None,
) -> list[VolumeSegment]:
    """Построить список сегментов громкости на основе найденных припевов.

    Parameters
    ----------
    chorus_segments:
        Список кортежей ``(start_sec, end_sec)`` для каждого припева.
        Используется только если ``segment_infos`` не передан.
    audio_duration:
        Общая длительность аудиофайла в секундах.
    chorus_volume:
        Громкость вокала в припевах (``CHORUS_BACKVOCAL_VOLUME``).
    default_volume:
        Громкость вокала вне припевов (``AUDIO_MIX_VOICE_VOLUME``).
    segment_infos:
        Опциональный список :class:`SegmentInfo` — расширенная информация
        о **всех** сегментах от детектора (chorus + non-chorus).
        Если передан, используется напрямую для построения :class:`VolumeSegment`
        с полными данными детектора (``segment_type``, ``backend``, ``scores``).
        Если не передан — используется ``chorus_segments`` (fallback).

    Returns
    -------
    list[VolumeSegment]
        Полный список сегментов, покрывающий весь трек.
    """
    # Если переданы расширенные данные детектора — используем их напрямую.
    # segment_infos содержит ВСЕ сегменты (chorus + non-chorus) с характеристиками.
    if segment_infos:
        result: list[VolumeSegment] = []
        for info in sorted(segment_infos, key=lambda s: s.start):
            volume = chorus_volume if info.segment_type == "chorus" else default_volume
            result.append(
                VolumeSegment(
                    start=info.start,
                    end=info.end,
                    volume=volume,
                    segment_type=info.segment_type,
                    backend=info.backend,
                    scores=dict(info.scores),
                )
            )
        return result

    # Fallback: строим из chorus_segments без расширенной информации детектора
    if not chorus_segments:
        # No chorus detected — use default volume for the whole track
        return [VolumeSegment(start=0.0, end=audio_duration, volume=default_volume)]

    sorted_chorus = sorted(chorus_segments, key=lambda s: s[0])
    segments: list[VolumeSegment] = []

    current_pos = 0.0
    for start, end in sorted_chorus:
        # Non-chorus segment before this chorus
        if start > current_pos:
            segments.append(
                VolumeSegment(start=current_pos, end=start, volume=default_volume)
            )
        # Chorus segment
        segments.append(
            VolumeSegment(
                start=start,
                end=end,
                volume=chorus_volume,
                segment_type="chorus",
            )
        )
        current_pos = end

    # Non-chorus segment after the last chorus
    if current_pos < audio_duration:
        segments.append(
            VolumeSegment(start=current_pos, end=audio_duration, volume=default_volume)
        )

    return segments


def save_volume_segments(
    segments: list[VolumeSegment],
    output_path: Path,
) -> None:
    """Сохранить разметку громкости в JSON-файл.

    Сохраняет обязательные поля ``start``, ``end``, ``volume``, а также
    опциональные поля детектора: ``segment_type``, ``backend``, ``scores``.

    Parameters
    ----------
    segments:
        Список сегментов громкости.
    output_path:
        Путь к выходному JSON-файлу.
    """
    _logger = logging.getLogger(__name__)
    data = []
    for seg in segments:
        item: dict[str, Any] = {
            "start": seg.start,
            "end": seg.end,
            "volume": seg.volume,
        }
        if seg.segment_type is not None:
            item["segment_type"] = seg.segment_type
        if seg.backend is not None:
            item["backend"] = seg.backend
        if seg.scores:
            item["scores"] = seg.scores
        data.append(item)

    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _logger.debug(
        "save_volume_segments: saved %d segments to '%s'",
        len(segments),
        output_path,
    )


def load_volume_segments(input_path: Path) -> list[VolumeSegment]:
    """Загрузить разметку громкости из JSON-файла.

    Поддерживает как старый формат (только ``start``, ``end``, ``volume``),
    так и новый формат с полями ``segment_type``, ``backend``, ``scores``.

    Parameters
    ----------
    input_path:
        Путь к JSON-файлу с разметкой громкости.

    Returns
    -------
    list[VolumeSegment]
        Список сегментов громкости.

    Raises
    ------
    FileNotFoundError
        Если файл не найден.
    ValueError
        Если файл содержит некорректные данные.
    """
    _logger = logging.getLogger(__name__)
    if not input_path.exists():
        raise FileNotFoundError(
            f"Файл разметки громкости не найден: {input_path}"
        )
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Некорректный JSON в файле разметки громкости '{input_path}': {exc}"
        ) from exc

    segments: list[VolumeSegment] = []
    for item in data:
        segments.append(
            VolumeSegment(
                start=float(item["start"]),
                end=float(item["end"]),
                volume=float(item["volume"]),
                segment_type=item.get("segment_type"),
                backend=item.get("backend"),
                scores=item.get("scores", {}),
            )
        )
    _logger.debug(
        "load_volume_segments: loaded %d segments from '%s'",
        len(segments),
        input_path,
    )
    return segments


class ChorusDetector:
    """Определяет временные отрезки припевов в аудиофайле.

    Parameters
    ----------
    backend:
        Бэкенд детектирования: ``"msaf"``, ``"librosa"`` или ``"hybrid"``.
        По умолчанию ``"hybrid"``.
    min_duration_sec:
        Минимальная длительность сегмента-кандидата в секундах (по умолчанию 15).
    max_duration_sec:
        Максимальная длительность сегмента-кандидата в секундах (по умолчанию 60).
    """

    def __init__(
        self,
        backend: str = "hybrid",
        min_duration_sec: float = 15.0,
        max_duration_sec: float = 60.0,
    ) -> None:
        self._backend = backend.lower()
        self._min_duration = min_duration_sec
        self._max_duration = max_duration_sec

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_with_info(self, audio_file: str) -> list[SegmentInfo]:
        """Определить временные отрезки припевов с расширенной информацией.

        Parameters
        ----------
        audio_file:
            Путь к аудиофайлу (MP3, FLAC, WAV и т.д.).

        Returns
        -------
        list[SegmentInfo]
            Список объектов :class:`SegmentInfo` для каждого найденного припева.
            Каждый объект содержит временные границы, тип сегмента и
            характеристики детектора.
            Возвращает пустой список, если определить структуру не удалось.
        """
        audio_path = Path(audio_file)
        if not audio_path.exists():
            logger.warning("ChorusDetector: audio file not found: '%s'", audio_file)
            return []

        logger.debug(
            "ChorusDetector: backend=%s, min_dur=%.1f, max_dur=%.1f, file='%s'",
            self._backend,
            self._min_duration,
            self._max_duration,
            audio_file,
        )

        if self._backend == "msaf":
            return self._detect_msaf_with_info(audio_file)
        elif self._backend == "librosa":
            result = self._detect_librosa_with_info(audio_file)
            if not result:
                logger.warning(
                    "ChorusDetector: librosa backend returned no segments for '%s'",
                    audio_file,
                )
            return result
        elif self._backend == "hybrid":
            return self._detect_hybrid_with_info(audio_file)
        else:
            logger.warning(
                "ChorusDetector: unknown backend '%s', falling back to hybrid",
                self._backend,
            )
            return self._detect_hybrid_with_info(audio_file)

    def detect(self, audio_file: str) -> list[tuple[float, float]]:
        """Определить временные отрезки припевов в аудиофайле.

        Обёртка над :meth:`detect_with_info` для обратной совместимости.

        Parameters
        ----------
        audio_file:
            Путь к аудиофайлу (MP3, FLAC, WAV и т.д.).

        Returns
        -------
        list[tuple[float, float]]
            Список кортежей ``(start_sec, end_sec)`` для каждого припева.
            Возвращает пустой список, если определить структуру не удалось.
        """
        return [(seg.start, seg.end) for seg in self.detect_with_info(audio_file)]

    # ------------------------------------------------------------------
    # Backend: msaf
    # ------------------------------------------------------------------

    def _detect_msaf_with_info(self, audio_file: str) -> list[SegmentInfo]:
        """Детектирование через msaf (spectral clustering) с расширенной информацией."""
        try:
            import scipy
            if not hasattr(scipy, "inf"):
                scipy.inf = np.inf  # type: ignore[attr-defined]
            import msaf  # type: ignore[import]
        except ImportError:
            logger.error(
                "ChorusDetector: msaf is not installed. Install it with: uv add msaf"
            )
            return []

        try:
            logger.debug("ChorusDetector[msaf]: running msaf.process on '%s'", audio_file)
            boundaries, labels = msaf.process(
                str(audio_file),
                boundaries_id="sf",
                labels_id="scluster",
            )
            logger.debug(
                "ChorusDetector[msaf]: %d boundaries, labels=%s",
                len(boundaries) if boundaries is not None else 0,
                labels,
            )
        except Exception as exc:
            logger.warning(
                "ChorusDetector[msaf]: msaf.process failed for '%s': %s",
                audio_file,
                exc,
            )
            return []

        if boundaries is None or labels is None:
            logger.warning(
                "ChorusDetector[msaf]: returned None boundaries or labels for '%s'",
                audio_file,
            )
            return []

        segments = self._build_segments_from_boundaries(list(boundaries), list(labels))
        if not segments:
            return []

        # Собираем статистику по меткам для передачи в SegmentInfo
        label_count: dict[int, int] = {}
        label_duration: dict[int, float] = {}
        for start, end, label in segments:
            label_count[label] = label_count.get(label, 0) + 1
            label_duration[label] = label_duration.get(label, 0.0) + (end - start)

        chorus_segments = self._pick_chorus_segments(segments)
        chorus_segments = self._filter_by_duration(chorus_segments)

        # Определяем chorus_label для пометки типа сегмента
        chorus_set = set(chorus_segments)

        result: list[SegmentInfo] = []
        for start, end, label in segments:
            seg_type = "chorus" if (start, end) in chorus_set else "non-chorus"
            result.append(
                SegmentInfo(
                    start=start,
                    end=end,
                    segment_type=seg_type,
                    backend="msaf",
                    scores={
                        "label": label,
                        "label_count": label_count.get(label, 0),
                        "label_duration": round(label_duration.get(label, 0.0), 3),
                    },
                )
            )

        chorus_result = [s for s in result if s.segment_type == "chorus"]
        logger.info(
            "ChorusDetector[msaf]: detected %d chorus segment(s) for '%s': %s",
            len(chorus_result),
            audio_file,
            [(s.start, s.end) for s in chorus_result],
        )
        return result

    def _detect_msaf(self, audio_file: str) -> list[tuple[float, float]]:
        """Детектирование через msaf (spectral clustering).

        .. deprecated::
            Используйте :meth:`_detect_msaf_with_info` напрямую.
            Этот метод оставлен для внутренней совместимости.
        """
        infos = self._detect_msaf_with_info(audio_file)
        return [(s.start, s.end) for s in infos if s.segment_type == "chorus"]

    # ------------------------------------------------------------------
    # Backend: librosa
    # ------------------------------------------------------------------

    def _detect_librosa_with_info(self, audio_file: str) -> list[SegmentInfo]:
        """Детектирование на основе признаков librosa с расширенной информацией.

        Алгоритм:
        1. Загружает аудио через librosa.
        2. Вычисляет хроматограмму (chroma_cqt).
        3. Строит матрицу самоподобия (recurrence_matrix).
        4. Определяет границы сегментов через агломеративную кластеризацию
           по матрице самоподобия.
        5. Ранжирует сегменты по комбинации признаков:
           - повторяемость (self-similarity score),
           - энергия гармоники (HPSS),
           - ритмическая стабильность (tempogram).
        6. Выбирает сегменты с наивысшим суммарным рейтингом как припевы.
        """
        try:
            import librosa  # type: ignore[import]
        except ImportError:
            logger.error(
                "ChorusDetector: librosa is not installed. Install it with: uv add librosa"
            )
            return []

        try:
            logger.debug("ChorusDetector[librosa]: loading audio '%s'", audio_file)
            y, sr = librosa.load(audio_file, sr=22050, mono=True)
        except Exception as exc:
            logger.warning(
                "ChorusDetector[librosa]: failed to load audio '%s': %s",
                audio_file,
                exc,
            )
            return []

        duration = librosa.get_duration(y=y, sr=sr)
        logger.debug("ChorusDetector[librosa]: duration=%.1f sec", duration)

        # 1. Chroma + self-similarity
        chroma = self._compute_chroma(y, sr)
        sim_matrix = self._compute_self_similarity(chroma)

        # 2. Определяем границы сегментов через novelty curve
        boundaries_sec = self._compute_boundaries(y, sr, chroma, duration)
        logger.debug(
            "ChorusDetector[librosa]: %d boundaries found: %s",
            len(boundaries_sec),
            [f"{b:.1f}" for b in boundaries_sec],
        )

        if len(boundaries_sec) < 2:
            logger.warning(
                "ChorusDetector[librosa]: not enough boundaries for '%s'", audio_file
            )
            return []

        # 3. Строим сегменты и вычисляем признаки для каждого
        hop_length = 512
        frames_per_sec = sr / hop_length

        hpss_harmonic_energy = self._hpss_energy(y, sr)
        tempogram_stability = self._compute_tempogram_stability(y, sr, boundaries_sec)

        # Собираем все сегменты с оценками (включая отфильтрованные по длительности)
        all_scored: list[tuple[float, float, float, float, float, float]] = []
        for i in range(len(boundaries_sec) - 1):
            start = boundaries_sec[i]
            end = boundaries_sec[i + 1]
            dur = end - start

            if dur < self._min_duration or dur > self._max_duration:
                continue

            # Self-similarity score: среднее значение в блоке матрицы
            f_start = int(start * frames_per_sec)
            f_end = int(end * frames_per_sec)
            f_start = max(0, min(f_start, sim_matrix.shape[0] - 1))
            f_end = max(f_start + 1, min(f_end, sim_matrix.shape[0]))

            sim_score = float(np.mean(sim_matrix[f_start:f_end, :]))

            # HPSS harmonic energy score
            h_start = int(start * sr)
            h_end = int(end * sr)
            h_start = max(0, min(h_start, len(hpss_harmonic_energy) - 1))
            h_end = max(h_start + 1, min(h_end, len(hpss_harmonic_energy)))
            hpss_score = float(np.mean(hpss_harmonic_energy[h_start:h_end]))

            # Tempogram stability score
            tempo_score = tempogram_stability.get(i, 0.0)

            # Суммарный рейтинг
            total_score = sim_score + hpss_score + tempo_score

            logger.debug(
                "ChorusDetector[librosa]: seg [%.1f-%.1f] sim=%.3f hpss=%.3f tempo=%.3f total=%.3f",
                start,
                end,
                sim_score,
                hpss_score,
                tempo_score,
                total_score,
            )
            all_scored.append((start, end, sim_score, hpss_score, tempo_score, total_score))

        if not all_scored:
            logger.warning(
                "ChorusDetector[librosa]: no valid segments after filtering for '%s'",
                audio_file,
            )
            return []

        # 4. Определяем порог: сегменты с рейтингом выше медианы — припевы
        total_scores = [total for _, _, _, _, _, total in all_scored]
        threshold = float(np.median(total_scores))

        result: list[SegmentInfo] = []
        for start, end, sim_score, hpss_score, tempo_score, total_score in all_scored:
            seg_type = "chorus" if total_score >= threshold else "non-chorus"
            result.append(
                SegmentInfo(
                    start=start,
                    end=end,
                    segment_type=seg_type,
                    backend="librosa",
                    scores={
                        "sim_score": round(sim_score, 4),
                        "hpss_score": round(hpss_score, 4),
                        "tempo_score": round(tempo_score, 4),
                        "total_score": round(total_score, 4),
                        "threshold": round(threshold, 4),
                    },
                )
            )

        chorus_result = [s for s in result if s.segment_type == "chorus"]
        logger.info(
            "ChorusDetector[librosa]: detected %d chorus segment(s) for '%s': %s",
            len(chorus_result),
            audio_file,
            [(s.start, s.end) for s in chorus_result],
        )
        return result

    def _detect_librosa(self, audio_file: str) -> list[tuple[float, float]]:
        """Детектирование на основе признаков librosa.

        .. deprecated::
            Используйте :meth:`_detect_librosa_with_info` напрямую.
            Этот метод оставлен для внутренней совместимости.
        """
        infos = self._detect_librosa_with_info(audio_file)
        return [(s.start, s.end) for s in infos if s.segment_type == "chorus"]

    # ------------------------------------------------------------------
    # Backend: hybrid
    # ------------------------------------------------------------------

    def _detect_hybrid_with_info(self, audio_file: str) -> list[SegmentInfo]:
        """Объединяет результаты msaf и librosa с расширенной информацией.

        Стратегия:
        - Запускает оба бэкенда.
        - Если оба дали результат — объединяет chorus-сегменты с пересечением > 30%,
          а non-chorus сегменты от обоих бэкендов включает в результат.
        - Если только один дал результат — использует его.
        - Если ни один не дал результат — возвращает пустой список.

        Возвращает **все** сегменты (chorus + non-chorus) с характеристиками детекторов.
        """
        msaf_infos = self._detect_msaf_with_info(audio_file)
        librosa_infos = self._detect_librosa_with_info(audio_file)

        msaf_chorus = [(s.start, s.end) for s in msaf_infos if s.segment_type == "chorus"]
        librosa_chorus = [(s.start, s.end) for s in librosa_infos if s.segment_type == "chorus"]

        logger.debug(
            "ChorusDetector[hybrid]: msaf=%d chorus segs, librosa=%d chorus segs",
            len(msaf_chorus),
            len(librosa_chorus),
        )

        if not msaf_chorus and not librosa_chorus:
            logger.warning(
                "ChorusDetector[hybrid]: both backends returned no segments for '%s'",
                audio_file,
            )
            return []

        if not msaf_chorus:
            logger.info(
                "ChorusDetector[hybrid]: msaf returned nothing, using librosa results"
            )
            return [
                SegmentInfo(
                    start=s.start,
                    end=s.end,
                    segment_type=s.segment_type,
                    backend="hybrid",
                    scores={**s.scores, "confirmed_by": "librosa_only"},
                )
                for s in librosa_infos
            ]

        if not librosa_chorus:
            logger.info(
                "ChorusDetector[hybrid]: librosa returned nothing, using msaf results"
            )
            return [
                SegmentInfo(
                    start=s.start,
                    end=s.end,
                    segment_type=s.segment_type,
                    backend="hybrid",
                    scores={**s.scores, "confirmed_by": "msaf_only"},
                )
                for s in msaf_infos
            ]

        # Объединяем chorus-сегменты из обоих бэкендов
        merged_pairs = self._merge_segments_with_source(msaf_chorus, librosa_chorus)

        # Строим индексы оценок для быстрого поиска
        librosa_scores_map = {(s.start, s.end): s.scores for s in librosa_infos}
        msaf_scores_map = {(s.start, s.end): s.scores for s in msaf_infos}

        # 1. Добавляем все chorus-сегменты (результат объединения)
        result: list[SegmentInfo] = []
        for (start, end), confirmed_by in merged_pairs:
            if confirmed_by in ("librosa", "librosa_fallback"):
                scores = dict(librosa_scores_map.get((start, end), {}))
            else:
                scores = dict(msaf_scores_map.get((start, end), {}))
            scores["confirmed_by"] = confirmed_by
            result.append(
                SegmentInfo(
                    start=start,
                    end=end,
                    segment_type="chorus",
                    backend="hybrid",
                    scores=scores,
                )
            )

        # 2. Добавляем non-chorus сегменты от msaf (не вошедшие в chorus)
        for s in msaf_infos:
            if s.segment_type == "non-chorus":
                result.append(
                    SegmentInfo(
                        start=s.start,
                        end=s.end,
                        segment_type="non-chorus",
                        backend="hybrid",
                        scores={**s.scores, "source": "msaf"},
                    )
                )

        # 3. Добавляем non-chorus сегменты от librosa (не вошедшие в chorus и не перекрывающиеся с msaf non-chorus)
        msaf_non_chorus_ranges = [
            (s.start, s.end) for s in msaf_infos if s.segment_type == "non-chorus"
        ]
        for s in librosa_infos:
            if s.segment_type == "non-chorus":
                # Проверяем, не перекрывается ли с уже добавленными msaf non-chorus
                overlaps = any(
                    min(s.end, me) - max(s.start, ms) > 0
                    for ms, me in msaf_non_chorus_ranges
                )
                if not overlaps:
                    result.append(
                        SegmentInfo(
                            start=s.start,
                            end=s.end,
                            segment_type="non-chorus",
                            backend="hybrid",
                            scores={**s.scores, "source": "librosa"},
                        )
                    )

        result.sort(key=lambda x: x.start)

        chorus_result = [s for s in result if s.segment_type == "chorus"]
        logger.info(
            "ChorusDetector[hybrid]: merged %d chorus segment(s) for '%s': %s",
            len(chorus_result),
            audio_file,
            [(s.start, s.end) for s in chorus_result],
        )
        logger.debug(
            "ChorusDetector[hybrid]: total %d segment(s) (chorus + non-chorus) for '%s'",
            len(result),
            audio_file,
        )
        return result

    def _detect_hybrid(self, audio_file: str) -> list[tuple[float, float]]:
        """Объединяет результаты msaf и librosa для повышения точности.

        .. deprecated::
            Используйте :meth:`_detect_hybrid_with_info` напрямую.
            Этот метод оставлен для внутренней совместимости.
        """
        infos = self._detect_hybrid_with_info(audio_file)
        return [(s.start, s.end) for s in infos if s.segment_type == "chorus"]

    # ------------------------------------------------------------------
    # Helper: librosa feature computation
    # ------------------------------------------------------------------

    def _compute_chroma(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Вычислить хроматограмму (chroma_cqt).

        Returns
        -------
        np.ndarray
            Матрица chroma shape (12, T).
        """
        import librosa  # type: ignore[import]

        hop_length = 512
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
        logger.debug("ChorusDetector: chroma shape=%s", chroma.shape)
        return chroma

    def _compute_self_similarity(self, chroma: np.ndarray) -> np.ndarray:
        """Построить матрицу самоподобия на основе хроматограммы.

        Использует cosine similarity между фреймами.

        Returns
        -------
        np.ndarray
            Квадратная матрица shape (T, T) со значениями [0, 1].
        """
        import librosa  # type: ignore[import]

        # Нормализуем chroma по L2 для cosine similarity
        chroma_norm = librosa.util.normalize(chroma, axis=0)
        # recurrence_matrix возвращает булеву или взвешенную матрицу
        R = librosa.segment.recurrence_matrix(
            chroma_norm,
            mode="affinity",
            metric="cosine",
            sparse=False,
        )
        logger.debug("ChorusDetector: self-similarity matrix shape=%s", R.shape)
        return R

    def _compute_boundaries(
        self,
        y: np.ndarray,
        sr: int,
        chroma: np.ndarray,
        duration: float,
    ) -> list[float]:
        """Определить границы сегментов через novelty curve по chroma.

        Returns
        -------
        list[float]
            Список временных меток границ в секундах (включая 0 и duration).
        """
        import librosa  # type: ignore[import]

        hop_length = 512

        # Novelty curve через структурные границы
        try:
            # Используем агломеративную кластеризацию через librosa
            bounds_frames = librosa.segment.agglomerative(chroma, k=8)
            bounds_sec = librosa.frames_to_time(bounds_frames, sr=sr, hop_length=hop_length)
            bounds_list = [0.0] + sorted(float(b) for b in bounds_sec) + [duration]
            # Убираем дубликаты и слишком близкие границы (< 5 сек)
            filtered: list[float] = [bounds_list[0]]
            for b in bounds_list[1:]:
                if b - filtered[-1] >= 5.0:
                    filtered.append(b)
            if filtered[-1] < duration - 1.0:
                filtered.append(duration)
            return filtered
        except Exception as exc:
            logger.warning(
                "ChorusDetector: agglomerative segmentation failed: %s, "
                "falling back to fixed-size segments",
                exc,
            )
            # Fallback: фиксированные сегменты по 30 секунд
            step = 30.0
            return [float(t) for t in np.arange(0, duration, step)] + [duration]

    def _hpss_energy(self, y: np.ndarray, sr: int) -> np.ndarray:
        """Вычислить энергию гармонической составляющей (HPSS).

        Returns
        -------
        np.ndarray
            Массив RMS-энергии гармоники, shape (N_samples,).
            Значения нормализованы в [0, 1].
        """
        import librosa  # type: ignore[import]

        y_harmonic, _ = librosa.effects.hpss(y)
        # RMS по скользящему окну
        frame_length = 2048
        hop_length = 512
        rms = librosa.feature.rms(y=y_harmonic, frame_length=frame_length, hop_length=hop_length)[0]
        # Интерполируем обратно до длины сигнала
        rms_full = np.interp(
            np.arange(len(y)),
            np.linspace(0, len(y), len(rms)),
            rms,
        )
        # Нормализуем
        max_val = float(np.max(rms_full))
        if max_val > 0:
            rms_full = rms_full / max_val
        return rms_full

    def _compute_tempogram_stability(
        self,
        y: np.ndarray,
        sr: int,
        boundaries_sec: list[float],
    ) -> dict[int, float]:
        """Вычислить ритмическую стабильность для каждого сегмента.

        Использует tempogram: сегменты с более стабильным ритмом
        (меньшей дисперсией темпа) получают более высокий балл.

        Returns
        -------
        dict[int, float]
            Словарь {индекс_сегмента: stability_score} в [0, 1].
        """
        import librosa  # type: ignore[import]

        hop_length = 512
        try:
            oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
            tempogram = librosa.feature.tempogram(
                onset_envelope=oenv, sr=sr, hop_length=hop_length
            )
        except Exception as exc:
            logger.warning("ChorusDetector: tempogram computation failed: %s", exc)
            return {}

        frames_per_sec = sr / hop_length
        stability: dict[int, float] = {}

        for i in range(len(boundaries_sec) - 1):
            start = boundaries_sec[i]
            end = boundaries_sec[i + 1]
            f_start = int(start * frames_per_sec)
            f_end = int(end * frames_per_sec)
            f_start = max(0, min(f_start, tempogram.shape[1] - 1))
            f_end = max(f_start + 1, min(f_end, tempogram.shape[1]))

            seg_tempogram = tempogram[:, f_start:f_end]
            # Стабильность = 1 - нормализованная дисперсия доминирующего темпа
            dominant_tempo_idx = np.argmax(np.mean(seg_tempogram, axis=1))
            tempo_series = seg_tempogram[dominant_tempo_idx, :]
            if len(tempo_series) > 1:
                std = float(np.std(tempo_series))
                mean = float(np.mean(tempo_series))
                cv = std / (mean + 1e-8)  # coefficient of variation
                stability[i] = max(0.0, 1.0 - cv)
            else:
                stability[i] = 0.0

        # Нормализуем в [0, 1]
        if stability:
            max_s = max(stability.values())
            if max_s > 0:
                stability = {k: v / max_s for k, v in stability.items()}

        return stability

    # ------------------------------------------------------------------
    # Helper: msaf segment processing
    # ------------------------------------------------------------------

    def _build_segments_from_boundaries(
        self,
        boundaries: list[float],
        labels: list[int],
    ) -> list[_Segment]:
        """Построить список сегментов (start, end, label) из границ и меток."""
        if len(boundaries) < 2:
            return []
        segments: list[_Segment] = []
        for i, label in enumerate(labels):
            if i + 1 < len(boundaries):
                start = float(boundaries[i])
                end = float(boundaries[i + 1])
                segments.append((start, end, int(label)))
        return segments

    def _pick_chorus_segments(
        self, segments: list[_Segment]
    ) -> list[tuple[float, float]]:
        """Выбрать сегменты с наиболее часто встречающейся меткой (припев)."""
        label_count: dict[int, int] = {}
        label_duration: dict[int, float] = {}
        for start, end, label in segments:
            label_count[label] = label_count.get(label, 0) + 1
            label_duration[label] = label_duration.get(label, 0.0) + (end - start)

        logger.debug(
            "ChorusDetector[msaf]: label_count=%s, label_duration=%s",
            label_count,
            label_duration,
        )

        repeating = {lbl: cnt for lbl, cnt in label_count.items() if cnt > 1}
        if repeating:
            chorus_label = max(
                repeating,
                key=lambda lbl: (label_count[lbl], label_duration[lbl]),
            )
        else:
            chorus_label = max(label_duration, key=lambda lbl: label_duration[lbl])

        logger.debug("ChorusDetector[msaf]: chorus_label=%d", chorus_label)

        return [
            (start, end)
            for start, end, label in segments
            if label == chorus_label
        ]

    def _filter_by_duration(
        self, segments: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        """Отфильтровать сегменты по минимальной и максимальной длительности."""
        filtered = [
            (s, e)
            for s, e in segments
            if self._min_duration <= (e - s) <= self._max_duration
        ]
        if len(filtered) < len(segments):
            logger.debug(
                "ChorusDetector: filtered %d → %d segments by duration [%.1f, %.1f]",
                len(segments),
                len(filtered),
                self._min_duration,
                self._max_duration,
            )
        # Если после фильтрации ничего не осталось — возвращаем исходные
        return filtered if filtered else segments

    # ------------------------------------------------------------------
    # Helper: merge segments from two backends
    # ------------------------------------------------------------------

    def _merge_segments_with_source(
        self,
        msaf_segs: list[tuple[float, float]],
        librosa_segs: list[tuple[float, float]],
    ) -> list[tuple[tuple[float, float], str]]:
        """Объединить сегменты из двух бэкендов с указанием источника подтверждения.

        Returns
        -------
        list[tuple[tuple[float, float], str]]
            Список пар ``((start, end), confirmed_by)`` где ``confirmed_by`` —
            строка ``"librosa"``, ``"msaf"`` или ``"librosa_fallback"``.
        """
        confirmed_librosa: list[tuple[float, float]] = []
        confirmed_msaf: list[tuple[float, float]] = []

        for ls, le in librosa_segs:
            lib_dur = le - ls
            for ms, me in msaf_segs:
                overlap = min(le, me) - max(ls, ms)
                if overlap > 0 and lib_dur > 0 and overlap / lib_dur >= 0.3:
                    confirmed_librosa.append((ls, le))
                    break

        for ms, me in msaf_segs:
            msaf_dur = me - ms
            for ls, le in librosa_segs:
                overlap = min(me, le) - max(ms, ls)
                if overlap > 0 and msaf_dur > 0 and overlap / msaf_dur >= 0.3:
                    confirmed_msaf.append((ms, me))
                    break

        # Предпочитаем librosa-границы, дополняем msaf-сегментами без пересечений
        result: list[tuple[tuple[float, float], str]] = [
            (seg, "librosa") for seg in confirmed_librosa
        ]
        result_segs = list(confirmed_librosa)

        for ms, me in confirmed_msaf:
            overlaps_existing = any(
                min(me, re) - max(ms, rs) > 0
                for rs, re in result_segs
            )
            if not overlaps_existing:
                result.append(((ms, me), "msaf"))
                result_segs.append((ms, me))

        # Если ничего не подтверждено — fallback на librosa (более надёжный)
        if not result:
            logger.debug(
                "ChorusDetector[hybrid]: no confirmed segments, falling back to librosa"
            )
            result = [(seg, "librosa_fallback") for seg in librosa_segs]

        result.sort(key=lambda x: x[0][0])
        return result

    def _merge_segments(
        self,
        msaf_segs: list[tuple[float, float]],
        librosa_segs: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        """Объединить сегменты из двух бэкендов.

        .. deprecated::
            Используйте :meth:`_merge_segments_with_source` напрямую.
            Этот метод оставлен для внутренней совместимости.
        """
        return [seg for seg, _ in self._merge_segments_with_source(msaf_segs, librosa_segs)]
