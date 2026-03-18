# Итерация 47: Исправление отрисовки подсегментов по фактическим границам

## Проблема

Метод `_draw_segments_layer` в `app/track_visualizer.py` некорректно расчитывает и отрисовывает ширину подсегментов: сейчас используется усреднение (`seg_duration / sub_count`), а должны отрисовываться с фактической шириной на основании временных границ подсегментов.

## Цель

Добавить сохранение фактических временных границ (`start`/`end`) для каждого подсегмента в `SegmentScore` и использовать их при отрисовке в TrackVisualizer.

## Изменения

### 1. Расширение SegmentScore (app/chorus_Мdetector.py)

**Текущая структура:**
```python
@dataclass
class SegmentScore:
    id: int
    vocal_energy: float = 0.0
    chroma_variance: float = 0.0
    sim_score: float = 0.0
    hpss_score: float = 0.0
    tempo_score: float = 0.0
```

**Новая структура:**
```python
@dataclass
class SegmentScore:
    id: int
    start: float = 0.0  # ← НОВОЕ: начало подсегмента
    end: float = 0.0    # ← НОВОЕ: конец подсегмента
    vocal_energy: float = 0.0
    chroma_variance: float = 0.0
    sim_score: float = 0.0
    hpss_score: float = 0.0
    tempo_score: float = 0.0
```

**Обновить методы:**
- `to_dict()` — добавить сериализацию `start`/`end`
- `from_dict()` — добавить десериализацию `start`/`end` с fallback на 0.0

### 2. Сохранение границ при создании VolumeSegment (app/chorus_detector.py)

В функции `build_volume_segments()` при создании `SegmentScore` (примерно строка 480) добавить передачу временных границ:

```python
scores_list = [SegmentScore(
    id=0,  # Присваивается позже
    start=info.start,  # ← НОВОЕ
    end=info.end,      # ← НОВОЕ
    vocal_energy=float(info.scores.get("vocal_energy", 1.0)),
    chroma_variance=float(info.scores.get("chroma_variance", 0.0)),
    sim_score=float(info.scores.get("sim_score", 0.0)),
    hpss_score=float(info.scores.get("hpss_score", 0.0)),
    tempo_score=float(info.scores.get("tempo_score", 0.0)),
)]
```

### 3. Исправление отрисовки в _draw_segments_layer (app/track_visualizer.py)

**Текущий код (строки ~940-947):**
```python
seg_duration = end - start
sub_count = len(scores)
sub_width = seg_duration / sub_count
for i, sub_score in enumerate(scores):
    sub_id = sub_score.get("id", i + 1)
    sub_start = start + i * sub_width
    sub_end = sub_start + sub_width
```

**Новый код:**
```python
for sub_score in scores:
    sub_id = sub_score.get("id", 0)
    # Используем фактические границы, fallback на равномерное распределение
    sub_start = float(sub_score.get("start", 0.0))
    sub_end = float(sub_score.get("end", 0.0))
    
    # Fallback для старых файлов без start/end
    if sub_start == 0.0 and sub_end == 0.0:
        seg_duration = end - start
        sub_count = len(scores)
        sub_width = seg_duration / sub_count
        idx = scores.index(sub_score)
        sub_start = start + idx * sub_width
        sub_end = sub_start + sub_width
    
    sub_width = sub_end - sub_start
```

### 4. Обновление отрисовки метрик в _draw_metrics_layer (app/track_visualizer.py)

**Текущий код (строки ~1181-1192):**
```python
seg_duration = end - start
sub_duration = seg_duration / len(scores)
for i, sub_score in enumerate(scores):
    sub_start = start + i * sub_duration
    sub_end = sub_start + sub_duration
```

**Новый код:**
```python
for sub_score in scores:
    sub_start = float(sub_score.get("start", 0.0))
    sub_end = float(sub_score.get("end", 0.0))
    
    # Fallback для старых файлов
    if sub_start == 0.0 and sub_end == 0.0:
        seg_duration = end - start
        sub_duration = seg_duration / len(scores)
        idx = scores.index(sub_score)
        sub_start = start + idx * sub_duration
        sub_end = sub_start + sub_duration
```

## Обратная совместимость

- Поля `start`/`end` в `SegmentScore` опциональные (default=0.0)
- При отсутствии границ в JSON используется fallback на равномерное распределение
- Старые файлы продолжают работать без перегенерации

## Файлы для изменения

1. `app/chorus_detector.py` — добавление полей в SegmentScore, обновление to_dict/from_dict, build_volume_segments
2. `app/track_visualizer.py` — исправление _draw_segments_layer и _draw_metrics_layer

## Тестирование

1. Сгенерировать новый трек и проверить, что в `volume_segments_file` появились поля `start`/`end` внутри `scores`
2. Проверить визуализацию — подсегменты должны отрисовываться с фактической шириной
3. Проверить старый файл без `start`/`end` — должен работать fallback на равномерное распределение

## Критерии завершения

- [ ] Поля `start`/`end` добавлены в `SegmentScore`
- [ ] Границы сохраняются при создании `VolumeSegment`
- [ ] `_draw_segments_layer` использует фактические границы
- [ ] `_draw_metrics_layer` использует фактические границы
- [ ] Обратная совместимость с старыми файлами работает
- [ ] Визуализация корректно отображает подсегменты разной длительности
