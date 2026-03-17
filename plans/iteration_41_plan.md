# Итерация 41 — Корректировка границ слов/строк с учётом instrumental-сегментов

## Цель

Уточнить границы привязки слов к строкам в [`AlignmentService`](app/alignment_service.py), используя разметку из `volume_segments_file`. Разметка проводится **только в рамках сегментов, отличных от типа `instrumental`**. Определить критерии и алгоритм обработки случаев, когда `segment` транскрипции или LRC началом/концом перекрывает `volume_segment` с типом `instrumental`.

---

## Проблема

Текущий [`AlignmentService`](app/alignment_service.py:1) выполняет привязку слов и строк без учёта музыкальной структуры трека. Слова и строки могут "растягиваться" на инструментальные сегменты (intro, bridge, outro), создавая некорректные тайминги.

---

## Структура данных

### VolumeSegment (из `volume_segments_file`)

```json
[
  {
    "id": 1,
    "start": 0.0,
    "end": 15.5,
    "volume": 0.2,
    "segment_type": "instrumental",
    "backend": "dual_file"
  },
  {
    "id": 2,
    "start": 15.5,
    "end": 45.0,
    "volume": 0.4,
    "segment_type": "verse",
    "backend": "dual_file"
  }
]
```

Критическое поле: `segment_type` — определяет, является ли сегмент `instrumental`.

---

## Алгоритм корректировки (мягкий)

### Шаг 1: Загрузка instrumental-сегментов

```python
def _load_instrumental_segments(volume_segments_path: Path) -> list[tuple[float, float]]:
    """Загрузить список (start, end) для всех instrumental сегментов."""
    segments = load_volume_segments(volume_segments_path)
    return [
        (seg.start, seg.end) 
        for seg in segments 
        if seg.segment_type == "instrumental"
    ]
```

### Шаг 2: Корректировка слов (Word-level)

Для каждого слова проверяем пересечение с instrumental-сегментами.

**Обрабатываем только частичное пересечение:**

```
Если слово частично пересекается с instrumental:
    → Корректировать границы слова
    
    Случай A: instrumental в начале слова
        word_start = instrumental_end
        (начало слова = конец инструментала)
        
    Случай B: instrumental в конце слова  
        word_end = instrumental_start
        (конец слова = начало инструментала)
```

**Критерий частичного пересечения:**
```python
def _get_instrumental_overlap(
    word_start: float, 
    word_end: float,
    instr_segments: list[tuple[float, float]]
) -> tuple[str, float] | None:
    """Вернуть (position, boundary_time) если есть частичное пересечение.
    
    position: "start" | "end" — где происходит пересечение
    boundary_time: время границы instrumental для корректировки
    """
    for instr_start, instr_end in instr_segments:
        # Частичное пересечение: слово задевает instrumental только с одной стороны
        
        # Случай A: instrumental в начале слова
        # word_start < instr_end AND word_end > instr_end AND word_start < instr_start
        if word_start < instr_end and word_end > instr_end and word_start < instr_start:
            return ("start", instr_end)
            
        # Случай B: instrumental в конце слова
        # word_start < instr_start AND word_end > instr_start AND word_end > instr_end
        if word_start < instr_start and word_end > instr_start and word_end > instr_end:
            return ("end", instr_start)
            
    return None
```

### Шаг 3: Корректировка строк (Line-level)

Для каждой строки применяется аналогичная логика:

```
Если строка частично пересекается с instrumental:
    → Корректировать границы
    
    Случай A: instrumental в начале строки
        line_start = instrumental_end
        Добавить маркер "(проигрыш)" перед первым словом строки
        
    Случай B: instrumental в конце строки
        line_end = instrumental_start
```

**Добавление маркера:**
```python
def _add_gap_marker_to_line(
    line_words: list[WordWithTimestamp],
    gap_end: float,
) -> list[WordWithTimestamp]:
    """Добавить слово-маркер (проигрыш) перед первым словом строки."""
    if not line_words:
        return line_words
        
    first_word = line_words[0]
    marker = WordWithTimestamp(
        word="(проигрыш)",
        start_time=first_word.start_time - (gap_end - first_word.start_time),
        end_time=first_word.start_time
    )
    # Вставить маркер в начало списка слов строки
    return [marker] + line_words
```

### Шаг 4: Пример работы

**Volume segments:**
```
0.0  - 15.0: instrumental (intro)
15.0 - 45.0: verse
45.0 - 60.0: instrumental (bridge)
```

**Words before correction:**
```
"Tell":   start=12.0, end=16.0  (instrumental в начале, случай A)
"and":    start=58.0, end=62.0  (instrumental в конце, случай B)
```

**After correction:**
```
"Tell":   start=15.0, end=16.0  (скорректировано: start = instrumental_end)
"and":    start=58.0, end=60.0  (скорректировано: end = instrumental_start)
```

**Line correction:**
```
Before: "Tell me something..." start=12.0, end=20.0
After:  "(проигрыш) Tell me something..." start=15.0, end=20.0
```

---

## Интеграция в AlignmentService

### Новый метод

```python
def _apply_instrumental_correction(
    self,
    result: AlignedLyricsResult,
    volume_segments_path: Path,
) -> AlignedLyricsResult:
    """Мягкая корректировка границ слов и строк по instrumental-сегментам.
    
    Обрабатывает только частичные пересечения:
    - instrumental в начале → truncate start
    - instrumental в конце → truncate end
    
    Args:
        result: Исходный результат выравнивания.
        volume_segments_path: Путь к JSON с volume_segments.
        
    Returns:
        Скорректированный результат.
    """
    instr_segments = self._load_instrumental_segments(volume_segments_path)
    if not instr_segments:
        return result
        
    # Корректировка слов
    corrected_words = self._correct_words_partial_overlap(
        result.words, instr_segments
    )
    
    # Корректировка строк
    corrected_lines = self._correct_lines_partial_overlap(
        result.segments, instr_segments, corrected_words
    )
    
    return AlignedLyricsResult(
        words=corrected_words,
        segments=corrected_lines
    )
```

### Обновление align_timestamps()

```python
def align_timestamps(
    self,
    transcription_json_path: Path,
    source_lyrics_path: Path,
    audio_file: Optional[Path] = None,
    max_word_time: float = 5.0,
    normal_word_time: float = 1.5,
    volume_segments_path: Optional[Path] = None,  # ← новый параметр
) -> AlignedLyricsResult:
    ...
    result = strategy.align(transcription_words, lyrics_segments)
    result = self._sanitise(result)
    
    # Мягкая корректировка по instrumental-сегментам
    if volume_segments_path and volume_segments_path.exists():
        result = self._apply_instrumental_correction(result, volume_segments_path)
        logger.info("AlignmentService: applied instrumental correction")
    
    return result
```

---

## Обновление Pipeline

В [`app/pipeline.py`](app/pipeline.py) метод `_step_align()`:

```python
async def _step_align(self) -> None:
    ...
    # Определяем путь к volume_segments_file
    volume_segments_path: Path | None = None
    if self._state.segment_groups_file:
        vsp = Path(self._state.segment_groups_file)
        if vsp.exists():
            volume_segments_path = vsp
    elif self._state.volume_segments_file:
        vsp = Path(self._state.volume_segments_file)
        if vsp.exists():
            volume_segments_path = vsp
    
    alignment = service.align_timestamps(
        transcription_json_path=transcribe_path,
        source_lyrics_path=lyrics_path,
        max_word_time=self._settings.max_word_time,
        normal_word_time=self._settings.normal_word_time,
        volume_segments_path=volume_segments_path,
    )
    ...
```

---

## Сейчас не делаем, заметки на будущее

### Жёсткая корректировка (удаление/разделение)

**Для слов:**
- Удаление слов полностью внутри instrumental
- Разделение слова на два при instrumental посередине
- Добавление маркера "(проигрыш)" для удалённого участка

**Для строк:**
- Удаление строк полностью внутри instrumental
- Разделение строки на две при instrumental посередине
- Корректное распределение слов между разделёнными строками

### Дополнительные улучшения

**Конфигурируемость:**
- Параметр `alignment_instrumental_correction: bool = True` — включение/выключение корректировки
- Параметр `alignment_correction_mode: str = "soft"` — "soft" | "hard" выбор режима

**Расширенная логика:**
- Учёт порога минимальной длительности слова после корректировки
- Интерполяция времени для слов, полностью попавших в instrumental (вместо удаления)
- Корректировка соседних слов при изменении границ (сохранение continuity)

---

## Порядок выполнения

1. [ ] Добавить метод `_load_instrumental_segments()` в `AlignmentService`
2. [ ] Реализовать `_correct_words_partial_overlap()` — мягкая корректировка слов (случаи A и B)
3. [ ] Реализовать `_correct_lines_partial_overlap()` — мягкая корректировка строк + маркер
4. [ ] Реализовать `_apply_instrumental_correction()` — объединяющий метод
5. [ ] Обновить сигнатуру `align_timestamps()` с параметром `volume_segments_path`
6. [ ] Обновить `_step_align()` в pipeline для передачи пути к сегментам
7. [ ] Протестировать на треке с intro и bridge

---

## Проверка

- [ ] Слова с instrumental в начале: `word.start = instrumental.end`
- [ ] Слова с instrumental в конце: `word.end = instrumental.start`
- [ ] Строки с instrumental в начале: `line.start = instrumental.end` + маркер "(проигрыш)"
- [ ] Строки с instrumental в конце: `line.end = instrumental.start`
- [ ] При отсутствии `volume_segments_file` поведение не изменяется
- [ ] Слова полностью внутри instrumental остаются без изменений (мягкий режим)

---

## Связанные файлы

| Файл | Изменения |
|------|-----------|
| [`app/alignment_service.py`](app/alignment_service.py) | Новые методы корректировки, обновление `align_timestamps()` |
| [`app/pipeline.py`](app/pipeline.py) | Передача `volume_segments_path` в `_step_align()` |
| [`app/chorus_detector.py`](app/chorus_detector.py) | Использование `load_volume_segments()` (уже реализовано) |
