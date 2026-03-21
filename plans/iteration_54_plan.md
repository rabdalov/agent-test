# Итерация 54 — Команда /split для разделения сегмента на два подсегмента

## Цель
Реализовать команду `/split` для ручного разделения сегмента на два подсегмента по указанной временной границе с сохранением типа и пересчётом метрик.

## Контекст

### Существующая инфраструктура
- Команда `/change` (итерация 44) реализована в [`SegmentChangeService`](app/segment_change_service.py:13) и [`KaraokeHandlers`](app/handlers_karaoke.py:38)
- FSM-состояние [`SegmentChangeStates`](app/models.py:126) используется для выбора типа сегмента
- Файл сегментов ([`volume_segments_file`](app/models.py:81)) содержит список [`VolumeSegment`](app/chorus_detector.py:165)
- Каждый [`VolumeSegment`](app/chorus_detector.py:165) содержит список [`SegmentScore`](app/chorus_detector.py:28) с полями `start`, `end`, метриками
- Детальные метрики ([`detailed_metrics_file`](app/models.py:89)) содержат 1-секундные точки [`MetricsPoint`](app/chorus_detector.py:93)

### Архитектурные решения (уточнённые)
1. **Метрики новых сегментов**: пересчитываются через [`ChorusDetector._aggregate_segment_features()`](app/chorus_detector.py:1118) на основе frame-level признаков
2. **Границы подсегментов**: при разделении сегмента создаём два новых [`SegmentScore`](app/chorus_detector.py:28) с обновлёнными границами
3. **Тип новых сегментов**: оба подсегмента сохраняют тип исходного сегмента
4. **Пересчёт id**: после разделения все сегменты перенумеровываются по порядку
5. **Архитектура**: метод добавляется в существующий [`SegmentChangeService`](app/segment_change_service.py:13)

## Функциональные требования

### 1. Парсинг времени
Поддерживаемые форматы:
- `/split 1:10.5` — минуты:секунды.миллисекунды
- `/split 83.3` — секунды (float)

Валидация:
- Время должно быть внутри какого-либо сегмента (не на границе)
- Минимальная длительность подсегмента — 1 секунда с каждой стороны

### 2. Разделение сегмента
Для сегмента с `start`, `end` и точкой разделения `split_time`:
- Подсегмент 1: `start` → `split_time`
- Подсегмент 2: `split_time` → `end`

Обработка [`SegmentScore`](app/chorus_detector.py:28):
- Пересчитываем метрики для каждого подсегмента через frame-level признаки
- Создаём новые [`SegmentScore`](app/chorus_detector.py:28) с обновлёнными `start`/`end`

### 3. Формат ответа
```
Было:
`1:00 - 1:34 (34 сек) Verse`

Стало:
`1:00 - 1:15 (15 сек) Verse`
`1:15 - 1:34 (19 сек) Verse`
```

### 4. Кнопки действий
После успешного разделения:
- `🔄 Пересчитать` — запуск пайплайна с шага MIX_AUDIO
- `📊 Показать` — генерация визуализации и отправка через `handle_step_visualize`

## Техническая реализация

### 1. Модификация SegmentChangeService

Добавить методы:

```python
def parse_split_time(self, time_str: str) -> float:
    """Парсит время из форматов m:ss.xx или секунды."""
    # Поддержка: 1:10.5, 1:10, 70.5, 70
    
def find_segment_by_time(
    self, 
    split_time: float, 
    segments: list[VolumeSegment]
) -> tuple[int, VolumeSegment] | None:
    """Находит сегмент, содержащий указанное время."""
    
def split_segment(
    self,
    segment_index: int,
    split_time: float,
    segments: list[VolumeSegment],
    track_source: str | None = None,
    vocal_file: str | None = None,
) -> list[VolumeSegment]:
    """Разделяет сегмент на два подсегмента с пересчётом метрик."""
    # 1. Извлекаем frame-level признаки через ChorusDetector
    # 2. Агрегируем метрики для каждого подсегмента
    # 3. Создаём два новых VolumeSegment
    # 4. Перенумеровываем id всех сегментов
```

### 2. Модификация handlers_karaoke.py

Добавить обработчик:

```python
@self.router.message(Command("split"))
async def handle_split(message: types.Message, state: FSMContext) -> None:
    """Обработчик команды /split <время>."""
    # 1. Парсинг времени
    # 2. Поиск активного трека
    # 3. Загрузка сегментов
    # 4. Валидация времени (внутри сегмента, мин. 1с с каждой стороны)
    # 5. Вызов SegmentChangeService.split_segment()
    # 6. Сохранение результата
    # 7. Отправка сообщения с кнопками
```

### 3. Пересчёт метрик

Используем существующий механизм:

```python
# Создаём ChorusDetector для доступа к методам агрегации
detector = ChorusDetector(
    chorus_volume=self._chorus_volume,
    default_volume=self._default_volume,
)

# Извлекаем frame-level признаки (если есть файлы)
frame_features = detector._extract_frame_features(track_source, vocal_file)

# Агрегируем метрики для подсегментов
features_list = detector._aggregate_segment_features(
    frame_features, 
    [(start1, end1), (start2, end2)]
)
```

Если frame-level извлечение невозможно (нет файлов или ошибка):
- Используем интерполяцию из [`detailed_metrics_file`](app/models.py:89)
- Fallback: копируем метрики исходного сегмента

## План реализации

### Шаг 1: Расширить SegmentChangeService
- [ ] Добавить `parse_split_time()` с поддержкой m:ss.xx и float
- [ ] Добавить `find_segment_by_time()` для поиска сегмента
- [ ] Добавить `split_segment()` с пересчётом метрик
- [ ] Добавить вспомогательный метод `_interpolate_metrics()` для fallback

### Шаг 2: Обновить handlers_karaoke.py
- [ ] Добавить обработчик `handle_split()`
- [ ] Добавить callback `handle_split_recalc` (аналогично `change_recalc`)
- [ ] Добавить callback `handle_split_visualize` для кнопки "Показать"

### Шаг 3: Обновить models.py (при необходимости)
- [ ] Добавить FSM-состояние `SegmentSplitStates` если требуется многошаговый ввод

### Шаг 4: Интеграция с group_volume_segments
- [ ] После разделения вызывать [`group_volume_segments()`](app/chorus_detector.py:1551) для обновления [`segment_groups_file`](app/models.py:82)

## Обработка ошибок

| Ошибка | Сообщение пользователю |
|--------|------------------------|
| Неверный формат времени | `❌ Неверный формат времени. Используйте: /split 1:10.5 или /split 70.5` |
| Время вне всех сегментов | `❌ Указанное время не попадает ни в один сегмент. Доступный диапазон: 0:00 - 3:45` |
| Время на границе сегмента | `❌ Точка разделения слишком близка к границе сегмента. Минимум 1 секунда от края.` |
| Сегмент слишком короткий | `❌ Сегмент слишком короткий для разделения (минимум 3 секунды).` |
| Ошибка пересчёта метрик | `⚠️ Разделение выполнено, но метрики могут быть неточными.` |

## Согласование с vision.md

- **Простота**: Минимум абстракций, расширение существующего класса
- **1 класс = 1 файл**: Логика в `SegmentChangeService`, хендлеры в `handlers_karaoke.py`
- **Три слоя**: Telegram-интерфейс → доменный сервис → интеграция с `ChorusDetector`
- **Python 3.12 + типизация**: Полная аннотация типов
- **Асинхронность**: Хендлеры async, сервисные методы синхронные (как в `/change`)

## Тестирование

1. **Успешное разделение**: `/split 1:15` для сегмента 1:00-1:34
2. **Граничные случаи**: время на границе, вне диапазона
3. **Форматы времени**: `70.5`, `1:10.5`, `1:10`
4. **Пересчёт**: кнопка "Пересчитать" запускает pipeline с MIX_AUDIO
5. **Визуализация**: кнопка "Показать" генерирует и отправляет PNG

## Зависимости

- [`SegmentChangeService`](app/segment_change_service.py:13) — расширение
- [`ChorusDetector`](app/chorus_detector.py:679) — пересчёт метрик
- [`TrackVisualizer`](app/track_visualizer.py:118) — генерация визуализации
- [`handle_step_visualize`](app/handlers_karaoke.py:355) — отправка визуализации
