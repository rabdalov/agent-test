# Итерация 27 — Удаление лишней информации из transcribe

## Задача шага

После выполнения шага `TRANSCRIBE` результирующий JSON транскрипции содержит множество лишних полей. Необходимо очистить его, оставив только необходимые данные для последующих шагов пайплайна.

### Требования к формату

1. **В корне оставить только следующие поля:**
   ```json
   {
     "duration": 295.0530625,
     "language": "ru",
     "segments": [...],
     "words": [...]
   }
   ```

2. **Секцию `words` оставить полностью как есть** — без каких-либо изменений.

3. **Секцию `segments` привести к строгому формату**, оставив только поля:
   - `id`
   - `start`
   - `end`
   - `text`

## Пример входных данных

Файл: `data_exapmles/Полина Гагарина - Shallow_transcription.json`

Исходный JSON содержит множество дополнительных полей на уровне корня и внутри каждого сегмента (например, `tokens`, `avg_logprob`, `compression_ratio`, `no_speech_prob` и другие метаданные Whisper).

## Ожидаемый результат

Файл сохранён в JSON следующего формата:

```json
{
  "duration": 295.0530625,
  "language": "ru",
  "segments": [
    {
      "id": 1,
      "start": 16.01,
      "end": 44.87,
      "text": " Tell me something, girl Are you happy and that's more than one? Or do you need more? Is there something else you're searching for? I've fallen In all the good times I find myself"
    }
  ],
  "words": [
    {
      "end": 16.77,
      "start": 16.01,
      "word": " Tell"
    }
  ]
}
```

## План реализации

### Шаг 1 — Создать функцию очистки транскрипта

Создать функцию очистки в модуле [`app/pipeline.py`](../app/pipeline.py) или в отдельном сервисе.

Функция должна:
- Принимать исходный словарь транскрипции (или путь к JSON-файлу)
- Извлекать только поля `duration`, `language`, `segments`, `words`
- Для каждого сегмента оставлять только поля `id`, `start`, `end`, `text`
- Возвращать очищенный словарь

Пример реализации:

```python
def clean_transcription(raw: dict) -> dict:
    """Очищает результат транскрипции, оставляя только необходимые поля."""
    cleaned_segments = [
        {
            "id": seg["id"],
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"],
        }
        for seg in raw.get("segments", [])
    ]
    return {
        "duration": raw.get("duration"),
        "language": raw.get("language"),
        "segments": cleaned_segments,
        "words": raw.get("words", []),
    }
```

### Шаг 2 — Применить функцию в пайплайне

Вызвать `clean_transcription()` после шага `TRANSCRIBE` и **до** шага `CORRECT_TRANSCRIPT` в [`app/pipeline.py`](../app/pipeline.py).

Место вставки — сразу после получения результата транскрипции и записи его в файл:

```python
# После шага TRANSCRIBE
transcription_data = clean_transcription(raw_transcription_data)
# Сохранить очищенный JSON
with open(transcription_path, "w", encoding="utf-8") as f:
    json.dump(transcription_data, f, ensure_ascii=False, indent=2)
```

### Шаг 3 — Проверить совместимость с последующими шагами

Убедиться, что все последующие шаги пайплайна корректно работают с новым форматом:
- Шаг `CORRECT_TRANSCRIPT` — использует поля `segments[].text`, `language`
- Шаг `ALIGN` — использует поля `segments`, `words`
- Шаг `ASS_GENERATE` — использует поля `segments`, `words`

## Проверка

После выполнения шага:
1. Создаётся очищенный JSON-файл транскрипции с минимальным набором полей
2. Файл содержит только `duration`, `language`, `segments`, `words`
3. Каждый сегмент содержит только `id`, `start`, `end`, `text`
4. Секция `words` сохранена без изменений
5. Все последующие шаги пайплайна успешно работают с очищенным форматом

## Связанные файлы

- [`app/pipeline.py`](../app/pipeline.py) — основной пайплайн, место реализации
- [`app/models.py`](../app/models.py) — модели данных (при необходимости обновить)
- `data_exapmles/Полина Гагарина - Shallow_transcription.json` — пример входных данных
