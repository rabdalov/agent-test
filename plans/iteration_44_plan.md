# Итерация 44: Команда /change для изменения типа сегмента

## Краткое описание

Реализация команды `/change` для ручного изменения типа сегментов в `volume_segments_file`. Пользователь указывает диапазон сегментов, бот предлагает выбрать новый тип (chorus, verse, instrumental), после чего обновляет JSON-файл и предлагает запустить пересчёт с шага MIX_AUDIO.

## Требования

### Функциональные

1. **Команда `/change <диапазон>`**:
   - Формат: `/change 7,8` или `/change 3,4,8-10` или `/change 5-7,9,12`
   - Работает с последним активным треком пользователя (аналогично `/continue`)
   - Требует наличия `volume_segments_file` в состоянии трека

2. **Парсинг диапазона**:
   - Поддержка перечисления: `1,3,5`
   - Поддержка диапазонов: `5-10`
   - Поддержка смешанного формата: `1,3,5-7,9`
   - Валидация: сегменты должны существовать в `volume_segments_file`

3. **Выбор типа сегмента**:
   - Доступные типы: `chorus`, `verse`, `instrumental`
   - Inline-кнопки с типами сегментов
   - Callback-данные: `change_type:<type>:<segment_ids>`

4. **Обновление сегментов**:
   - Изменение `segment_type` для указанных сегментов
   - Автоматический пересчёт `volume` на основе нового типа:
     - `chorus` → `CHORUS_BACKVOCAL_VOLUME`
     - `verse` / `instrumental` → `AUDIO_MIX_VOICE_VOLUME`
   - Сохранение обновлённого JSON в `volume_segments_file`

5. **Пост-обновление**:
   - Сообщение: "✅ Сегменты #{ids} изменены на {type}. Volume обновлён."
   - Inline-кнопка "🔄 Пересчитать" (callback: `change_recalc`)
   - При нажатии — запуск пайплайна с шага `MIX_AUDIO`

### Нефункциональные

- Использование FSM для хранения контекста изменений (список сегментов, track_id)
- Явная обработка ошибок с понятными сообщениями пользователю
- Логирование всех операций изменения типов сегментов

## Архитектура

### Новые компоненты

```
app/
├── segment_change_service.py    # Сервис изменения типов сегментов
│   └── class SegmentChangeService
│       ├── parse_segment_range(range_str: str) -> list[int]
│       ├── validate_segments(segment_ids: list[int], segments: list[VolumeSegment]) -> bool
│       ├── update_segment_types(segment_ids: list[int], new_type: str, segments: list[VolumeSegment], chorus_volume: float, default_volume: float) -> list[VolumeSegment]
│       └── save_segments(segments: list[VolumeSegment], output_path: Path) -> None
```

### Изменения в существующих файлах

1. **app/models.py**:
   - Новый FSM: `SegmentChangeStates` с состоянием `waiting_for_type_selection`

2. **app/handlers_karaoke.py**:
   - Новый обработчик: `/change` command handler
   - Callback handler: `change_type:<type>:<ids>`
   - Callback handler: `change_recalc`
   - Вспомогательный метод: `_run_mix_audio_step()`

3. **app/config.py** (опционально):
   - Валидация настроек `CHORUS_BACKVOCAL_VOLUME` и `AUDIO_MIX_VOICE_VOLUME`

4. **docs/bot_commands.md**:
   - Документация новой команды `/change`

## План реализации

### Шаг 1: Создание сервиса изменения сегментов

**Файл:** `app/segment_change_service.py`

```python
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
        
        Returns:
            Отсортированный список уникальных ID сегментов.
            
        Raises:
            ValueError: если формат некорректен.
        """
        # Реализация парсинга
        ...
    
    def validate_segments(
        self, 
        segment_ids: list[int], 
        segments: list[VolumeSegment]
    ) -> tuple[bool, str]:
        """Проверяет, существуют ли указанные сегменты.
        
        Returns:
            (is_valid, error_message)
        """
        # Реализация валидации
        ...
    
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
        # Реализация обновления
        ...
    
    def get_volume_for_type(self, segment_type: str) -> float:
        """Возвращает volume для указанного типа сегмента."""
        if segment_type == "chorus":
            return self._chorus_volume
        return self._default_volume
```

### Шаг 2: Добавление FSM-состояний

**Файл:** `app/models.py`

```python
class SegmentChangeStates(StatesGroup):
    """FSM для процесса изменения типа сегмента."""
    waiting_for_type_selection = State()
```

### Шаг 3: Реализация обработчиков в handlers_karaoke.py

**Обработчик команды `/change`:**

```python
@self.router.message(Command("change"))
async def handle_change(message: types.Message, state: FSMContext) -> None:
    """Обработчик команды /change <диапазон>."""
    # 1. Проверка доступа
    # 2. Получение аргумента (диапазон сегментов)
    # 3. Поиск последнего трека пользователя
    # 4. Проверка наличия volume_segments_file
    # 5. Парсинг диапазона через SegmentChangeService
    # 6. Валидация сегментов
    # 7. Сохранение в FSM: track_id, track_folder, segment_ids
    # 8. Отправка inline-клавиатуры с типами сегментов
    # 9. Переход в состояние waiting_for_type_selection
```

**Callback-обработчик выбора типа:**

```python
@self.router.callback_query(
    SegmentChangeStates.waiting_for_type_selection,
    F.data.startswith("change_type:")
)
async def handle_change_type_selection(
    callback: types.CallbackQuery, 
    state: FSMContext
) -> None:
    """Обработчик выбора типа сегмента."""
    # 1. Извлечение данных из callback: type, segment_ids
    # 2. Получение данных из FSM: track_folder, segment_ids
    # 3. Загрузка volume_segments_file
    # 4. Обновление типов через SegmentChangeService
    # 5. Сохранение обновлённого JSON
    # 6. Очистка FSM
    # 7. Отправка сообщения с кнопкой "Пересчитать"
```

**Callback-обработчик пересчёта:**

```python
@self.router.callback_query(F.data == "change_recalc")
async def handle_change_recalc(
    callback: types.CallbackQuery, 
    state: FSMContext
) -> None:
    """Запускает пайплайн с шага MIX_AUDIO."""
    # 1. Получение последнего трека пользователя
    # 2. Загрузка PipelineState
    # 3. Запуск _run_from_step с шага MIX_AUDIO
```

### Шаг 4: Интеграция с существующей логикой

Метод `_run_from_step` в `handlers_karaoke.py` уже существует и поддерживает запуск с любого шага. Используем его для запуска с `MIX_AUDIO`.

### Шаг 5: Обновление документации

**Файл:** `docs/bot_commands.md`

Добавить:
```markdown
change - Изменение типа сегментов в разметке громкости
```

И раздел с описанием использования команды.

## Тестовые сценарии

### Сценарий 1: Простое изменение одного сегмента

1. Пользователь отправляет `/change 5`
2. Бот показывает кнопки: chorus, verse, instrumental
3. Пользователь нажимает "chorus"
4. Бот: "✅ Сегмент #5 изменён на chorus. Volume обновлён."
5. Показывается кнопка "🔄 Пересчитать"
6. При нажатии запускается пайплайн с MIX_AUDIO

### Сценарий 2: Изменение диапазона

1. Пользователь отправляет `/change 3-5,8`
2. Бот показывает кнопки с типами
3. Пользователь нажимает "verse"
4. Бот: "✅ Сегменты #3-5, #8 изменены на verse. Volume обновлён."
5. Кнопка "🔄 Пересчитать"

### Сценарий 3: Ошибка — нет активного трека

1. Пользователь отправляет `/change 1`
2. Бот: "❌ Нет активного трека. Пожалуйста, начните обработку."

### Сценарий 4: Ошибка — нет volume_segments_file

1. Пользователь отправляет `/change 1` для трека без шага DETECT_CHORUS
2. Бот: "❌ Файл разметки сегментов не найден. Сначала выполните /step_chorus."

### Сценарий 5: Ошибка — неверный диапазон

1. Пользователь отправляет `/change abc`
2. Бот: "❌ Некорректный формат диапазона. Используйте: /change 1,2,3 или /change 5-10"

### Сценарий 6: Ошибка — сегмент не существует

1. Пользователь отправляет `/change 100` для трека с 10 сегментами
2. Бот: "❌ Сегмент #100 не найден. Доступные сегменты: #1-10."

## Логирование

```
INFO: User {user_id} initiated /change for segments {range} on track {track_id}
INFO: Segment types updated: {ids} -> {type}, volumes recalculated
INFO: User {user_id} triggered recalculation from MIX_AUDIO for track {track_id}
```

## Зависимости

- Существующие: `VolumeSegment`, `load_volume_segments`, `save_volume_segments` из `app/chorus_detector.py`
- Существующие: `PipelineState`, FSM-механизм из `app/models.py`
- Существующие: `_run_from_step` из `app/handlers_karaoke.py`


