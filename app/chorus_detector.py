"""ChorusDetector — определение временных отрезков припевов в аудиофайле.

Поддерживает три бэкенда (управляется через конфигурацию `CHORUS_DETECTOR_BACKEND`):

- ``msaf``    — текущий подход через `msaf.process()` (spectral clustering).
- ``librosa`` — новый подход на основе признаков `librosa`:
                chroma, self-similarity matrix, tempogram stability, HPSS energy.
- ``hybrid``  — объединяет результаты обоих подходов для повышения точности.

Метод :meth:`detect` возвращает список кортежей ``(start_sec, end_sec)``
для каждого найденного припева.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Тип для сегмента с меткой
_Segment = tuple[float, float, int]


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

    def detect(self, audio_file: str) -> list[tuple[float, float]]:
        """Определить временные отрезки припевов в аудиофайле.

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
            return self._detect_msaf(audio_file)
        elif self._backend == "librosa":
            result = self._detect_librosa(audio_file)
            if not result:
                logger.warning(
                    "ChorusDetector: librosa backend returned no segments for '%s'",
                    audio_file,
                )
            return result
        elif self._backend == "hybrid":
            return self._detect_hybrid(audio_file)
        else:
            logger.warning(
                "ChorusDetector: unknown backend '%s', falling back to hybrid",
                self._backend,
            )
            return self._detect_hybrid(audio_file)

    # ------------------------------------------------------------------
    # Backend: msaf
    # ------------------------------------------------------------------

    def _detect_msaf(self, audio_file: str) -> list[tuple[float, float]]:
        """Детектирование через msaf (spectral clustering)."""
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

        chorus_segments = self._pick_chorus_segments(segments)
        chorus_segments = self._filter_by_duration(chorus_segments)

        logger.info(
            "ChorusDetector[msaf]: detected %d chorus segment(s) for '%s': %s",
            len(chorus_segments),
            audio_file,
            chorus_segments,
        )
        return chorus_segments

    # ------------------------------------------------------------------
    # Backend: librosa
    # ------------------------------------------------------------------

    def _detect_librosa(self, audio_file: str) -> list[tuple[float, float]]:
        """Детектирование на основе признаков librosa.

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

        scored_segments: list[tuple[float, float, float]] = []
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

            # Суммарный рейтинг (нормализованный)
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
            scored_segments.append((start, end, total_score))

        if not scored_segments:
            logger.warning(
                "ChorusDetector[librosa]: no valid segments after filtering for '%s'",
                audio_file,
            )
            return []

        # 4. Выбираем сегменты с рейтингом выше медианы
        scores = [s for _, _, s in scored_segments]
        threshold = float(np.median(scores))
        chorus_segments = [
            (start, end)
            for start, end, score in scored_segments
            if score >= threshold
        ]

        logger.info(
            "ChorusDetector[librosa]: detected %d chorus segment(s) for '%s': %s",
            len(chorus_segments),
            audio_file,
            chorus_segments,
        )
        return chorus_segments

    # ------------------------------------------------------------------
    # Backend: hybrid
    # ------------------------------------------------------------------

    def _detect_hybrid(self, audio_file: str) -> list[tuple[float, float]]:
        """Объединяет результаты msaf и librosa для повышения точности.

        Стратегия:
        - Запускает оба бэкенда.
        - Если оба дали результат — объединяет сегменты с пересечением > 50%.
        - Если только один дал результат — использует его.
        - Если ни один не дал результат — возвращает пустой список.
        """
        msaf_segments = self._detect_msaf(audio_file)
        librosa_segments = self._detect_librosa(audio_file)

        logger.debug(
            "ChorusDetector[hybrid]: msaf=%d segs, librosa=%d segs",
            len(msaf_segments),
            len(librosa_segments),
        )

        if not msaf_segments and not librosa_segments:
            logger.warning(
                "ChorusDetector[hybrid]: both backends returned no segments for '%s'",
                audio_file,
            )
            return []

        if not msaf_segments:
            logger.info(
                "ChorusDetector[hybrid]: msaf returned nothing, using librosa results"
            )
            return librosa_segments

        if not librosa_segments:
            logger.info(
                "ChorusDetector[hybrid]: librosa returned nothing, using msaf results"
            )
            return msaf_segments

        # Объединяем: берём сегменты из librosa, которые пересекаются с msaf
        # (librosa даёт более точные временные границы)
        merged = self._merge_segments(msaf_segments, librosa_segments)

        logger.info(
            "ChorusDetector[hybrid]: merged %d chorus segment(s) for '%s': %s",
            len(merged),
            audio_file,
            merged,
        )
        return merged

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

    def _merge_segments(
        self,
        msaf_segs: list[tuple[float, float]],
        librosa_segs: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        """Объединить сегменты из двух бэкендов.

        Стратегия:
        - Для каждого сегмента librosa проверяем, пересекается ли он
          хотя бы с одним сегментом msaf (перекрытие > 30% длины librosa-сегмента).
        - Если да — включаем librosa-сегмент в результат (он точнее по границам).
        - Если librosa-сегмент не подтверждён msaf — всё равно включаем,
          если msaf-сегмент подтверждён librosa.
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
        result = list(confirmed_librosa)
        for ms, me in confirmed_msaf:
            # Добавляем msaf-сегмент только если он не перекрывается с уже добавленными
            overlaps_existing = any(
                min(me, re) - max(ms, rs) > 0
                for rs, re in result
            )
            if not overlaps_existing:
                result.append((ms, me))

        # Если ничего не подтверждено — fallback на librosa (более надёжный)
        if not result:
            logger.debug(
                "ChorusDetector[hybrid]: no confirmed segments, falling back to librosa"
            )
            result = librosa_segs

        result.sort(key=lambda x: x[0])
        return result
