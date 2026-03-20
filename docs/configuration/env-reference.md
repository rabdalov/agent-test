# Справочник переменных окружения

Полный список всех переменных окружения для конфигурации караоке-бота.

---

## Обязательные переменные

| Переменная | Описание | Пример |
|------------|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Токен Telegram бота от @BotFather | `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11` |
| `ADMIN_ID` | Telegram ID администратора бота | `123456789` |

---

## Telegram

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `TELEGRAM_BOT_TOKEN` | Токен бота | — | `123456:ABC...` |
| `ADMIN_ID` | ID администратора | — | `123456789` |
| `TLG_ALLOWED_ID` | Список разрешённых ID (JSON массив) | `[]` | `[123456789,987654321]` |

---

## Пути и хранение

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `TRACKS_ROOT_DIR` | Корневая папка для треков | `./tracks` | `/home/user/tracks` или `I:\karaoke\music` |

---

## Логирование

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `LOG_LEVEL` | Уровень логирования | `INFO` | DEBUG, INFO, WARNING, ERROR |

---

## Demucs (разделение дорожек)

| Переменная | Описание | По умолчанию | Диапазон/Пример |
|------------|----------|--------------|-----------------|
| `DEMUCS_MODEL` | Модель Demucs | `htdemucs` | htdemucs, htdemucs_ft, htdemucs_6s |
| `DEMUCS_OUTPUT_FORMAT` | Формат выхода | `mp3` | mp3, wav, flac |

---

## Speeches.ai (транскрибация)

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `SPEECHES_BASE_URL` | URL сервиса транскрибации | `http://localhost:8000` | `http://192.168.0.206:8001` |
| `TRANSCRIPTION_MODEL_ID` | Модель Whisper | `whisper-1` | `Systran/faster-whisper-medium` |
| `LANG_DEFAULT` | Язык по умолчанию | `ru` | ru, en, auto |
| `PROMPT_SPEECHES` | Промпт для Whisper | `""` | "Музыкальная транскрипция" |
| `SPEECHES_TIMEOUT` | Таймаут запроса (сек) | `300` | 60-600 |

---

## OpenRouter (LLM)

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `OPENROUTER_API_KEY` | API ключ OpenRouter | — | `sk-or-v1-...` |
| `OPENROUTER_MODEL` | Модель LLM | `qwen/qwen3-next-80b-a3b-instruct:free` | `qwen/qwen3.5-397b-a17b` |
| `OPENROUTER_API` | URL API | `https://api.openrouter.ai/v1` | `https://openrouter.ai/api/v1` |

---

## Lyrics провайдеры

### Genius

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `GENIUS_TOKEN` | Токен Genius API | — | `abc123...` |
| `LYRICS_ENABLE_GENIUS` | Включить Genius | `false` | true, false |

### LyricaV2

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `LYRICS_ENABLE_LYRICA` | Включить LyricaV2 | `false` | true, false |
| `LYRICA_BASE_URL` | URL сервиса LyricaV2 | `http://localhost:5000` | `https://lyrics.example.com` |

### lyrics-lib

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `LYRICS_ENABLE_LYRICSLIB` | Включить lyrics-lib | `false` | true, false |

---

## Yandex Music

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `YANDEX_MUSIC_TOKEN` | Токен авторизации Яндекс Музыки | — | `AQAAAA...` |

---

## YouTube

| Переменная | Описание | По умолчанию | Диапазон/Пример |
|------------|----------|--------------|-----------------|
| `YOUTUBE_DOWNLOAD_QUALITY` | Качество аудио | `best` | best, worst, 192, 128 |

---

## Video Render (ffmpeg)

| Переменная | Описание | По умолчанию | Диапазон/Пример |
|------------|----------|--------------|-----------------|
| `VIDEO_WIDTH` | Ширина видео | `1280` | 640-3840 |
| `VIDEO_HEIGHT` | Высота видео | `720` | 480-2160 |
| `VIDEO_BACKGROUND_COLOR` | Цвет фона | `black` | black, white, #RRGGBB |
| `VIDEO_FFMPEG_PRESET` | Пресет кодирования | `fast` | ultrafast, superfast, veryfast, faster, fast, medium, slow |
| `VIDEO_FFMPEG_CRF` | Качество (0=лучшее, 51=худшее) | `22` | 0-51 |

### Рекомендации по CRF и preset

| Preset | CRF | Качество | Скорость | Размер |
|--------|-----|----------|----------|--------|
| `ultrafast` | 28 | Низкое | Очень быстро | Большой |
| `fast` | 22 | Хорошее | Быстро | Средний |
| `medium` | 18 | Отличное | Медленно | Меньше |

---

## Chorus Detection

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `DETECT_CHORUS_ENABLED` | Включить детектирование | `true` | true, false |
| `CHORUS_MIN_DURATION_SEC` | Мин. длительность сегмента | `5.0` | 1.0-30.0 |
| `CHORUS_VOCAL_SILENCE_THRESHOLD` | Порог тишины для instrumental | `0.05` | 0.0-1.0 |
| `CHORUS_BOUNDARY_MERGE_TOLERANCE_SEC` | Допуск объединения границ | `2.0` | 0.0-5.0 |

---

## Mix Audio (бэк-вокал)

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `MIX_AUDIO_ENABLED` | Включить микширование | `true` | true, false |
| `CHORUS_BACKVOCAL_VOLUME` | Громкость в припевах | `0.3` | 0.0-1.0 |
| `AUDIO_MIX_VOICE_VOLUME` | Громкость вокала в миксе | `0.4` | 0.0-1.0 |
| `VOCAL_REVERB_ENABLED` | Эффект реверба | `false` | true, false |
| `VOCAL_ECHO_ENABLED` | Эффект эха | `false` | true, false |

### Рекомендуемые значения громкости

| Сценарий | `CHORUS_BACKVOCAL_VOLUME` | `AUDIO_MIX_VOICE_VOLUME` |
|----------|---------------------------|--------------------------|
| Минимальный бэк-вокал | 0.1 | 0.3 |
| Стандартный | 0.3 | 0.4 |
| Сильный бэк-вокал | 0.5 | 0.4 |
| Без бэк-вокала | 0.0 | 0.4 |

---

## Align (выравнивание текста)

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `MAX_WORD_TIME` | Макс. длительность первого слова | `5.0` | 1.0-10.0 |
| `NORMAL_WORD_TIME` | Нормальная длительность слова | `1.5` | 0.5-3.0 |

---

## ASS Subtitles (субтитры)

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `ASS_FONT_SIZE` | Размер шрифта (пиксели) | `60` | 20-100 |
| `ASS_PREVIEW_OFFSET` | Сдвиг превью (сек) | `0.5` | 0.0-2.0 |
| `ASS_COUNTDOWN_ENABLED` | Обратный отсчёт | `true` | true, false |
| `ASS_COUNTDOWN_SECONDS` | Длительность countdown | `3` | 1-5 |

---

## Output (вывод)

| Переменная | Описание | По умолчанию | Пример |
|------------|----------|--------------|--------|
| `SEND_VIDEO_TO_USER` | Отправлять видео в Telegram | `true` | true, false |
| `CONTENT_EXTERNAL_URL` | URL для ссылок на скачивание | — | `https://content.example.com` |

### Формат ссылки

Если `CONTENT_EXTERNAL_URL=https://content.example.com`, финальная ссылка:
```
https://content.example.com/music?getfile=Artist%20-%20Song/Artist%20-%20Song.mp4
```

---

## Feature Flags (функциональные переключатели)

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `CORRECT_TRANSCRIPT_ENABLED` | Корректировка транскрипции LLM | `true` | true, false |
| `TRACK_VISUALIZATION_ENABLED` | PNG визуализация трека | `false` | true, false |

---

## Hot Reload (горячая перезагрузка)

| Переменная | Описание | По умолчанию | Диапазон |
|------------|----------|--------------|----------|
| `ENV_RELOAD_ENABLED` | Включить hot reload | `true` | true, false |
| `ENV_RELOAD_INTERVAL_SEC` | Интервал проверки (сек) | `30` | 5-300 |

---

## Примеры конфигураций

### Минимальная конфигурация

```bash
TELEGRAM_BOT_TOKEN=your_token
ADMIN_ID=123456789
TRACKS_ROOT_DIR=/tracks
```

### Полная конфигурация (рекомендуемая)

```bash
# Base
LOG_LEVEL=INFO
TELEGRAM_BOT_TOKEN=your_token
ADMIN_ID=123456789
TLG_ALLOWED_ID=[123456789]
TRACKS_ROOT_DIR=/tracks

# Demucs
DEMUCS_MODEL=htdemucs
DEMUCS_OUTPUT_FORMAT=mp3

# Speeches
SPEECHES_BASE_URL=http://192.168.0.206:8001
TRANSCRIPTION_MODEL_ID=Systran/faster-whisper-medium
LANG_DEFAULT=ru
SPEECHES_TIMEOUT=300

# OpenRouter
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=qwen/qwen3.5-397b-a17b

# Lyrics
LYRICS_ENABLE_LYRICA=true
LYRICA_BASE_URL=http://localhost:5000

# Yandex
YANDEX_MUSIC_TOKEN=your_token

# YouTube
YOUTUBE_DOWNLOAD_QUALITY=best

# Video
VIDEO_WIDTH=1280
VIDEO_HEIGHT=720
VIDEO_FFMPEG_PRESET=fast
VIDEO_FFMPEG_CRF=22

# Chorus
CHORUS_BACKVOCAL_VOLUME=0.3
AUDIO_MIX_VOICE_VOLUME=0.4

# ASS
ASS_FONT_SIZE=60
ASS_COUNTDOWN_ENABLED=true
ASS_COUNTDOWN_SECONDS=3

# Output
SEND_VIDEO_TO_USER=true
CONTENT_EXTERNAL_URL=https://your-domain.com

# Features
CORRECT_TRANSCRIPT_ENABLED=true
TRACK_VISUALIZATION_ENABLED=false

# Hot reload
ENV_RELOAD_ENABLED=true
ENV_RELOAD_INTERVAL_SEC=30
```

### Конфигурация для разработки

```bash
LOG_LEVEL=DEBUG
VIDEO_FFMPEG_PRESET=ultrafast
VIDEO_FFMPEG_CRF=28
SEND_VIDEO_TO_USER=false
TRACK_VISUALIZATION_ENABLED=true
```

---

## Чек-лист настройки

- [ ] Создать бота в @BotFather, получить `TELEGRAM_BOT_TOKEN`
- [ ] Узнать свой Telegram ID для `ADMIN_ID`
- [ ] Настроить `TRACKS_ROOT_DIR` с достаточным местом
- [ ] Установить и настроить сервис транскрибации (speeches.ai)
- [ ] Получить API ключ OpenRouter (опционально, для CORRECT_TRANSCRIPT)
- [ ] Настроить провайдеры текстов песен (опционально)
- [ ] Получить токен Яндекс Музыки (опционально)
- [ ] Настроить `CONTENT_EXTERNAL_URL` для ссылок (опционально)
