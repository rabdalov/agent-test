# Итерация 35 — Разделение шага MIX_AUDIO на DETECT_CHORUS и MIX_AUDIO

## Статус

- [x] Выполнено

## Задача

Разделить шаг `MIX_AUDIO` на два самостоятельных шага для повышения прозрачности и возможности перезапуска с промежуточного состояния.

## Новая последовательность шагов pipeline

```
DOWNLOAD → ASK_LANGUAGE → GET_LYRICS → SEPARATE → TRANSCRIBE → DETECT_CHORUS → CORRECT_TRANSCRIPT → ALIGN → MIX_AUDIO → GENERATE_ASS → RENDER_VIDEO → SEND_VIDEO
```

## Описание новых шагов

### Шаг `DETECT_CHORUS`

- Запускает [`ChorusDetector.detect()`](app/chorus_detector.py) для определения временных отрезков припевов.
- Формирует `volume_segments` через [`VocalProcessor.build_volume_segments()`](app/vocal_processor.py).
- Сохраняет разметку в `{track_stem}_volume_segments.json`.
- Сохраняет путь в [`PipelineState.volume_segments_file`](app/models.py).
- Пропускается (как и раньше), если `MIX_AUDIO_ENABLED=false`.

### Шаг `MIX_AUDIO`

- Загружает готовый `volume_segments_file` через [`VocalProcessor.load_volume_segments()`](app/vocal_processor.py).
- Применяет разметку громкости к вокальной дорожке → `processed_vocal_file`.
- Создаёт микс `instrumental + processed_vocal` → `backvocal_mix_file`.

## Изменения в файлах

### [`app/models.py`](app/models.py)

- Добавлен `DETECT_CHORUS` в `PipelineStep` (между `TRANSCRIBE` и `CORRECT_TRANSCRIPT`).
- `MIX_AUDIO` перенесён после `ALIGN`.

### [`app/pipeline.py`](app/pipeline.py)

- Обновлены `_STEP_LABELS`, `_ORDERED_STEPS`, `_STEP_REQUIRED_ARTIFACTS`, `step_methods`.
- Добавлен метод `_step_detect_chorus()`.
- Обновлён `_step_mix_audio()`.

### [`app/vocal_processor.py`](app/vocal_processor.py)

- Добавлен статический метод `load_volume_segments()`.

## Проверка

После шага `DETECT_CHORUS` в папке трека появляется `{track_stem}_volume_segments.json`; шаг `MIX_AUDIO` использует этот файл для создания `processed_vocal_file` и `backvocal_mix_file`.
