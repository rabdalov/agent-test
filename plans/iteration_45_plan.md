# Итерация 45: Countdown в instrumental-сегментах и превью следующей строки

## Цель
Модифицировать [`AssGenerator`](app/ass_generator.py:140) для улучшения UX в karaoke-видео:
1. **Обратный отсчет 3-2-1** в конце instrumental-сегментов (заменяет текст в ActiveLine)
2. **Превью следующей строки** за 3 секунды до окончания instrumental (в слое NextLine)

## Требования UX (утверждены)
- Countdown **заменяет** текст ActiveLine (цифры 3-2-1 вместо слов песни)
- Для instrumental < 3 сек показываются только доступные цифры (например, 2-1 или 1)
- Цифры countdown используют стиль `Highlight` (циановый цвет)
- NextLine показывает текст следующей вокальной строки песни

## Изменения в коде

### 1. Модификация метода `generate()`
**Файл:** [`app/ass_generator.py`](app/ass_generator.py:162)

```python
def generate(
    self,
    aligned_json_path: Path,
    output_ass_path: Path,
    track_title: str = "",
    volume_segments_path: Path | None = None,
) -> None:
```

**Изменения:**
- Загружать `volume_segments_file` (если передан) и вычислять список instrumental-интервалов
- Определить mapping: конец instrumental → следующий вокальный сегмент
- Передавать информацию в `_build_segment_dialogues()`

**Логика определения "следующей строки":**
```python
# Для instrumental-сегмента с end_time=X найти первый вокальный сегмент
# у которого start_time >= X (или ближайший по времени)
next_vocal_segment = find_first_segment_with_start_after(instrumental_end)
```

### 2. Новый метод `_get_instrumental_windows()`
**Файл:** [`app/ass_generator.py`](app/ass_generator.py:140)

```python
def _get_instrumental_windows(
    self,
    volume_segments: list[dict],
    aligned_segments: list[dict],
) -> list[tuple[float, float, dict | None]]:
    """Extract instrumental intervals and their next vocal segments.
    
    Returns list of tuples: (instrumental_start, instrumental_end, next_vocal_segment)
    next_vocal_segment - первый вокальный сегмент после instrumental (или None)
    """
```

**Алгоритм:**
1. Отфильтровать сегменты с `segment_type == "instrumental"`
2. Для каждого instrumental найти `next_segment` из `aligned_segments`:
   - Сегмент с `start_time >= instrumental_end`
   - Если несколько — ближайший по времени
3. Вернуть список `(start, end, next_segment)`

### 3. Модификация `_build_segment_dialogues()`
**Файл:** [`app/ass_generator.py`](app/ass_generator.py:389)

```python
def _build_segment_dialogues(
    self,
    seg: dict,
    next_seg: dict | None,
    instrumental_windows: list[tuple[float, float, dict | None]] | None = None,
) -> list[str]:
```

**Логика проверки countdown:**
```python
# Проверить, заканчивается ли текущий сегмент в instrumental-окне
for inst_start, inst_end, next_vocal in (instrumental_windows or []):
    # Если seg.end == inst_end (или примерно равен с точностью до epsilon)
    if abs(seg["end"] - inst_end) < 0.01 and next_vocal:
        # Генерировать countdown и early NextLine
        countdown_lines = self._build_countdown_dialogues(
            inst_end, next_vocal, seg
        )
        lines.extend(countdown_lines)
```

### 4. Новый метод `_build_countdown_dialogues()`
**Файл:** [`app/ass_generator.py`](app/ass_generator.py:140)

```python
def _build_countdown_dialogues(
    self,
    instrumental_end: float,
    next_vocal_segment: dict,
    current_seg: dict,
) -> list[str]:
    """Build countdown (3-2-1) and early NextLine dialogues.
    
    Args:
        instrumental_end: время окончания instrumental
        next_vocal_segment: следующий вокальный сегмент (для NextLine)
        current_seg: текущий сегмент (для определения доступного времени)
    
    Returns:
        Список Dialogue-строк для countdown и early NextLine
    """
```

**Логика:**
```python
lines = []
remaining_time = instrumental_end - current_seg["start"]

# Определяем, сколько цифр можно показать
# Минимум 1 сек на цифру
countdown_start = max(current_seg["start"], instrumental_end - 3)

# Генерируем цифры countdown
for i, digit in enumerate([3, 2, 1], start=0):
    digit_start = instrumental_end - (3 - i)
    digit_end = digit_start + 1
    
    # Проверяем, что цифра помещается в сегмент
    if digit_start >= current_seg["start"]:
        lines.append(
            f"Dialogue: 1,"
            f"{_format_ass_time(digit_start)},"
            f"{_format_ass_time(min(digit_end, instrumental_end))},"
            f"Highlight,,0,0,0,,{digit}\n"
        )

# Early NextLine (за 3 сек до конца instrumental)
next_text = next_vocal_segment.get("text", "")
if next_text and countdown_start < instrumental_end:
    lines.append(
        f"Dialogue: 0,"
        f"{_format_ass_time(countdown_start)},"
        f"{_format_ass_time(instrumental_end)},"
        f"NextLine,,0,0,0,,{next_text}\n"
    )

return lines
```

### 5. Обработка стиля `Highlight` для countdown
Цифры countdown используют существующий стиль `Highlight` (уже определен в [`_ASS_HEADER_TEMPLATE`](app/ass_generator.py:109)):
```
Style: Highlight,  Arial,{font_size},&H0000FFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,5,0,0,0,1
```
Цвет `&H0000FFFF` = Cyan (циан/голубой)

## Пример результата в ASS

### Сценарий 1: Длинный instrumental (5 сек)
```ass
; instrumental с 40.0 по 45.0
; следующий вокал начинается в 45.0
Dialogue: 0,0:00:40.00,0:00:42.00,ActiveLine,,0,0,0,,(проигрыш)
Dialogue: 0,0:00:42.00,0:00:45.00,NextLine,,0,0,0,,Следующая строка песни
Dialogue: 1,0:00:42.00,0:00:43.00,Highlight,,0,0,0,,3
Dialogue: 1,0:00:43.00,0:00:44.00,Highlight,,0,0,0,,2
Dialogue: 1,0:00:44.00,0:00:45.00,Highlight,,0,0,0,,1
```

### Сценарий 2: Короткий instrumental (2 сек)
```ass
; instrumental с 30.0 по 32.0
Dialogue: 0,0:00:30.00,0:00:31.00,ActiveLine,,0,0,0,,(проигрыш)
Dialogue: 0,0:00:29.00,0:00:32.00,NextLine,,0,0,0,,Следующая строка песни
Dialogue: 1,0:00:31.00,0:00:32.00,Highlight,,0,0,0,,1
```

## Параметры конфигурации
Добавить в [`app/config.py`](app/config.py:1):
```python
# Включение countdown в instrumental-сегментах
ASS_COUNTDOWN_ENABLED: bool = Field(default=True)
ASS_COUNTDOWN_SECONDS: int = Field(default=3, ge=1, le=5)
```

## Интеграция в pipeline
**Файл:** [`app/pipeline.py`](app/pipeline.py:1)

В шаге `GENERATE_ASS`:
```python
ass_generator.generate(
    aligned_lyrics_file=state.aligned_lyrics_file,
    output_ass_path=state.ass_file,
    track_title=track_title,
    volume_segments_path=state.volume_segments_file,  # уже передается
)
```

## Тестирование

### Ручное тестирование
1. Запустить пайплайн для трека с известными instrumental-сегментами
2. Проверить сгенерированный ASS файл:
   - Наличие `Dialogue: 1` с цифрами 3-2-1 перед концом instrumental
   - Наличие `Dialogue: 0, NextLine` за 3 сек до конца instrumental
3. Отрендерить видео и визуально проверить поведение

### Граничные случаи
- Instrumental в начале трека (нет предыдущего сегмента)
- Instrumental в конце трека (нет следующего вокального сегмента)
- Несколько instrumental подряд
- Очень короткий instrumental (< 1 сек) — countdown пропускается полностью

## Файлы для изменения
1. [`app/ass_generator.py`](app/ass_generator.py:1) — основные изменения
2. [`app/config.py`](app/config.py:1) — новые параметры конфигурации
3. [`docs/tasklist.md`](docs/tasklist.md:1) — обновление статуса
