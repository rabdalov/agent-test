# Итерация 22 — Формирование ссылки на MP4 для скачивания

## Статус

- [x] Выполнено

## Описание

Добавить в шаг `RENDER_VIDEO` формирование публичной ссылки на готовый MP4-файл для скачивания.

Формат ссылки: `https://{external_url}/music?getfile={track_subdir}/{output_file}`

## План реализации

### 1. Конфигурация

Добавить параметр конфигурации `CONTENT_EXTERNAL_URL` в [`app/config.py`](../app/config.py) и [`.env`](../example.env) — внешний URL контент-сервера (например, `content.homeserver.top`).

### 2. Формирование ссылки в пайплайне

В классе [`KaraokePipeline`](../app/pipeline.py) после успешного выполнения шага `RENDER_VIDEO`:

- Сформировать ссылку на основе `track_stem` и `output_file` из [`PipelineState`](../app/models.py)
- Сохранить ссылку в [`PipelineState`](../app/models.py) (новое поле `download_url`)

### 3. Отправка ссылки пользователю в Telegram

Обновить обработчик завершения пайплайна в [`app/handlers_karaoke.py`](../app/handlers_karaoke.py):

- Отправить сообщение со ссылкой на скачивание MP4

## Проверка

После завершения пайплайна пользователь получает ссылку вида:

```
https://content.homeserver.top/music?getfile=track_subdir/filename.mp4
```

для скачивания готового караоке-видео.
