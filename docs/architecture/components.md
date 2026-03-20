# Компоненты системы

## Слой Telegram-интерфейса

### [`BotApp`](app/bot_app.py)

Точка входа для Telegram-бота. Отвечает за инициализацию и запуск.

**Основные задачи:**
- Создание экземпляра `aiogram.Bot` и `Dispatcher`
- Регистрация middleware (`UpdateLoggingMiddleware`)
- Регистрация хендлеров через `KaraokeHandlers.register(dp)`
- Запуск polling-цикла
- Graceful shutdown

**Ключевые методы:**
```python
async def start(self) -> None  # Запуск бота
async def stop(self) -> None   # Остановка бота
```

**Зависимости:**
- [`Settings`](app/config.py) — конфигурация
- [`KaraokeHandlers`](app/handlers_karaoke.py) — обработчики

---

### [`KaraokeHandlers`](app/handlers_karaoke.py)

Обработчики команд и сообщений Telegram-бота. Реализует FSM (Finite State Machine) для диалогов.

**Поддерживаемые команды:**

| Команда | Описание |
|---------|----------|
| `/start` | Приветственное сообщение |
| `/continue` | Продолжение прерванного пайплайна |
| `/search` | Поиск трека в локальном хранилище и Яндекс Музыке |
| `/change <диапазон>` | Изменение типа сегментов |

**FSM-состояния:**
- `TrackLangStates` — выбор языка трека
- `LyricsStates` — ожидание текста песни от пользователя
- `LyricsChoiceStates` — выбор источника текста (транскрипция/загрузка)
- `LyricsConfirmStates` — подтверждение сгенерированного текста
- `SearchStates` — процесс поиска трека
- `SegmentChangeStates` — изменение типа сегмента

**Обработчики сообщений:**
- Текстовые сообщения (URL-ссылки)
- Аудио/видео файлы
- Callback-запросы от inline-кнопок

---

## Слой доменного пайплайна

### [`KaraokePipeline`](app/pipeline.py)

Центральный компонент, оркестрирующий обработку трека.

**Порядок шагов пайплайна:**
```
DOWNLOAD → ASK_LANGUAGE → GET_LYRICS → SEPARATE → TRANSCRIBE → 
GENERATE_LYRICS → DETECT_CHORUS → CORRECT_TRANSCRIPT → ALIGN → 
MIX_AUDIO → GENERATE_ASS → RENDER_VIDEO → SEND_VIDEO
```

**Публичный API:**
```python
# Создание нового пайплайна
pipeline = KaraokePipeline.create_new(
    settings=settings,
    user_id=user_id,
    source_type=SourceType.TELEGRAM_FILE,
    source_url=url,
    track_folder=folder,
    bot=bot
)

# Запуск обработки
result = await pipeline.run(
    progress_callback=callback,
    start_from_step=None  # или конкретный шаг для продолжения
)

# Возобновление после ожидания ввода
result = await pipeline.resume(progress_callback=callback)
```

**Возможности:**
- Запуск с любого шага (при наличии артефактов)
- Автоматическое возобновление после ошибки
- Пауза для ожидания ввода пользователя (`WaitingForInputError`)
- Персистирование состояния в `state.json`

---

### [`PipelineState`](app/models.py)

Pydantic-модель, хранящая состояние пайплайна.

**Ключевые поля:**

| Поле | Описание |
|------|----------|
| `track_id` | UUID трека |
| `current_step` | Текущий/последний шаг пайплайна |
| `status` | PENDING / IN_PROGRESS / WAITING_FOR_INPUT / COMPLETED / FAILED |
| `source_type` | Тип источника (telegram_file, local_file, yandex_music, youtube, http_url) |
| `track_source` | Путь к исходному аудиофайлу |
| `vocal_file` | Путь к вокальной дорожке (после SEPARATE) |
| `instrumental_file` | Путь к инструментальной дорожке |
| `transcribe_json_file` | JSON с транскрипцией |
| `source_lyrics_file` | TXT с текстом песни |
| `aligned_lyrics_file` | JSON с выровненными таймкодами |
| `ass_file` | Файл субтитров ASS |
| `output_file` | Итоговый MP4 |
| `volume_segments_file` | JSON с разметкой сегментов |
| `segment_groups_file` | JSON с группами сегментов |
| `download_url` | Ссылка на скачивание |

---

## Слой интеграций

### [`DemucsService`](app/demucs_service.py)

Разделение аудио на голос и музыку.

**Конфигурация:**
- `DEMUCS_MODEL` — модель (htdemucs, htdemucs_ft)
- `DEMUCS_OUTPUT_FORMAT` — формат выхода (mp3, wav)

**Метод:**
```python
async def separate(
    self,
    audio_path: str,
    track_dir: str
) -> tuple[str, str]  # (vocals_path, accompaniment_path)
```

---

### [`SpeechesClient`](app/speeches_client.py)

Клиент для сервиса транскрибации speeches.ai (Whisper-совместимый).

**Конфигурация:**
- `SPEECHES_BASE_URL` — URL сервиса
- `TRANSCRIPTION_MODEL_ID` — модель Whisper
- `SPEECHES_TIMEOUT` — таймаут запроса

**Метод:**
```python
async def transcribe(
    self,
    vocal_file: Path,
    output_json: Path,
    language: str | None = None
) -> None
```

---

### [`LLMClient`](app/llm_client.py)

Клиент для работы с LLM через OpenRouter.

**Конфигурация:**
- `OPENROUTER_API_KEY` — API ключ
- `OPENROUTER_MODEL` — модель (например, qwen/qwen3.5-397b-a17b)
- `OPENROUTER_API` — URL API

**Метод:**
```python
async def chat_completion(
    self,
    messages: list[dict[str, str]],
    temperature: float = 0.3
) -> str
```

---

### [`LyricsService`](app/lyrics_service.py)

Поиск текстов песен через различные провайдеры.

**Провайдеры:**
- Genius API (`LYRICS_ENABLE_GENIUS`)
- LyricaV2 (`LYRICS_ENABLE_LYRICA`)
- lyrics-lib (`LYRICS_ENABLE_LYRICSLIB`)
- Яндекс Музыка (встроен в `YandexMusicDownloader`)

**Методы:**
```python
async def find_lyrics(track_stem: str, track_file_name: str | None) -> str | None
@staticmethod
def generate_lyrics_from_transcription(transcription_path: Path) -> str
```

---

### [`ChorusDetector`](app/chorus_detector.py)

Детектирование музыкальных сегментов (припев, куплет, инструментал).

**Алгоритм:**
1. Извлечение признаков из аудио (chroma, MFCC, энергия)
2. Построение self-similarity matrix
3. Кластеризация сегментов
4. Определение типа по энергии вокала

**Конфигурация:**
- `CHORUS_MIN_DURATION_SEC` — минимальная длительность сегмента
- `CHORUS_VOCAL_SILENCE_THRESHOLD` — порог тишины для instrumental

**Классы данных:**
- `VolumeSegment` — сегмент с параметрами громкости
- `SegmentScore` — метрики подсегмента
- `FrameFeatures` — признаки для каждой секунды

---

### [`VocalProcessor`](app/vocal_processor.py)

Обработка вокала с эффектом бэк-вокала.

**Функции:**
- Применение volume automation по сегментам
- Микширование вокала с инструменталом
- Создание фиксированного микса (для 3-й аудиодорожки)

**Методы:**
```python
async def process_and_mix(
    self,
    instrumental_file: str,
    vocal_file: str,
    volume_segments: list[VolumeSegment],
    output_file: str
) -> None

async def mix_instrumental_and_vocal_fixed_volume(...) -> None
```

---

### [`AlignmentService`](app/alignment_service.py)

Выравнивание текста песни по таймкодам транскрипции.

**Стратегии выравнивания:**
- `LrcDirectStrategy` — если транскрипция содержит LRC-совместимые таймкоды
- `SequenceAlignmentStrategy` — алгоритм Нидлмана-Вунша для последовательностей

**Корректировка таймингов:**
- Вставка "(проигрыш)" перед длинными паузами
- Параметры `MAX_WORD_TIME` и `NORMAL_WORD_TIME`

---

### [`AssGenerator`](app/ass_generator.py)

Генерация субтитров в формате ASS (Advanced SubStation Alpha).

**Функции:**
- Караоке-эффекты (перекрашивание по словам)
- Countdown в instrumental-сегментах (3-2-1)
- Превью следующей строки
- Отображение данных сегментов

**Конфигурация:**
- `ASS_FONT_SIZE` — размер шрифта
- `ASS_COUNTDOWN_ENABLED` — включить countdown
- `ASS_COUNTDOWN_SECONDS` — длительность countdown

---

### [`VideoRenderer`](app/video_renderer.py)

Рендеринг финального караоке-видео через ffmpeg.

**Аудиодорожки в MP4:**
1. **Instrumental** — чистая минусовка
2. **Original** — исходный трек
3. **Instrumental + Voice (40%)** — микс
4. **Backvocal mix** — инструментал + обработанный вокал (опционально)

**Конфигурация:**
- `VIDEO_WIDTH`, `VIDEO_HEIGHT` — разрешение
- `VIDEO_FFMPEG_PRESET` — пресет кодирования
- `VIDEO_FFMPEG_CRF` — качество (0-51)

---

### [`TrackVisualizer`](app/track_visualizer.py)

Визуализация сегментов трека в виде PNG timeline.

**Слои визуализации:**
1. Сегменты трека (цвета по типу)
2. Транскрипция (слова и сегменты)
3. Выровненный текст (строки)
4. Метрики (vocal_energy, sim_score, hpss_score)

**Конфигурация:**
- `TRACK_VISUALIZATION_ENABLED` — включить генерацию

---

### [`CorrectTranscriptService`](app/correct_transcript_service.py)

Корректировка транскрипции с использованием LLM на основе текста песни.

**Процесс:**
1. Получение оригинальной транскрипции
2. Сравнение с текстом песни
3. Исправление ошибок распознавания
4. Сохранение скорректированной версии

**Конфигурация:**
- `CORRECT_TRANSCRIPT_ENABLED` — включить шаг

---

### [`SegmentChangeService`](app/segment_change_service.py)

Сервис для изменения типа сегментов через команду `/change`.

**Функции:**
- Парсинг диапазонов (например, `1,2,3`, `5-10`, `1,3,5-7`)
- Изменение типа сегмента (chorus, verse, instrumental)
- Автоматический пересчёт громкости

---

## Сервисы загрузки

### [`YandexMusicDownloader`](app/yandex_music_downloader.py)

Загрузка треков с Яндекс Музыки.

**Возможности:**
- Загрузка по URL трека/альбома
- Получение LRC-текстов (синхронизированных)
- Поддержка FLAC для lossless треков

**Конфигурация:**
- `YANDEX_MUSIC_TOKEN` — токен авторизации

---

### [`YouTubeDownloader`](app/youtube_downloader.py)

Загрузка аудио с YouTube.

**Возможности:**
- Загрузка лучшего доступного аудио
- Валидация длительности (> 60 сек)
- Извлечение метаданных

**Конфигурация:**
- `YOUTUBE_DOWNLOAD_QUALITY` — качество (best, worst, 192)

---

## Утилиты и конфигурация

### [`Settings`](app/config.py)

Pydantic-модель для загрузки конфигурации из переменных окружения.

**Особенности:**
- Загрузка из `.env` файла
- Маскирование чувствительных полей при логировании
- Управление списком разрешённых/отклонённых пользователей

**Методы:**
```python
def add_allowed_user(user_id: int, user_name: str | None) -> None
def add_denied_user(user_id: int, user_name: str | None) -> None
def is_user_allowed(user_id: int) -> bool
def is_user_denied(user_id: int) -> bool
```

---

### [`ConfigWatcher`](app/config_watcher.py)

Горячая перезагрузка конфигурации без перезапуска бота.

**Принцип работы:**
1. Мониторинг `mtime` файла `.env`
2. Перечитывание при изменении
3. Обновление полей `Settings` (кроме токена и логирования)

**Конфигурация:**
- `ENV_RELOAD_ENABLED` — включить
- `ENV_RELOAD_INTERVAL_SEC` — интервал проверки

---

### [`utils.py`](app/utils.py)

Вспомогательные функции.

**Функции:**
```python
def normalize_filename(filename: str) -> str
# Нормализация имени файла: только буквы, цифры, дефис, подчёркивание, пробел
```
