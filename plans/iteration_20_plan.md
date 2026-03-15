# Итерация 20 — Рендеринг видео с несколькими аудиодорожками

## Цель

Модифицировать шаг `RENDER_VIDEO` для создания MP4 с тремя переключаемыми аудиодорожками.

## Аудиодорожки

1. **Instrumental** — чистая инструментальная дорожка (без вокала)
2. **Original** — полный исходный трек с вокалом
3. **Instrumental+Voice** — микс: инструментальная дорожка + вокал на настраиваемую громкость (по умолчанию 40%)

## Источники данных из state

- `track_source` — исходный аудиофайл (используется как дорожка **Original**)
- `vocal_file` — файл вокала (используется для микса с инструменталом в дорожке **Instrumental+Voice**)
- Инструментальная дорожка берётся из результата работы `demucs` (уже присутствует в state после шага разделения)

## Технические требования

### ffmpeg мультиплексирование

Применить `ffmpeg` для мультиплексирования трёх аудиодорожек в один MP4:

```bash
ffmpeg \
  -i video_no_audio.mp4 \
  -i instrumental.wav \
  -i original.mp3 \
  -i mix_instrumental_voice.wav \
  -map 0:v \
  -map 1:a -map 2:a -map 3:a \
  -metadata:s:a:0 title="Instrumental" \
  -metadata:s:a:1 title="Original" \
  -metadata:s:a:2 title="Instrumental+Voice" \
  -c:v copy -c:a aac \
  output.mp4
```

### Микс Instrumental+Voice

Для создания дорожки **Instrumental+Voice** использовать `ffmpeg` фильтр `amix` или `amerge` с регулировкой громкости голоса:

```bash
ffmpeg \
  -i instrumental.wav \
  -i vocal.wav \
  -filter_complex "[1:a]volume={VOICE_VOLUME}[v];[0:a][v]amix=inputs=2:duration=first[out]" \
  -map "[out]" \
  mix_instrumental_voice.wav
```

где `{VOICE_VOLUME}` — значение из параметра `AUDIO_MIX_VOICE_VOLUME` (по умолчанию `0.4`).

## Конфигурация

### Новый параметр в `.env`

```env
# Громкость голоса в миксе Instrumental+Voice (0.0 - 1.0, по умолчанию 0.4)
AUDIO_MIX_VOICE_VOLUME=0.4
```

Добавить параметр в [`app/config.py`](../app/config.py) в класс конфигурации:

```python
AUDIO_MIX_VOICE_VOLUME: float = 0.4
```

## Изменения в коде

### [`app/video_renderer.py`](../app/video_renderer.py)

- Добавить метод `create_voice_mix()` для создания смешанной дорожки Instrumental+Voice
- Модифицировать основной метод рендеринга для принятия трёх аудиодорожек
- Добавить метаданные дорожек (`-metadata:s:a:N title="..."`) в ffmpeg-команду

### [`app/pipeline.py`](../app/pipeline.py)

- В шаге `RENDER_VIDEO` передавать в рендерер:
  - `state.instrumental_file` — инструментальная дорожка
  - `state.track_source` — оригинальный трек
  - `state.vocal_file` — файл вокала для микса
- Вызывать `create_voice_mix()` перед финальным рендерингом

### [`app/config.py`](../app/config.py)

- Добавить поле `AUDIO_MIX_VOICE_VOLUME: float = 0.4`

### [`example.env`](../example.env)

- Добавить документированный параметр `AUDIO_MIX_VOICE_VOLUME=0.4`

## Проверка результата

- В итоговом MP4 можно переключаться между тремя аудиодорожками в медиаплеере (VLC, mpv и др.)
- Дорожки имеют читаемые названия: `Instrumental`, `Original`, `Instrumental+Voice`
- Громкость голоса в миксе регулируется через параметр `AUDIO_MIX_VOICE_VOLUME`
- Видеодорожка копируется без перекодирования (`-c:v copy`) для сохранения качества и скорости

## Связанные файлы

- [`app/video_renderer.py`](../app/video_renderer.py) — основной модуль рендеринга
- [`app/pipeline.py`](../app/pipeline.py) — пайплайн обработки
- [`app/config.py`](../app/config.py) — конфигурация приложения
- [`app/models.py`](../app/models.py) — модели состояния (state)
- [`example.env`](../example.env) — пример конфигурации окружения
