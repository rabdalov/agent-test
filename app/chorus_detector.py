"""ChorusDetector — определение временных отрезков сегментов в аудиофайле.

Поддерживает два режима работы:

- **Двухфайловый** (``vocal_file`` передан): объединяет границы из ``track_source``
  и ``vocal_file`` через msaf, обогащает признаками librosa, классифицирует сегменты
  по расширенному набору типов.
- **Однофайловый** (``vocal_file`` не передан): работает только через msaf на
  ``track_source``, без детектирования ``"instrumental"``.

Метод :meth:`ChorusDetector.detect` возвращает список :class:`SegmentInfo` с расширенной
информацией о каждом сегменте: тип сегмента и характеристики детекторов.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SegmentScore:
    """Метрики отдельного суб-сегмента.
    
    Attributes
    ----------
    id:
        Порядковый номер суб-сегмента (из msaf).
    vocal_energy:
        Нормализованная энергия вокала [0, 1].
    chroma_variance:
        Вариативность chroma features [0, 1].
    sim_score:
        Self-similarity score [0, 1].
    hpss_score:
        Harmonic-percussive separation score [0, 1].
    tempo_score:
        Rhythmic stability score [0, 1].
    """
    id: int
    vocal_energy: float = 0.0
    chroma_variance: float = 0.0
    sim_score: float = 0.0
    hpss_score: float = 0.0
    tempo_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в dict для JSON."""
        return {
            "id": self.id,
            "vocal_energy": self.vocal_energy,
            "chroma_variance": self.chroma_variance,
            "sim_score": self.sim_score,
            "hpss_score": self.hpss_score,
            "tempo_score": self.tempo_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SegmentScore":
        """Десериализовать из dict."""
        return cls(
            id=int(data.get("id", 0)),
            vocal_energy=float(data.get("vocal_energy", 0.0)),
            chroma_variance=float(data.get("chroma_variance", 0.0)),
            sim_score=float(data.get("sim_score", 0.0)),
            hpss_score=float(data.get("hpss_score", 0.0)),
            tempo_score=float(data.get("tempo_score", 0.0)),
        )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


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
        Тип сегмента: ``"chorus"`` | ``"verse"`` | ``"bridge"`` |
        ``"intro"`` | ``"outro"`` | ``"instrumental"``.
    backend:
        Бэкенд, которым был найден сегмент: ``"dual_file"`` или ``"single_file"``.
    scores:
        Словарь с характеристиками детектора для данного сегмента.
        Возможные ключи: ``vocal_energy``, ``chroma_variance``,
        ``sim_score``, ``hpss_score``, ``tempo_score``.
    """

    start: float
    end: float
    segment_type: str  # "chorus" | "verse" | "bridge" | "intro" | "outro" | "instrumental"
    backend: str       # "dual_file" | "single_file"
    scores: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        """Длительность сегмента в секундах."""
        return self.end - self.start


@dataclass
class VolumeSegment:
    """Временной сегмент с заданной громкостью вокала.
    
    scores ВСЕГДА список SegmentScore, даже для несгруппированных сегментов.
    
    Attributes
    ----------
    start, end, volume, segment_type, backend:
        Как раньше.
    scores:
        Список метрик суб-сегментов (всегда list, никогда dict).
        Для несгруппированного сегмента — список из 1 элемента.
    id:
        id первого суб-сегмента (= scores[0].id если scores не пуст).
    """
    start: float
    end: float
    volume: float
    segment_type: str | None = None
    backend: str | None = None
    scores: list[SegmentScore] = field(default_factory=list)
    id: int = 0

    @property
    def duration(self) -> float:
        """Длительность сегмента в секундах."""
        return self.end - self.start

    @property
    def subsegment_count(self) -> int:
        """Количество суб-сегментов в группе."""
        return len(self.scores)

    def get_first_id(self) -> int:
        """Получить id первого суб-сегмента."""
        return self.scores[0].id if self.scores else self.id

    def get_last_id(self) -> int:
        """Получить id последнего суб-сегмента."""
        return self.scores[-1].id if self.scores else self.id

    def get_id_range(self) -> str:
        """Вернуть строку диапазона id, например '#1-3'."""
        if not self.scores:
            return f"#{self.id}"
        first = self.get_first_id()
        last = self.get_last_id()
        if first == last:
            return f"#{first}"
        return f"#{first}-{last}"

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в dict для JSON."""
        result: dict[str, Any] = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "volume": self.volume,
        }
        if self.segment_type is not None:
            result["segment_type"] = self.segment_type
        if self.backend is not None:
            result["backend"] = self.backend
        if self.scores:
            result["scores"] = [s.to_dict() for s in self.scores]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VolumeSegment":
        """Десериализовать из dict.
        
        Поддерживает ТОЛЬКО новый формат (scores как list[dict]).
        """
        scores_data = data.get("scores", [])
        scores: list[SegmentScore] = []
        
        if isinstance(scores_data, list):
            scores = [SegmentScore.from_dict(s) for s in scores_data]
        # Если scores не list — ошибка, старый формат не поддерживается
        
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            volume=float(data["volume"]),
            segment_type=data.get("segment_type"),
            backend=data.get("backend"),
            scores=scores,
            id=int(data.get("id", 0)),
        )


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _merge_boundaries(
    boundaries_a: list[float],
    boundaries_b: list[float],
    tolerance_sec: float = 2.0,
) -> list[float]:
    """Объединить два списка границ с допуском.

    Алгоритм: объединить, отсортировать, удалить дубликаты
    (если разница между соседними < tolerance_sec — оставить первый).

    Parameters
    ----------
    boundaries_a:
        Первый список временных меток границ.
    boundaries_b:
        Второй список временных меток границ.
    tolerance_sec:
        Допуск в секундах для объединения близких границ.

    Returns
    -------
    list[float]
        Отсортированный список объединённых границ без дубликатов.
    """
    combined = sorted(set(boundaries_a) | set(boundaries_b))
    if not combined:
        return []

    merged: list[float] = [combined[0]]
    for b in combined[1:]:
        if b - merged[-1] >= tolerance_sec:
            merged.append(b)

    return merged


def _boundaries_to_segments(
    boundaries: list[float],
) -> list[tuple[float, float]]:
    """Построить сегменты (start, end) из отсортированного списка границ.

    Parameters
    ----------
    boundaries:
        Отсортированный список временных меток границ.

    Returns
    -------
    list[tuple[float, float]]
        Список кортежей ``(start, end)`` для каждого сегмента.
    """
    if len(boundaries) < 2:
        return []
    return [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]


def _compute_vocal_energy_per_segment(
    vocal_file: str,
    segments: list[tuple[float, float]],
) -> list[float]:
    """Вычислить среднюю RMS-энергию вокала для каждого сегмента.

    Использует librosa.load() + librosa.feature.rms().
    Возвращает нормализованные значения в [0, 1].

    Parameters
    ----------
    vocal_file:
        Путь к файлу вокальной дорожки.
    segments:
        Список кортежей ``(start, end)`` для каждого сегмента.

    Returns
    -------
    list[float]
        Список нормализованных значений RMS-энергии для каждого сегмента.
        При ошибке загрузки возвращает список из единиц (вокал везде).
    """
    try:
        import librosa  # type: ignore[import]
    except ImportError:
        logger.error(
            "_compute_vocal_energy_per_segment: librosa is not installed. "
            "Install it with: uv add librosa"
        )
        return [1.0] * len(segments)

    try:
        y, sr = librosa.load(vocal_file, sr=22050, mono=True)
    except Exception as exc:
        logger.warning(
            "_compute_vocal_energy_per_segment: failed to load vocal file '%s': %s",
            vocal_file,
            exc,
        )
        return [1.0] * len(segments)

    hop_length = 512
    frame_length = 2048
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
    frames_per_sec = sr / hop_length

    energies: list[float] = []
    for start, end in segments:
        f_start = int(start * frames_per_sec)
        f_end = int(end * frames_per_sec)
        f_start = max(0, min(f_start, len(rms) - 1))
        f_end = max(f_start + 1, min(f_end, len(rms)))
        seg_rms = float(np.mean(rms[f_start:f_end]))
        energies.append(seg_rms)

    # Нормализуем в [0, 1]
    max_energy = max(energies) if energies else 0.0
    if max_energy > 0:
        energies = [e / max_energy for e in energies]

    return energies


def _get_volume_for_segment_type(
    segment_type: str,
    chorus_volume: float,
    default_volume: float,
) -> float:
    """Определить громкость вокала для типа сегмента.

    Parameters
    ----------
    segment_type:
        Тип сегмента: ``"chorus"``, ``"instrumental"``, ``"verse"``, и т.д.
    chorus_volume:
        Громкость для припевов.
    default_volume:
        Громкость по умолчанию (для всех остальных типов).

    Returns
    -------
    float
        Громкость вокала для данного типа сегмента.
    """
    if segment_type == "chorus":
        return chorus_volume
    elif segment_type == "instrumental":
        return default_volume  # инструментал — вокал на стандартной громкости
    else:
        return default_volume  # verse, bridge, intro, outro


# ---------------------------------------------------------------------------
# Volume segments functions
# ---------------------------------------------------------------------------


def build_volume_segments(
    chorus_segments: list[tuple[float, float]],
    audio_duration: float,
    chorus_volume: float,
    default_volume: float,
    segment_infos: list[SegmentInfo] | None = None,
) -> list[VolumeSegment]:
    """Построить список сегментов громкости на основе найденных сегментов.

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
        о **всех** сегментах от детектора.
        Если передан, используется напрямую для построения :class:`VolumeSegment`
        с полными данными детектора (``segment_type``, ``backend``, ``scores``).
        Если не передан — используется ``chorus_segments`` (fallback).

    Returns
    -------
    list[VolumeSegment]
        Полный список сегментов, покрывающий весь трек.
    """
    # Если переданы расширенные данные детектора — используем их с заполнением пробелов.
    if segment_infos:
        result: list[VolumeSegment] = []
        sorted_infos = sorted(segment_infos, key=lambda s: s.start)
        current_pos = 0.0
        for info in sorted_infos:
            # Заполнить пробел перед сегментом (если есть)
            if info.start > current_pos + 0.01:
                result.append(
                    VolumeSegment(
                        start=current_pos,
                        end=info.start,
                        volume=default_volume,
                        scores=[],
                    )
                )
            volume = _get_volume_for_segment_type(
                info.segment_type, chorus_volume, default_volume
            )
            
            # Создаём SegmentScore из info.scores
            scores_list = [SegmentScore(
                id=0,  # Будет присвоен позже
                vocal_energy=float(info.scores.get("vocal_energy", 1.0)),
                chroma_variance=float(info.scores.get("chroma_variance", 0.0)),
                sim_score=float(info.scores.get("sim_score", 0.0)),
                hpss_score=float(info.scores.get("hpss_score", 0.0)),
                tempo_score=float(info.scores.get("tempo_score", 0.0)),
            )]
            
            result.append(
                VolumeSegment(
                    start=info.start,
                    end=info.end,
                    volume=volume,
                    segment_type=info.segment_type,
                    backend=info.backend,
                    scores=scores_list,
                )
            )
            current_pos = info.end
        # Заполнить пробел после последнего сегмента (если есть)
        if current_pos < audio_duration - 0.01:
            result.append(
                VolumeSegment(
                    start=current_pos,
                    end=audio_duration,
                    volume=default_volume,
                    scores=[],
                )
            )
        # Присваиваем порядковые номера
        for idx, seg in enumerate(result, start=1):
            seg.id = idx
            # Обновляем id в scores, если есть
            if seg.scores:
                seg.scores[0].id = idx
        return result

    # Fallback: строим из chorus_segments без расширенной информации детектора
    if not chorus_segments:
        # No chorus detected — use default volume for the whole track
        seg = VolumeSegment(start=0.0, end=audio_duration, volume=default_volume, id=1, scores=[])
        return [seg]

    sorted_chorus = sorted(chorus_segments, key=lambda s: s[0])
    segments: list[VolumeSegment] = []

    current_pos = 0.0
    for start, end in sorted_chorus:
        # Non-chorus segment before this chorus
        if start > current_pos:
            segments.append(
                VolumeSegment(start=current_pos, end=start, volume=default_volume, scores=[])
            )
        # Chorus segment
        segments.append(
            VolumeSegment(
                start=start,
                end=end,
                volume=chorus_volume,
                segment_type="chorus",
                scores=[],
            )
        )
        current_pos = end

    # Non-chorus segment after the last chorus
    if current_pos < audio_duration:
        segments.append(
            VolumeSegment(start=current_pos, end=audio_duration, volume=default_volume, scores=[])
        )

    # Присваиваем порядковые номера
    for idx, seg in enumerate(segments, start=1):
        seg.id = idx

    return segments


def save_volume_segments(
    segments: list[VolumeSegment],
    output_path: Path,
) -> None:
    """Сохранить разметку громкости в JSON.
    
    Использует новый формат с scores как list[SegmentScore].
    """
    _logger = logging.getLogger(__name__)
    data = [seg.to_dict() for seg in segments]
    
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
    
    Поддерживает ТОЛЬКО новый формат: scores как list[dict].
    """
    _logger = logging.getLogger(__name__)
    if not input_path.exists():
        raise FileNotFoundError(f"Файл разметки громкости не найден: {input_path}")
    
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Некорректный JSON в файле '{input_path}': {exc}") from exc
    
    segments = [VolumeSegment.from_dict(item) for item in data]
    
    _logger.debug(
        "load_volume_segments: loaded %d segments from '%s'",
        len(segments),
        input_path,
    )
    return segments


# ---------------------------------------------------------------------------
# ChorusDetector class
# ---------------------------------------------------------------------------


class ChorusDetector:
    """Определяет временные отрезки сегментов в аудиофайле.

    Поддерживает два режима:

    - **Двухфайловый** (``vocal_file`` передан в :meth:`detect`): объединяет
      границы из ``audio_file`` и ``vocal_file`` через msaf, обогащает признаками
      librosa, классифицирует сегменты по расширенному набору типов.
    - **Однофайловый** (``vocal_file`` не передан): работает только через msaf
      на ``audio_file``, без детектирования ``"instrumental"``.

    Parameters
    ----------
    min_duration_sec:
        Минимальная длительность сегмента-кандидата в секундах (по умолчанию 5.0).
    vocal_silence_threshold:
        Порог энергии вокала для определения инструментального сегмента
        (по умолчанию 0.05).
    boundary_merge_tolerance_sec:
        Допуск в секундах при объединении границ из двух файлов (по умолчанию 2.0).
    """

    def __init__(
        self,
        min_duration_sec: float = 5.0,
        vocal_silence_threshold: float = 0.05,
        boundary_merge_tolerance_sec: float = 2.0,
        chorus_volume: float = 0.4,
        default_volume: float = 0.2,
    ) -> None:
        self._min_duration = min_duration_sec
        self._vocal_silence_threshold = vocal_silence_threshold
        self._boundary_merge_tolerance = boundary_merge_tolerance_sec
        self._chorus_volume = chorus_volume
        self._default_volume = default_volume

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        audio_file: str,
        vocal_file: str | None = None,
    ) -> list[SegmentInfo]:
        """Определить сегменты с расширенной информацией.

        Если ``vocal_file`` передан — использует двухфайловый подход:
        объединяет границы из обоих файлов и детектирует ``"instrumental"``.
        Если ``vocal_file`` не передан — использует только ``audio_file``
        через msaf, без детектирования ``"instrumental"``.

        Parameters
        ----------
        audio_file:
            Путь к основному аудиофайлу (полный трек).
        vocal_file:
            Путь к файлу вокальной дорожки (опционально).

        Returns
        -------
        list[SegmentInfo]
            Список объектов :class:`SegmentInfo` для каждого найденного сегмента.
            Возвращает пустой список, если определить структуру не удалось.
        """
        audio_path = Path(audio_file)
        if not audio_path.exists():
            logger.warning("ChorusDetector: audio file not found: '%s'", audio_file)
            return []

        backend_name = "dual_file" if vocal_file else "single_file"
        logger.debug(
            "ChorusDetector: mode=%s, min_dur=%.1f, file='%s'",
            backend_name,
            self._min_duration,
            audio_file,
        )

        # Шаг 1: Получить границы через msaf
        boundaries_full = self._get_msaf_boundaries(audio_file)
        if not boundaries_full:
            logger.warning(
                "ChorusDetector: msaf returned no boundaries for '%s'", audio_file
            )
            return []

        boundaries_vocal: list[float] = []
        if vocal_file:
            vocal_path = Path(vocal_file)
            if vocal_path.exists():
                boundaries_vocal = self._get_msaf_boundaries(vocal_file)
                if not boundaries_vocal:
                    logger.warning(
                        "ChorusDetector: msaf returned no boundaries for vocal file '%s', "
                        "using only track boundaries",
                        vocal_file,
                    )
            else:
                logger.warning(
                    "ChorusDetector: vocal file not found: '%s', "
                    "using only track boundaries",
                    vocal_file,
                )

        # Шаг 2: Объединить границы
        merged_boundaries = _merge_boundaries(
            boundaries_full,
            boundaries_vocal,
            tolerance_sec=self._boundary_merge_tolerance,
        )
        segments = _boundaries_to_segments(merged_boundaries)
        # Объединяем короткие сегменты с соседними (не удаляем — это создаёт пробелы)
        segments = self._merge_short_segments_internal(segments, self._min_duration)

        if not segments:
            logger.warning(
                "ChorusDetector: no segments after merging short segments "
                "(min_duration=%.1f) for '%s'",
                self._min_duration,
                audio_file,
            )
            return []

        logger.debug(
            "ChorusDetector: %d segments after merging and filtering: %s",
            len(segments),
            [(f"{s:.1f}", f"{e:.1f}") for s, e in segments],
        )

        # Шаг 3: Вычислить vocal_energy для каждого сегмента
        if vocal_file and Path(vocal_file).exists():
            vocal_energy_list = _compute_vocal_energy_per_segment(vocal_file, segments)
        else:
            # Нет данных о вокале — считаем вокал везде
            vocal_energy_list = [1.0] * len(segments)

        # Шаг 4: Обогатить сегменты признаками librosa
        features_list = self._enrich_segments_with_librosa(
            audio_file, segments, vocal_energy_list
        )

        # Шаг 5: Классифицировать сегменты
        total_segments = len(segments)
        result: list[SegmentInfo] = []
        for i, ((start, end), features) in enumerate(zip(segments, features_list)):
            seg_type = self._classify_segment(
                features=features,
                segment_index=i,
                total_segments=total_segments,
                all_features=features_list,
                has_vocal_data=(vocal_file is not None),
            )
            result.append(
                SegmentInfo(
                    start=start,
                    end=end,
                    segment_type=seg_type,
                    backend=backend_name,
                    scores={
                        "vocal_energy": round(features.get("vocal_energy", 1.0), 4),
                        "chroma_variance": round(features.get("chroma_variance", 0.0), 4),
                        "sim_score": round(features.get("sim_score", 0.0), 4),
                        "hpss_score": round(features.get("hpss_score", 0.0), 4),
                        "tempo_score": round(features.get("tempo_score", 0.0), 4),
                    },
                )
            )

        chorus_result = [s for s in result if s.segment_type == "chorus"]
        logger.info(
            "ChorusDetector[%s]: detected %d chorus segment(s) for '%s': %s",
            backend_name,
            len(chorus_result),
            audio_file,
            [(s.start, s.end) for s in chorus_result],
        )
        logger.debug(
            "ChorusDetector[%s]: total %d segment(s) for '%s': %s",
            backend_name,
            len(result),
            audio_file,
            [(s.start, s.end, s.segment_type) for s in result],
        )
        return result

    def _merge_short_segments_internal(
        self,
        segments: list[tuple[float, float]],
        min_duration: float,
    ) -> list[tuple[float, float]]:
        """Объединить короткие сегменты с соседними.
        
        Сегменты короче min_duration объединяются с предыдущим сегментом.
        Это внутренний метод для работы с сырыми сегментами (кортежами).
        
        Parameters
        ----------
        segments:
            Список кортежей (start, end).
        min_duration:
            Минимальная длительность сегмента в секундах.
            
        Returns
        -------
        list[tuple[float, float]]
            Список объединённых сегментов.
        """
        if not segments:
            return []
        
        result: list[tuple[float, float]] = [segments[0]]
        
        for current in segments[1:]:
            prev_start, prev_end = result[-1]
            curr_start, curr_end = current
            curr_duration = curr_end - curr_start
            
            if curr_duration < min_duration:
                # Объединяем с предыдущим: расширяем prev_end до curr_end
                result[-1] = (prev_start, curr_end)
            else:
                result.append(current)
        
        return result

    # ------------------------------------------------------------------
    # Private: msaf boundary extraction
    # ------------------------------------------------------------------

    def _get_msaf_boundaries(self, audio_file: str) -> list[float]:
        """Получить границы сегментов через msaf.

        Parameters
        ----------
        audio_file:
            Путь к аудиофайлу.

        Returns
        -------
        list[float]
            Список временных меток границ в секундах.
            Пустой список при ошибке.
        """
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
                "ChorusDetector[msaf]: %d boundaries for '%s'",
                len(boundaries) if boundaries is not None else 0,
                audio_file,
            )
        except Exception as exc:
            logger.warning(
                "ChorusDetector[msaf]: msaf.process failed for '%s': %s",
                audio_file,
                exc,
            )
            return []

        if boundaries is None or len(boundaries) == 0:
            logger.warning(
                "ChorusDetector[msaf]: returned None or empty boundaries for '%s'",
                audio_file,
            )
            return []

        return [float(b) for b in boundaries]

    # ------------------------------------------------------------------
    # Private: librosa feature enrichment
    # ------------------------------------------------------------------

    def _enrich_segments_with_librosa(
        self,
        audio_file: str,
        segments: list[tuple[float, float]],
        vocal_energy_list: list[float],
    ) -> list[dict]:
        """Вычислить librosa-признаки для каждого сегмента.

        Признаки: sim_score, hpss_score, tempo_score, vocal_energy, chroma_variance.
        Все признаки нормализованы в [0, 1].

        Parameters
        ----------
        audio_file:
            Путь к аудиофайлу.
        segments:
            Список кортежей ``(start, end)`` для каждого сегмента.
        vocal_energy_list:
            Список значений vocal_energy для каждого сегмента (из шага 2.3).

        Returns
        -------
        list[dict]
            Список словарей с признаками для каждого сегмента.
            При ошибке загрузки librosa возвращает список с нулевыми признаками.
        """
        empty_features = [
            {
                "sim_score": 0.0,
                "hpss_score": 0.0,
                "tempo_score": 0.0,
                "vocal_energy": vocal_energy_list[i] if i < len(vocal_energy_list) else 1.0,
                "chroma_variance": 0.0,
            }
            for i in range(len(segments))
        ]

        try:
            import librosa  # type: ignore[import]
        except ImportError:
            logger.error(
                "ChorusDetector: librosa is not installed. Install it with: uv add librosa"
            )
            return empty_features

        try:
            logger.debug("ChorusDetector[librosa]: loading audio '%s'", audio_file)
            y, sr = librosa.load(audio_file, sr=22050, mono=True)
        except Exception as exc:
            logger.warning(
                "ChorusDetector[librosa]: failed to load audio '%s': %s",
                audio_file,
                exc,
            )
            return empty_features

        hop_length = 512
        frames_per_sec = sr / hop_length

        # Chroma + self-similarity matrix
        try:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
            chroma_norm = librosa.util.normalize(chroma, axis=0)
            sim_matrix = librosa.segment.recurrence_matrix(
                chroma_norm,
                mode="affinity",
                metric="cosine",
                sparse=False,
            )
        except Exception as exc:
            logger.warning(
                "ChorusDetector[librosa]: chroma/similarity computation failed: %s", exc
            )
            sim_matrix = None
            chroma = None

        # HPSS harmonic energy
        try:
            y_harmonic, _ = librosa.effects.hpss(y)
            frame_length = 2048
            rms_harmonic = librosa.feature.rms(
                y=y_harmonic, frame_length=frame_length, hop_length=hop_length
            )[0]
            rms_full = np.interp(
                np.arange(len(y)),
                np.linspace(0, len(y), len(rms_harmonic)),
                rms_harmonic,
            )
            max_rms = float(np.max(rms_full))
            if max_rms > 0:
                rms_full = rms_full / max_rms
        except Exception as exc:
            logger.warning(
                "ChorusDetector[librosa]: HPSS computation failed: %s", exc
            )
            rms_full = None

        # Tempogram
        try:
            oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
            tempogram = librosa.feature.tempogram(
                onset_envelope=oenv, sr=sr, hop_length=hop_length
            )
        except Exception as exc:
            logger.warning(
                "ChorusDetector[librosa]: tempogram computation failed: %s", exc
            )
            tempogram = None

        # Вычисляем признаки для каждого сегмента
        raw_features: list[dict] = []
        for i, (start, end) in enumerate(segments):
            f_start = int(start * frames_per_sec)
            f_end = int(end * frames_per_sec)

            # sim_score
            sim_score = 0.0
            if sim_matrix is not None:
                fs = max(0, min(f_start, sim_matrix.shape[0] - 1))
                fe = max(fs + 1, min(f_end, sim_matrix.shape[0]))
                sim_score = float(np.mean(sim_matrix[fs:fe, :]))

            # hpss_score
            hpss_score = 0.0
            if rms_full is not None:
                h_start = int(start * sr)
                h_end = int(end * sr)
                h_start = max(0, min(h_start, len(rms_full) - 1))
                h_end = max(h_start + 1, min(h_end, len(rms_full)))
                hpss_score = float(np.mean(rms_full[h_start:h_end]))

            # tempo_score (ритмическая стабильность)
            tempo_score = 0.0
            if tempogram is not None:
                fs = max(0, min(f_start, tempogram.shape[1] - 1))
                fe = max(fs + 1, min(f_end, tempogram.shape[1]))
                seg_tempogram = tempogram[:, fs:fe]
                dominant_idx = np.argmax(np.mean(seg_tempogram, axis=1))
                tempo_series = seg_tempogram[dominant_idx, :]
                if len(tempo_series) > 1:
                    std = float(np.std(tempo_series))
                    mean = float(np.mean(tempo_series))
                    cv = std / (mean + 1e-8)
                    tempo_score = max(0.0, 1.0 - cv)

            # chroma_variance
            chroma_variance = 0.0
            if chroma is not None:
                fs = max(0, min(f_start, chroma.shape[1] - 1))
                fe = max(fs + 1, min(f_end, chroma.shape[1]))
                seg_chroma = chroma[:, fs:fe]
                chroma_variance = float(np.mean(np.var(seg_chroma, axis=1)))

            raw_features.append({
                "sim_score": sim_score,
                "hpss_score": hpss_score,
                "tempo_score": tempo_score,
                "vocal_energy": vocal_energy_list[i] if i < len(vocal_energy_list) else 1.0,
                "chroma_variance": chroma_variance,
            })

        # Нормализуем tempo_score в [0, 1] по всем сегментам
        tempo_scores = [f["tempo_score"] for f in raw_features]
        max_tempo = max(tempo_scores) if tempo_scores else 0.0
        if max_tempo > 0:
            for f in raw_features:
                f["tempo_score"] = f["tempo_score"] / max_tempo

        # Нормализуем chroma_variance в [0, 1]
        chroma_vars = [f["chroma_variance"] for f in raw_features]
        max_chroma_var = max(chroma_vars) if chroma_vars else 0.0
        if max_chroma_var > 0:
            for f in raw_features:
                f["chroma_variance"] = f["chroma_variance"] / max_chroma_var

        logger.debug(
            "ChorusDetector[librosa]: enriched %d segments with features",
            len(raw_features),
        )
        return raw_features

    # ------------------------------------------------------------------
    # Private: segment classification
    # ------------------------------------------------------------------

    def _classify_segment(
        self,
        features: dict,
        segment_index: int,
        total_segments: int,
        all_features: list[dict],
        has_vocal_data: bool = True,
    ) -> str:
        """Классифицировать сегмент по типу.

        Правила классификации (в порядке приоритета):

        1. ``vocal_energy < vocal_silence_threshold`` → ``"instrumental"``
           (только если есть данные о вокале)
        2. ``segment_index == 0`` AND ``duration < 60 сек`` → ``"intro"``
        3. ``segment_index == total_segments - 1`` AND ``duration < 60 сек`` → ``"outro"``
        4. ``sim_score > median + 0.1`` AND ``hpss_score > median`` → ``"chorus"``
        5. ``sim_score < median - 0.1`` AND ``vocal_energy > threshold`` → ``"verse"``
        6. ``tempo_score < median - 0.2`` AND ``vocal_energy > threshold`` → ``"bridge"``
        7. иначе → ``"verse"`` (fallback)

        Parameters
        ----------
        features:
            Словарь признаков для данного сегмента.
        segment_index:
            Индекс сегмента в списке.
        total_segments:
            Общее количество сегментов.
        all_features:
            Список признаков всех сегментов (для вычисления медиан).
        has_vocal_data:
            Флаг наличия данных о вокале (если False — правило 1 не применяется).

        Returns
        -------
        str
            Тип сегмента.
        """
        vocal_energy = features.get("vocal_energy", 1.0)
        sim_score = features.get("sim_score", 0.0)
        hpss_score = features.get("hpss_score", 0.0)
        tempo_score = features.get("tempo_score", 0.0)

        # Вычисляем медианы по всем сегментам
        sim_scores = [f.get("sim_score", 0.0) for f in all_features]
        hpss_scores = [f.get("hpss_score", 0.0) for f in all_features]
        tempo_scores = [f.get("tempo_score", 0.0) for f in all_features]

        median_sim = float(np.median(sim_scores)) if sim_scores else 0.0
        median_hpss = float(np.median(hpss_scores)) if hpss_scores else 0.0
        median_tempo = float(np.median(tempo_scores)) if tempo_scores else 0.0

        threshold = self._vocal_silence_threshold

        # Правило 1: инструментал (только если есть данные о вокале)
        if has_vocal_data and vocal_energy < threshold:
            return "instrumental"

        # Правило 2: интро (первый сегмент)
        if segment_index == 0:
            return "intro"

        # Правило 3: аутро (последний сегмент)
        if segment_index == total_segments - 1:
            return "outro"

        # Правило 4: припев
        if sim_score > median_sim + 0.1 and hpss_score > median_hpss:
            return "chorus"

        # Правило 5: куплет
        if sim_score < median_sim - 0.1 and vocal_energy > threshold:
            return "verse"

        # Правило 6: бридж
        if tempo_score < median_tempo - 0.2 and vocal_energy > threshold:
            return "bridge"

        # Правило 7: fallback
        return "verse"

    def merge_segments(
        self,
        segments: list[VolumeSegment],
        should_merge: Callable[[VolumeSegment, VolumeSegment], bool],
    ) -> list[VolumeSegment]:
        """Универсальный метод объединения соседних сегментов.
        
        Проходим по сегментам слева направо, группируя соседние,
        для которых should_merge(prev, current) возвращает True.
        
        Алгоритм объединения (единый для всех случаев):
        - start = start первого сегмента в группе
        - end = end последнего сегмента в группе
        - segment_type = тип первого сегмента
        - backend = backend первого сегмента
        - scores = конкатенация всех scores в хронологическом порядке
        - id = id первого сегмента
        - volume = вычисляется по типу через _get_volume_for_segment_type
          с использованием self._chorus_volume и self._default_volume
          
        Parameters
        ----------
        segments:
            Исходный список сегментов (будет отсортирован по start).
        should_merge:
            Функция (prev, current) -> bool, определяющая объединение.
            
        Returns
        -------
        list[VolumeSegment]
            Список объединённых сегментов.
        """
        if not segments:
            return []
        
        # Сортируем по start
        sorted_segs = sorted(segments, key=lambda s: s.start)
        
        groups: list[list[VolumeSegment]] = []
        current_group: list[VolumeSegment] = [sorted_segs[0]]
        
        for seg in sorted_segs[1:]:
            prev = current_group[-1]
            if should_merge(prev, seg):
                current_group.append(seg)
            else:
                groups.append(current_group)
                current_group = [seg]
        
        if current_group:
            groups.append(current_group)
        
        # Создаём VolumeSegment для каждой группы
        result: list[VolumeSegment] = []
        for group in groups:
            merged = self._combine_group(group)
            result.append(merged)
        
        return result

    def _combine_group(
        self,
        segs: list[VolumeSegment],
    ) -> VolumeSegment:
        """Объединить группу сегментов в один."""
        if not segs:
            raise ValueError("Cannot combine empty group")
        if len(segs) == 1:
            return segs[0]
        
        first = segs[0]
        last = segs[-1]
        segment_type = first.segment_type or "unknown"
        
        # Вычисляем volume по типу через _get_volume_for_segment_type
        volume = _get_volume_for_segment_type(
            segment_type, self._chorus_volume, self._default_volume
        )
        
        # Конкатенируем все scores
        all_scores: list[SegmentScore] = []
        for seg in segs:
            all_scores.extend(seg.scores)
        
        return VolumeSegment(
            start=first.start,
            end=last.end,
            volume=volume,
            segment_type=segment_type,
            backend=first.backend,
            scores=all_scores,
            id=first.id,
        )

    def should_merge_short(
        self,
        prev: VolumeSegment,
        current: VolumeSegment,
    ) -> bool:
        """Предикат для объединения коротких сегментов.
        
        Объединяем, если ТЕКУЩИЙ сегмент короче min_duration.
        Использует self._min_duration из настроек ChorusDetector.
        """
        return (current.end - current.start) < self._min_duration


from collections.abc import Callable


def should_merge_same_type(
    prev: VolumeSegment,
    current: VolumeSegment,
) -> bool:
    """Предикат для объединения сегментов одинакового типа."""
    prev_type = prev.segment_type or "unknown"
    curr_type = current.segment_type or "unknown"
    return prev_type == curr_type
