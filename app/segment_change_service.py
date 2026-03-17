"""Сервис для изменения типов сегментов в volume_segments_file."""

import logging
import re
from pathlib import Path
from typing import Any

from .chorus_detector import VolumeSegment, save_volume_segments, load_volume_segments

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