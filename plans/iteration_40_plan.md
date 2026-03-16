# Итерация 40: Группировка сегментов по типу

## Задача

Создать метод `group_volume_segments` в классе `VolumeSegment` для группировки соседних сегментов с одинаковым типом. Результаты сохранять в файл `{stem}_segment_groups.json`. Обновить `TrackVisualizer` для отрисовки сегментов по группам.

---

## Текущее состояние

### Входные данные (volume_segments.json)

```json
[
  {
    "id": 1,
    "start": 0.0,
    "end": 8.684,
    "volume": 0.2,
    "segment_type": "instrumental",
    "backend": "dual_file",
    "scores": {
      "vocal_energy": 0.0001,
      "chroma_variance": 0.4557,
      "sim_score": 0.0102,
      "hpss_score": 0.0587,
      "tempo_score": 1.0
    }
  },
  {
    "id": 2,
    "start": 8.684,
    "end": 27.492,
    "volume": 0.2,
    "segment_type": "instrumental",
    "backend": "dual_file",
    "scores": {
      "vocal_energy": 0.0003,
      "chroma_variance": 0.862,
      "sim_score": 0.0068,
      "hpss_score": 0.1462,
      "tempo_score": 1.0
    }
  }
]
```

### Выходные данные (segment_groups.json)

```json
[
  {
    "id_group": 1,
    "start": 0.0,
    "end": 27.492,
    "volume": 0.2,
    "segment_type": "instrumental",
    "backend": "dual_file",
    "scores": [
      {
        "id": 1,
        "vocal_energy": 0.0001,
        "chroma_variance": 0.4557,
        "sim_score": 0.0102,
        "hpss_score": 0.0587,
        "tempo_score": 1.0
      },
      {
        "id": 2,
        "vocal_energy": 0.0003,
        "chroma_variance": 0.862,
        "sim_score": 0.0068,
        "hpss_score": 0.1462,
        "tempo_score": 1.0
      }
    ]
  }
]
```

---

## План реализации

### Этап 1: Метод group_volume_segments

**Файл:** `app/chorus_detector.py`

Добавить в класс `VolumeSegment`:

```python
@classmethod
def group_volume_segments(
    cls,
    segments: list[VolumeSegment]
) -> list[VolumeSegment]:
    """Группировать соседние сегменты по идентичному типу.
    
    Алгоритм:
    1. Сортировать по start (гарантировано, но на всякий случай)
    2. Пройти по сегментам, группируя соседние с одинаковым segment_type 
    3. Для каждой группы создать новый VolumeSegment с:
       - id_group = порядковый номер группы
       - start = начало первого сегмента
       - end = конец последнего сегмента
       - volume = На основании типа сегмента используя метод `VolumeSegment._get_volume_for_segment_type`
       - segment_type = тип группы
       - backend = из первого сегмента
       - scores = массив словарей {id, ...metrics}
    4. корректно обработать случай, когда объединение сегментов не требуется, но нужно все равно преобраовать формат `scores` в виде массива с одним сегментом   
    """
```

### Этап 2: Функция сохранения групп

**Файл:** `app/chorus_detector.py`

Добавить функцию:

```python
def save_segment_groups(
    groups: list[VolumeSegment],
    output_path: Path,
) -> None:
    """Сохранить группы сегментов в JSON-файл."""
```

### Этап 3: Обновление TrackVisualizer

**Файл:** `app/track_visualizer.py`

1. Обновить `_load_volume_segments` для определения формата:
   - `scores` — dict → старый формат
   - `scores` — list → новый формат (группы)

2. Обновить `_draw_segments_layer`:
   - Рисовать один прямоугольник на группу
   - Отображать диапазон id: "#1-3"
   - отображать наименование группы.
   - Отказаться от отображения цифровых метрик в слое сегментов.

3. Обновить `_draw_metrics_layer`:
   - Графики Метрик должны остаться визуально без изменений, так как метрики должны использовать данные из обновленных `scores`

### Этап 4: Интеграция в пайплайн 

- Вызвать `group_volume_segments` непосредственно самом начале шага `GENERATE_ASS`.
- Сохранить результат: `{track_stem}_segment_groups.json` 
- сохранить путь в переменную `segment_groups_file` в файле `state.json` 
- Передавать путь к группам в `TrackVisualizer`
- Изменить метод `AssGenerator.generate` чтобы всегда корректно поддерживать новый формат `scores` из `segment_groups_file`

---

## Критерии приёмки

1. Метод `group_volume_segments` корректно объединяет соседние сегменты с одинаковым `segment_type`
2. Файл `{stem}_segment_groups.json` создаётся с корректным форматом
3. `TrackVisualizer` корректно отображает группы сегментов и их метрики
4. `AssGenerator` корректно отрисовывает субтитры с группами сегментов
4. Обратная совместимость: НЕ поддерживается.
