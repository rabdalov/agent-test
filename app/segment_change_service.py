"""Сервис для изменения типов сегментов в volume_segments_file."""

import logging
import re
from pathlib import Path
from typing import Any

from .chorus_detector import (
    ChorusDetector,
    FrameFeatures,
    SegmentScore,
    VolumeSegment,
    load_detailed_metrics,
    load_volume_segments,
    save_volume_segments,
)

logger = logging.getLogger(__name__)


class SegmentChangeService:
    """Сервис для изменения типов сегментов и пересчёта volume."""

    ALLOWED_TYPES = ["chorus", "verse", "instrumental"]

    def __init__(self, chorus_volume: float = 0.4, default_volume: float = 0.2) -> None:
        self._chorus_volume = chorus_volume
        self._default_volume = default_volume

    def parse_segment_range(self, range_str: str) -> list[int]:
        """Парсит строку диапазона в список ID сегментов.

        Поддерживаемые форматы:
        - "1,2,3" -> [1, 2, 3]
        - "5-10" -> [5, 6, 7, 8, 9, 10]
        - "1,3,5-7,9" -> [1, 3, 5, 6, 7, 9]

        Args:
            range_str: Строка с диапазоном сегментов

        Returns:
            Отсортированный список уникальных ID сегментов.

        Raises:
            ValueError: если формат некорректен.
        """
        if not range_str or not range_str.strip():
            raise ValueError("Диапазон не может быть пустым")

        range_str = range_str.strip()
        segment_ids: set[int] = set()

        # Разбиваем по запятым
        parts = [p.strip() for p in range_str.split(",")]

        for part in parts:
            if not part:
                continue

            # Проверяем, является ли часть диапазоном (например, "5-10")
            if "-" in part:
                try:
                    start_str, end_str = part.split("-", 1)
                    start = int(start_str.strip())
                    end = int(end_str.strip())
                    if start <= 0 or end <= 0:
                        raise ValueError("ID сегментов должны быть положительными числами")
                    if start > end:
                        start, end = end, start  # Меняем местами
                    for i in range(start, end + 1):
                        segment_ids.add(i)
                except ValueError as exc:
                    raise ValueError(f"Некорректный формат диапазона: '{part}'") from exc
            else:
                # Одиночный ID
                try:
                    segment_id = int(part)
                    if segment_id <= 0:
                        raise ValueError("ID сегмента должен быть положительным числом")
                    segment_ids.add(segment_id)
                except ValueError as exc:
                    raise ValueError(f"Некорректный ID сегмента: '{part}'") from exc

        if not segment_ids:
            raise ValueError("Не удалось распознать ни одного ID сегмента")

        return sorted(segment_ids)

    def validate_segments(
        self,
        segment_ids: list[int],
        segments: list[VolumeSegment],
    ) -> tuple[bool, str]:
        """Проверяет, существуют ли указанные сегменты.

        Args:
            segment_ids: Список ID сегментов для проверки
            segments: Список всех сегментов

        Returns:
            (is_valid, error_message)
        """
        if not segments:
            return False, "Список сегментов пуст"

        # Собираем все существующие ID
        existing_ids: set[int] = set()
        for seg in segments:
            existing_ids.add(seg.id)
            # Также учитываем ID из scores
            for score in seg.scores:
                existing_ids.add(score.id)

        if not existing_ids:
            return False, "Не найдено ни одного сегмента"

        min_id = min(existing_ids)
        max_id = max(existing_ids)

        invalid_ids = [sid for sid in segment_ids if sid not in existing_ids]

        if invalid_ids:
            if len(invalid_ids) == 1:
                return (
                    False,
                    f"Сегмент #{invalid_ids[0]} не найден. Доступные сегменты: #{min_id}-{max_id}."
                )
            else:
                ids_str = ", ".join(f"#{sid}" for sid in invalid_ids)
                return (
                    False,
                    f"Сегменты {ids_str} не найдены. Доступные сегменты: #{min_id}-{max_id}."
                )

        return True, ""

    def update_segment_types(
        self,
        segment_ids: list[int],
        new_type: str,
        segments: list[VolumeSegment],
    ) -> list[VolumeSegment]:
        """Обновляет тип и volume для указанных сегментов.

        Args:
            segment_ids: Список ID сегментов для изменения
            new_type: Новый тип сегмента (chorus/verse/instrumental)
            segments: Список всех сегментов

        Returns:
            Обновлённый список сегментов

        Raises:
            ValueError: если тип недопустим
        """
        if new_type not in self.ALLOWED_TYPES:
            raise ValueError(
                f"Недопустимый тип сегмента: '{new_type}'. "
                f"Допустимые типы: {', '.join(self.ALLOWED_TYPES)}"
            )

        segment_ids_set = set(segment_ids)
        new_volume = self.get_volume_for_type(new_type)

        for seg in segments:
            # Проверяем id самого сегмента
            if seg.id in segment_ids_set:
                seg.segment_type = new_type
                seg.volume = new_volume
                continue

            # Проверяем id в scores
            for score in seg.scores:
                if score.id in segment_ids_set:
                    seg.segment_type = new_type
                    seg.volume = new_volume
                    break

        return segments

    def get_volume_for_type(self, segment_type: str) -> float:
        """Возвращает volume для указанного типа сегмента."""
        if segment_type == "chorus":
            return self._chorus_volume
        return self._default_volume

    def format_segment_ids(self, segment_ids: list[int]) -> str:
        """Форматирует список ID сегментов в читаемую строку.

        Примеры:
        - [1] -> "#1"
        - [1, 2, 3] -> "#1-3"
        - [1, 3, 5] -> "#1, #3, #5"
        - [1, 3, 5, 6, 7] -> "#1, #3, #5-7"
        """
        if not segment_ids:
            return ""

        if len(segment_ids) == 1:
            return f"#{segment_ids[0]}"

        # Группируем последовательные ID
        ranges: list[str] = []
        start = segment_ids[0]
        end = segment_ids[0]

        for seg_id in segment_ids[1:]:
            if seg_id == end + 1:
                end = seg_id
            else:
                if start == end:
                    ranges.append(f"#{start}")
                else:
                    ranges.append(f"#{start}-{end}")
                start = end = seg_id

        # Добавляем последнюю группу
        if start == end:
            ranges.append(f"#{start}")
        else:
            ranges.append(f"#{start}-{end}")

        return ", ".join(ranges)

    # ------------------------------------------------------------------
    # Split segment functionality
    # ------------------------------------------------------------------

    def parse_split_time(self, time_str: str) -> float:
        """Парсит время из форматов m:ss.xx или секунды.

        Поддерживаемые форматы:
        - "1:10.5" -> 70.5 (минуты:секунды.миллисекунды)
        - "1:10" -> 70 (минуты:секунды)
        - "70.5" -> 70.5 (секунды)
        - "70" -> 70 (секунды)

        Args:
            time_str: Строка с временем

        Returns:
            Время в секундах (float)

        Raises:
            ValueError: если формат некорректен
        """
        time_str = time_str.strip()

        # Формат m:ss или m:ss.xx
        if ":" in time_str:
            parts = time_str.split(":", 1)
            try:
                minutes = float(parts[0].strip())
                seconds = float(parts[1].strip())
                return minutes * 60 + seconds
            except ValueError as exc:
                raise ValueError(f"Некорректный формат времени: '{time_str}'") from exc

        # Формат секунд (float)
        try:
            return float(time_str)
        except ValueError as exc:
            raise ValueError(f"Некорректный формат времени: '{time_str}'") from exc

    def find_segment_by_time(
        self,
        split_time: float,
        segments: list[VolumeSegment],
    ) -> tuple[int, VolumeSegment] | None:
        """Находит сегмент, содержащий указанное время.

        Args:
            split_time: Время в секундах
            segments: Список всех сегментов

        Returns:
            Кортеж (индекс, сегмент) или None если не найден
        """
        for idx, seg in enumerate(segments):
            if seg.start <= split_time <= seg.end:
                return idx, seg
        return None

    def split_segment(
        self,
        segment_index: int,
        split_time: float,
        segments: list[VolumeSegment],
        track_source: str | None = None,
        vocal_file: str | None = None,
        detailed_metrics_path: Path | None = None,
    ) -> tuple[list[VolumeSegment], VolumeSegment, VolumeSegment]:
        """Разделяет сегмент на два подсегмента с пересчётом метрик.

        Args:
            segment_index: Индекс сегмента для разделения
            split_time: Точка разделения в секундах
            segments: Список всех сегментов
            track_source: Путь к основному аудиофайлу (для пересчёта метрик)
            vocal_file: Путь к файлу вокала (для пересчёта метрик)
            detailed_metrics_path: Путь к файлу детальных метрик (fallback)

        Returns:
            Кортеж (обновлённый список сегментов, сегмент1, сегмент2)

        Raises:
            ValueError: если не удалось разделить или пересчитать метрики
        """
        if segment_index < 0 or segment_index >= len(segments):
            raise ValueError(f"Некорректный индекс сегмента: {segment_index}")

        original_seg = segments[segment_index]
        original_type = original_seg.segment_type or "verse"
        original_backend = original_seg.backend
        original_scores = original_seg.scores.copy() if original_seg.scores else []

        # Получаем volume для типа
        volume = self.get_volume_for_type(original_type)

        # Определяем границы подсегментов
        seg1_start = original_seg.start
        seg1_end = split_time
        seg2_start = split_time
        seg2_end = original_seg.end

        # Пытаемся пересчитать метрики через ChorusDetector
        seg1_scores: list[SegmentScore] = []
        seg2_scores: list[SegmentScore] = []
        metrics_recalculated = False

        try:
            # Создаём ChorusDetector для доступа к методам агрегации
            detector = ChorusDetector(
                chorus_volume=self._chorus_volume,
                default_volume=self._default_volume,
            )

            # Извлекаем frame-level признаки если есть файлы
            frame_features: FrameFeatures | None = None
            if track_source and Path(track_source).exists():
                frame_features = detector._extract_frame_features(track_source, vocal_file)

            if frame_features is not None:
                # Агрегируем метрики для подсегментов
                subsegments = [(seg1_start, seg1_end), (seg2_start, seg2_end)]
                features_list = detector._aggregate_segment_features(
                    frame_features, subsegments
                )

                # Создаём SegmentScore для каждого подсегмента
                seg1_scores = [SegmentScore(
                    id=0,  # Будет присвоен позже
                    start=seg1_start,
                    end=seg1_end,
                    vocal_energy=float(features_list[0].get("vocal_energy", 0.5)),
                    chroma_variance=float(features_list[0].get("chroma_variance", 0.0)),
                    sim_score=float(features_list[0].get("sim_score", 0.0)),
                    hpss_score=float(features_list[0].get("hpss_score", 0.0)),
                    tempo_score=float(features_list[0].get("tempo_score", 0.0)),
                )]

                seg2_scores = [SegmentScore(
                    id=0,  # Будет присвоен позже
                    start=seg2_start,
                    end=seg2_end,
                    vocal_energy=float(features_list[1].get("vocal_energy", 0.5)),
                    chroma_variance=float(features_list[1].get("chroma_variance", 0.0)),
                    sim_score=float(features_list[1].get("sim_score", 0.0)),
                    hpss_score=float(features_list[1].get("hpss_score", 0.0)),
                    tempo_score=float(features_list[1].get("tempo_score", 0.0)),
                )]
                metrics_recalculated = True
            else:
                logger.warning("Frame feature extraction failed, using fallback metrics")

        except Exception as exc:
            logger.warning("Failed to recalculate metrics using ChorusDetector: %s", exc)

        # Fallback: интерполяция из detailed_metrics или копирование
        if not metrics_recalculated:
            seg1_scores, seg2_scores = self._interpolate_metrics(
                original_scores,
                seg1_start, seg1_end, seg2_start, seg2_end,
                detailed_metrics_path,
            )

        # Создаём новые сегменты
        seg1 = VolumeSegment(
            start=seg1_start,
            end=seg1_end,
            volume=volume,
            segment_type=original_type,
            backend=original_backend,
            scores=seg1_scores,
            id=0,  # Будет перенумерован
        )

        seg2 = VolumeSegment(
            start=seg2_start,
            end=seg2_end,
            volume=volume,
            segment_type=original_type,
            backend=original_backend,
            scores=seg2_scores,
            id=0,  # Будет перенумерован
        )

        # Формируем новый список сегментов
        new_segments = segments[:segment_index] + [seg1, seg2] + segments[segment_index + 1:]

        # Перенумеровываем все сегменты
        for idx, seg in enumerate(new_segments, start=1):
            seg.id = idx
            if seg.scores:
                seg.scores[0].id = idx

        return new_segments, seg1, seg2

    def _interpolate_metrics(
        self,
        original_scores: list[SegmentScore],
        seg1_start: float,
        seg1_end: float,
        seg2_start: float,
        seg2_end: float,
        detailed_metrics_path: Path | None,
    ) -> tuple[list[SegmentScore], list[SegmentScore]]:
        """Интерполирует метрики для подсегментов.

        Сначала пытается использовать detailed_metrics, затем копирует из original_scores.
        """
        # Пытаемся загрузить детальные метрики
        if detailed_metrics_path and detailed_metrics_path.exists():
            try:
                metrics = load_detailed_metrics(detailed_metrics_path)

                # Фильтруем метрики для каждого подсегмента
                seg1_metrics = [m for m in metrics if seg1_start <= m.time < seg1_end]
                seg2_metrics = [m for m in metrics if seg2_start <= m.time < seg2_end]

                if seg1_metrics and seg2_metrics:
                    def avg(values: list[float]) -> float:
                        return sum(values) / len(values) if values else 0.0

                    seg1_score = SegmentScore(
                        id=0,
                        start=seg1_start,
                        end=seg1_end,
                        vocal_energy=avg([m.vocal_energy for m in seg1_metrics]),
                        chroma_variance=avg([m.chroma_variance for m in seg1_metrics]),
                        hpss_score=avg([m.hpss_score for m in seg1_metrics]),
                    )

                    seg2_score = SegmentScore(
                        id=0,
                        start=seg2_start,
                        end=seg2_end,
                        vocal_energy=avg([m.vocal_energy for m in seg2_metrics]),
                        chroma_variance=avg([m.chroma_variance for m in seg2_metrics]),
                        hpss_score=avg([m.hpss_score for m in seg2_metrics]),
                    )

                    return [seg1_score], [seg2_score]
            except Exception as exc:
                logger.warning("Failed to interpolate from detailed_metrics: %s", exc)

        # Fallback: копируем метрики из original_scores
        if original_scores:
            original = original_scores[0]
            seg1_score = SegmentScore(
                id=0,
                start=seg1_start,
                end=seg1_end,
                vocal_energy=original.vocal_energy,
                chroma_variance=original.chroma_variance,
                sim_score=original.sim_score,
                hpss_score=original.hpss_score,
                tempo_score=original.tempo_score,
            )
            seg2_score = SegmentScore(
                id=0,
                start=seg2_start,
                end=seg2_end,
                vocal_energy=original.vocal_energy,
                chroma_variance=original.chroma_variance,
                sim_score=original.sim_score,
                hpss_score=original.hpss_score,
                tempo_score=original.tempo_score,
            )
            return [seg1_score], [seg2_score]

        # Нет метрик — создаём пустые
        return (
            [SegmentScore(id=0, start=seg1_start, end=seg1_end)],
            [SegmentScore(id=0, start=seg2_start, end=seg2_end)],
        )

    def format_time(self, seconds: float) -> str:
        """Форматирует время в читаемый формат m:ss или m:ss.xx."""
        minutes = int(seconds // 60)
        secs = seconds % 60
        if secs == int(secs):
            return f"{minutes}:{int(secs):02d}"
        return f"{minutes}:{secs:05.2f}"