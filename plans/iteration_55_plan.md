# Итерация 55 — Стратегия Forced Alignment на основе MMS_FA

## Цель

Реализовать новую стратегию выравнивания текста по таймкодам `ForcedAlignmentStrategy` в [`AlignmentService`](app/alignment_service.py:504), использующую метод **Forced alignment for multilingual data** из [PyTorch Audio tutorial](https://docs.pytorch.org/audio/2.8/tutorials/forced_alignment_for_multilingual_data_tutorial.html) на базе модели MMS_FA (Facebook's Massively Multilingual Speech).

## Контекст

### Существующая архитектура AlignmentService

- Базовый класс [`AlignmentStrategy`](app/alignment_service.py:215) с методом `align()`
- Две реализации:
  - [`LrcDirectStrategy`](app/alignment_service.py:234) — использует встроенные LRC-таймкоды
  - [`SequenceAlignmentStrategy`](app/alignment_service.py:281) — Needleman-Wunsch выравнивание между ASR и текстом песни
- Выбор стратегии через [`_select_strategy()`](app/alignment_service.py:602) на основе наличия LRC-меток
- Входные данные:
  - `transcription_json_path` — JSON с word-level таймкодами от Whisper/speeches.ai
  - `source_lyrics_path` — TXT с текстом песни
  - Опционально `audio_file` — путь к вокальной дорожке (зарезервировано)

### Отличие MMS_FA от текущего подхода

| Аспект | Текущий подход | MMS_FA |
|--------|---------------|--------|
| Источник таймкодов | ASR (Whisper) → align с текстом | Прямое forced alignment аудио с текстом |
| Зависимости | Нет ML | torch, torchaudio, transformers |
| Точность | Зависит от качества ASR | Непосредственное выравнивание |
| Языки | Ограничены Whisper | 1000+ языков (MMS) |

## Функциональные требования

### 1. Новая стратегия ForcedAlignmentStrategy

Класс реализует [`AlignmentStrategy`](app/alignment_service.py:215) интерфейс:

```python
class ForcedAlignmentStrategy(AlignmentStrategy):
    """Forced alignment using MMS_FA from torchaudio.
    
    Aligns audio file with lyrics text using Facebook's MMS model.
    """
```

Особенности:
- Принимает путь к WAV-файлу (16kHz, mono) и текст песни
- Возвращает [`AlignedLyricsResult`](app/alignment_service.py:60) с word-level и line-level таймкодами
- Использует язык из [`PipelineState.language`](app/models.py:75) (ISO код, например "rus", "eng")

### 2. Конвертация аудио в WAV 16kHz mono

```python
def convert_to_wav_for_mms(
    input_path: Path,
    output_path: Path,
    sample_rate: int = 16000,
) -> Path:
    """Convert audio to WAV 16kHz mono using ffmpeg."""
```

Параметры ffmpeg:
- `-ar 16000` — частота дискретизации
- `-ac 1` — моно
- `-c:a pcm_s16le` — 16-bit PCM

### 3. Интеграция MMS_FA

Алгоритм по [PyTorch tutorial](https://docs.pytorch.org/audio/2.8/tutorials/forced_alignment_for_multilingual_data_tutorial.html):

```python
def align_with_mms_fa(
    waveform: torch.Tensor,
    transcript: str,
    language: str,
) -> list[WordWithTimestamp]:
    """Run MMS_FA alignment.
    
    Args:
        waveform: Audio tensor (1, samples) at 16kHz
        transcript: Plain text lyrics
        language: ISO 639-3 code (e.g., 'rus', 'eng')
    
    Returns:
        List of WordWithTimestamp with start/end times
    """
```

Шаги:
1. Загрузка модели `Wav2Vec2ForCTC` из `facebook/mms-1b-all` или `facebook/mms-1b-fl102`
2. Загрузка tokenizer'а для указанного языка
3. Получение эмиссий (emissions) через forward pass
4. Построение trellis для Viterbi alignment
5. Backtracking для получения границ слов
6. Конвертация в [`WordWithTimestamp`](app/alignment_service.py:37)

### 4. Пост-обработка результатов

После получения word-level таймкодов:

1. **Группировка в строки** — разделение на строки по исходному форматированию `source_lyrics_file`
2. **Интерполяция** — для слов без точного выравнивания (gap handling)
3. **Санитизация** — [`_sanitise()`](app/alignment_service.py:617) для неотрицательных таймкодов

### 5. Конфигурация

Новые параметры в [`config.py`](app/config.py):

```python
# Alignment strategy selection
ALIGN_ENABLE_FORCED: bool = False  # Enable ForcedAlignmentStrategy
ALIGN_MMS_MODEL: str = "facebook/mms-1b-fl102"  # 102 languages, compact
ALIGN_MMS_DEVICE: str = "auto"  # "auto", "cpu", "rocm"
```

При `ALIGN_ENABLE_FORCED=True` стратегия выбирается явно (без fallback), иначе — текущая логика.

**Определение устройства при `auto`:**
1. Проверить `torch.version.hip` (ROCm/HIP доступен)
2. Если HIP доступен → использовать `"cuda:0"` (в терминологии PyTorch для ROCm)
3. Иначе → `"cpu"`

**Важно**: CUDA не проверяем и не используем (`torch.cuda.is_available()` не вызываем)

## Техническая реализация

### Шаг 1: Добавление зависимостей

В `pyproject.toml`:

```toml
[project.optional-dependencies]
forced-align = [
    "torch>=2.0.0",
    "torchaudio>=2.0.0",
    "transformers>=4.30.0",
]
```

Или в основные зависимости (принято решение о включении).

### Шаг 2: Класс ForcedAlignmentStrategy

```python
class ForcedAlignmentStrategy(AlignmentStrategy):
    """Forced alignment using Facebook MMS model via torchaudio."""
    
    # Mapping ISO 639-1 → ISO 639-3 for MMS
    _LANG_CODE_MAPPING = {
        "ru": "rus",
        "en": "eng",
    }
    
    def __init__(
        self,
        model_name: str = "facebook/mms-1b-fl102",
        device: str = "auto",
    ) -> None:
        self.model_name = model_name
        self.device = self._resolve_device(device)
    
    def _resolve_device(self, device: str) -> str:
        """Resolve device: auto-detect ROCm or fallback to CPU."""
        if device != "auto":
            return device
        # Check for ROCm/HIP only, never CUDA
        if hasattr(torch.version, "hip") and torch.version.hip is not None:
            return "cuda:0"  # ROCm uses "cuda" naming in PyTorch
        return "cpu"
    
    def _convert_lang_code(self, lang: str) -> str:
        """Convert ISO 639-1 to ISO 639-3 for MMS."""
        return self._LANG_CODE_MAPPING.get(lang, lang)
    
    def _load_model(self) -> tuple[Any, Any]:
        """Load MMS model and tokenizer (no caching, load every call)."""
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        model = Wav2Vec2ForCTC.from_pretrained(self.model_name)
        processor = Wav2Vec2Processor.from_pretrained(self.model_name)
        model.to(self.device)
        model.eval()
        return model, processor
    
    def align(
        self,
        transcription_words: list[WordWithTimestamp],  # Not used, but required by interface
        lyrics_segments: list[tuple[float | None, str]],
        audio_path: Path | None = None,
        language: str = "en",  # ISO 639-1: "ru" or "en"
    ) -> AlignedLyricsResult:
        """Run forced alignment on audio with lyrics text."""
        if audio_path is None:
            raise ValueError("ForcedAlignmentStrategy requires audio_path")
        
        # Load model fresh every time (no caching)
        model, processor = self._load_model()
        
        # Load and preprocess audio
        waveform = self._load_audio(audio_path)
        
        # Flatten lyrics to transcript
        transcript = self._build_transcript(lyrics_segments)
        
        # Convert language code to ISO 639-3
        mms_lang = self._convert_lang_code(language)
        
        # Run alignment
        words_with_ts = self._run_mms_alignment(waveform, transcript, mms_lang, model, processor)
        
        # Cleanup (allow GC to free memory)
        del model, processor
        
        # Group into lines
        lines = self._group_into_lines(words_with_ts, lyrics_segments)
        
        return AlignedLyricsResult(words=words_with_ts, segments=lines)
```

### Шаг 3: Модификация AlignmentService

```python
def align_timestamps(
    self,
    transcription_json_path: Path,
    source_lyrics_path: Path,
    audio_file: Optional[Path] = None,
    language: Optional[str] = None,  # ISO code from PipelineState
    max_word_time: float = 5.0,
    normal_word_time: float = 1.5,
) -> AlignedLyricsResult:
```

Изменения:
1. Новый параметр `language` — ISO 639-3 код для MMS
2. Передача `audio_file` и `language` в стратегию
3. Обновление `_select_strategy()` для учета `ALIGN_ENABLE_FORCED`

### Шаг 4: Модификация PipelineState

Добавить поле в [`models.py`](app/models.py):

```python
language: str = "eng"  # ISO 639-3 code, set during TRANSCRIBE step
```

Уже существует в [`PipelineState`](app/models.py:47) — использовать его.

### Шаг 5: Интеграция в KaraokePipeline

Обновить `_step_align()`:

```python
def _step_align(self, state: PipelineState) -> None:
    """Align lyrics with timestamps."""
    service = AlignmentService()
    
    result = service.align_timestamps(
        transcription_json_path=Path(state.corrected_transcribe_json_file or state.transcribe_json_file),
        source_lyrics_path=Path(state.source_lyrics_file),
        audio_file=Path(state.vocal_file) if state.vocal_file else None,
        language=state.language,  # ISO code from TRANSCRIBE
    )
    
    # Save result
    save_aligned_result(result, Path(state.aligned_lyrics_file))
```

## Алгоритм MMS_FA Alignment

### Основные шаги (по PyTorch tutorial)

```python
def _run_mms_alignment(
    self,
    waveform: torch.Tensor,
    transcript: str,
    language: str,
) -> list[WordWithTimestamp]:
    """Execute MMS forced alignment algorithm."""
    import torchaudio
    from torchaudio.models import wav2vec2_model
    
    # 1. Tokenize transcript
    tokens = self._processor.tokenizer.encode(transcript, return_tensors="pt")
    
    # 2. Get model emissions
    with torch.no_grad():
        emissions = self._model(waveform).logits
    
    # 3. Build trellis for Viterbi
    # trellis[i, j] = probability of being at token j at frame i
    trellis = self._get_trellis(emissions, tokens)
    
    # 4. Backtrack to find best path
    path = self._backtrack(trellis, emissions, tokens)
    
    # 5. Merge repeats and extract word boundaries
    word_segments = self._merge_repeats(path, transcript)
    
    # 6. Convert to WordWithTimestamp
    words = []
    for word, start_frame, end_frame in word_segments:
        start_time = start_frame / self._sample_rate
        end_time = end_frame / self._sample_rate
        words.append(WordWithTimestamp(
            word=word,
            start_time=round(start_time, 3),
            end_time=round(end_time, 3),
        ))
    
    return words
```

### Обработка ошибок

| Ошибка | Действие |
|--------|----------|
| Модель не найдена | Лог ERROR, fallback на SequenceAlignmentStrategy |
| Неподдерживаемый язык | Лог WARNING, fallback на SequenceAlignmentStrategy |
| Ошибка конвертации WAV | Лог ERROR, fallback на SequenceAlignmentStrategy |
| Ошибка alignment (пустой результат) | Лог WARNING, fallback на SequenceAlignmentStrategy |

## План реализации

### Шаг 1: Добавить зависимости
- [ ] Обновить `pyproject.toml` с torch, torchaudio, transformers
- [ ] Установить через `uv sync`

### Шаг 2: Реализовать ForcedAlignmentStrategy
- [ ] Создать класс с наследованием от AlignmentStrategy
- [ ] Реализовать `_load_model()` с lazy loading
- [ ] Реализовать `_load_audio()` через torchaudio
- [ ] Реализовать `_run_mms_alignment()` по tutorial
- [ ] Реализовать `_build_transcript()` из lyrics_segments
- [ ] Реализовать `_group_into_lines()` для line-level результатов

### Шаг 3: Модификация AlignmentService
- [ ] Добавить параметр `language` в `align_timestamps()`
- [ ] Обновить `_select_strategy()` для учета `ALIGN_ENABLE_FORCED`
- [ ] Добавить метод `_convert_to_wav()` для ffmpeg-конвертации

### Шаг 4: Конфигурация
- [ ] Добавить `ALIGN_ENABLE_FORCED` в `config.py`
- [ ] Добавить `ALIGN_MMS_MODEL` и `ALIGN_MMS_DEVICE`

### Шаг 5: Интеграция в пайплайн
- [ ] Обновить `KaraokePipeline._step_align()` для передачи audio_file и language
- [ ] Убедиться что `state.language` заполняется на шаге TRANSCRIBE

### Шаг 6: Тестирование
- [ ] Тест с русским языком (rus)
- [ ] Тест с английским языком (eng)
- [ ] Тест fallback при ошибке
- [ ] Сравнение точности с SequenceAlignmentStrategy

## Согласование с vision.md

- **Простота (KISS)**: Стратегия как отдельный класс, минимальная интеграция с существующим кодом
- **1 класс = 1 файл**: `ForcedAlignmentStrategy` внутри `alignment_service.py`
- **Три слоя**: Telegram-интерфейс → AlignmentService (домен) → MMS_FA (интеграция)
- **Python 3.12 + типизация**: Полная аннотация типов
- **Асинхронность**: Тяжёлые CPU-операции (model inference) в executor pool
- **Конфигурация**: Все параметры через Settings
- **Ошибки**: Явный fallback на работающую стратегию при проблемах

## Артефакты

- Модифицированный `app/alignment_service.py` с новой стратегией
- Обновлённый `app/config.py` с параметрами MMS
- Обновлённый `pyproject.toml` с зависимостями
- Тестовый скрипт `scripts/test_forced_alignment.py`

## Принятые архитектурные решения

| Вопрос | Решение |
|--------|---------|
| **Модель** | `facebook/mms-1b-fl102` (102 языка, компактнее) |
| **Кэширование** | Загружать при каждом вызове (без singleton кэша) |
| **GPU** | Поддержка ROCm (AMD) — определить наличие, использовать если есть, иначе CPU-only. **CUDA не используем!** |
| **Языковые коды** | Простой mapping: `ru` → `rus`, `en` → `eng` (только эти два кода) |
