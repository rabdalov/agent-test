# План реализации итерации 17: Корректировка транскрипции с использованием LLM (CORRECT_TRANSCRIPT)

## Цель
Добавить новый шаг CORRECT_TRANSCRIPT в пайплайн, который выполняется после TRANSCRIBE и перед ALIGN. Шаг использует LLM для корректировки распознанного текста на основе исходного текста песни.

## Текущее состояние

### Изученные файлы:
- [`app/pipeline.py`](app/pipeline.py) - текущая структура пайплайна
- [`app/config.py`](app/config.py) - конфигурация (нет LLM параметров)
- [`app/models.py`](app/models.py) - enum PipelineStep (нет CORRECT_TRANSCRIPT)
- [`example.env`](example.env) - есть настройки OpenRouter (OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_API)

### Отсутствующие компоненты:
- Нет `app/llm_client.py` (LLM клиент не реализован)
- Нет шага CORRECT_TRANSCRIPT в пайплайне

---

## Детальный план реализации

### Этап 1: Добавление конфигурации LLM

**1.1 Добавить параметры LLM в `app/config.py`:**
- `openrouter_api_key: str | None = None` - API ключ OpenRouter
- `openrouter_model: str = "qwen/qwen3-next-80b-a3b-instruct:free"` - модель LLM
- `openrouter_api_url: str = "https://api.openrouter.ai/v1"` - URL API
- `correct_transcript_enabled: bool = True` - включение/выключение шага CORRECT_TRANSCRIPT

**1.2 Обновить загрузку из окружения в `Settings.from_env()`:**
- OPENROUTER_API_KEY
- OPENROUTER_MODEL
- OPENROUTER_API_URL
- CORRECT_TRANSCRIPT_ENABLED

### Этап 2: Создание LLM клиента

**2.1 Создать `app/llm_client.py`:**
- Класс `LLMClient` с использованием `openai` клиента
- Настройка на провайдера OpenRouter
- Метод `complete(prompt: str) -> str` для отправки запросов к LLM

### Этап 3: Обновление моделей

**3.1 Добавить CORRECT_TRANSCRIPT в `app/models.py`:**
```python
class PipelineStep(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    GET_LYRICS = "GET_LYRICS"
    SEPARATE = "SEPARATE"
    TRANSCRIBE = "TRANSCRIBE"
    CORRECT_TRANSCRIPT = "CORRECT_TRANSCRIPT"  # новый шаг
    ALIGN = "ALIGN"
    GENERATE_ASS = "GENERATE_ASS"
    RENDER_VIDEO = "RENDER_VIDEO"
```

**3.2 Добавить поле для скорректированной транскрипции в `PipelineState`:**
```python
corrected_transcribe_json_file: str | None = None
```

### Этап 4: Реализация сервиса корректировки транскрипции

**4.1 Создать `app/correct_transcript_service.py`:**
- Класс `CorrectTranscriptService`
- Метод `correct_transcript(transcription_json_path: Path, lyrics_path: Path) -> dict`:
  - Читает исходную транскрипцию из JSON
  - Читает текст песни из файла
  - Формирует промпт для LLM с инструкцией по корректировке
  - Отправляет запрос к LLM
  - Парсит ответ LLM и возвращает скорректированную транскрипцию в том же формате JSON

**Формат промпта для LLM:**
```
You are a music transcription expert. Given a transcription of a song and the original lyrics, 
your task is to correct the transcription to better match the original lyrics.

Original lyrics:
{lyrics_text}

Transcription (JSON):
{transcription_json}

Please return the corrected transcription in the same JSON format. 
Focus on fixing:
1. Misrecognized words
2. Missing words
3. Word order issues
4. Punctuation

Return only the corrected JSON, no additional text.
```

### Этап 5: Интеграция в пайплайн

**5.1 Обновить `app/pipeline.py`:**

- Добавить импорт `CorrectTranscriptService` и `LLMClient`
- Обновить `_STEP_LABELS`:
```python
_STEP_LABELS: dict[PipelineStep, str] = {
    ...
    PipelineStep.CORRECT_TRANSCRIPT: "корректировка транскрипции",
    ...
}
```

- Обновить `_ORDERED_STEPS` (вставить CORRECT_TRANSCRIPT после TRANSCRIBE):
```python
_ORDERED_STEPS: list[PipelineStep] = [
    PipelineStep.DOWNLOAD,
    PipelineStep.GET_LYRICS,
    PipelineStep.SEPARATE,
    PipelineStep.TRANSCRIBE,
    PipelineStep.CORRECT_TRANSCRIPT,  # новый шаг
    PipelineStep.ALIGN,
    PipelineStep.GENERATE_ASS,
    PipelineStep.RENDER_VIDEO,
]
```

- Обновить `_STEP_REQUIRED_ARTIFACTS`:
```python
_STEP_REQUIRED_ARTIFACTS: dict[PipelineStep, list[str]] = {
    ...
    PipelineStep.CORRECT_TRANSCRIPT: ["transcribe_json_file", "source_lyrics_file"],
    PipelineStep.ALIGN: ["source_lyrics_file", "transcribe_json_file"],  # может использовать corrected
    ...
}
```

- Добавить метод `_step_correct_transcribe()`:
```python
async def _step_correct_transcribe(self) -> None:
    # Проверяем, включен ли шаг
    if not self._settings.correct_transcript_enabled:
        logger.info("CORRECT_TRANSCRIPT step skipped (disabled in config)")
        return
    
    transcribe_path = self._state.transcribe_json_file
    lyrics_path = self._state.source_lyrics_file
    
    # Создаём сервис корректировки
    llm_client = LLMClient(
        api_key=self._settings.openrouter_api_key,
        model=self._settings.openrouter_model,
        api_url=self._settings.openrouter_api_url,
    )
    correct_service = CorrectTranscriptService(llm_client=llm_client)
    
    # Выполняем корректировку
    corrected_data = await correct_service.correct_transcript(
        transcription_json_path=Path(transcribe_path),
        lyrics_path=Path(lyrics_path),
    )
    
    # Сохраняем скорректированную транскрипцию
    stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
    track_dir = Path(self._request.track_folder)
    output_json = track_dir / f"{stem}_transcription_corrected.json"
    
    import json
    output_json.write_text(json.dumps(corrected_data, indent=2, ensure_ascii=False), encoding="utf-8")
    
    self._state.corrected_transcribe_json_file = str(output_json)
    self._save_state()
```

- Обновить метод `_step_align()` для использования скорректированной транскрипции:
```python
async def _step_align(self) -> None:
    # Используем скорректированную транскрипцию, если она есть
    transcribe_path = self._state.corrected_transcribe_json_file or self._state.transcribe_json_file
    ...
```

- Добавить шаг в `step_methods` dict

### Этап 6: Обновление example.env

**6.1 Добавить настройки LLM в `example.env`:**
```
# CORRECT TRANSCRIPT SETTINGS
# Enable/disable the CORRECT_TRANSCRIPT step (default: true)
CORRECT_TRANSCRIPT_ENABLED=true
```

---

## Порядок выполнения (todolist)

1. [ ] Добавить параметры LLM в `app/config.py`
2. [ ] Создать `app/llm_client.py` (класс LLMClient)
3. [ ] Обновить `app/models.py`: добавить CORRECT_TRANSCRIPT в PipelineStep и поле в PipelineState
4. [ ] Создать `app/correct_transcript_service.py` (класс CorrectTranscriptService)
5. [ ] Обновить `app/pipeline.py`:
   - Добавить импорты
   - Обновить _STEP_LABELS
   - Обновить _ORDERED_STEPS
   - Обновить _STEP_REQUIRED_ARTIFACTS
   - Добавить метод _step_correct_transcribe
   - Обновить _step_align для использования скорректированной транскрипции
   - Добавить шаг в step_methods dict
6. [ ] Обновить `example.env` с новыми параметрами

---

## Проверка

После реализации необходимо проверить:
1. Шаг CORRECT_TRANSCRIPT выполняется после TRANSCRIBE и перед ALIGN
2. LLM корректирует транскрипцию на основе текста песни
3. Скорректированная транскрипция сохраняется в отдельный файл
4. Шаг ALIGN использует скорректированную транскрипцию при наличии
5. При отключённом CORRECT_TRANSCRIPT (через конфигурацию) шаг пропускается

---

## Зависимости

- Требуется пакет `openai` (уже указан в vision.md)
- Требуется `httpx` (для async HTTP запросов, если не хватит возможностей openai клиента)
