# Итерация 33 — Функция "бэк-вокал" в припевах из дорожки Vocal

## Задача

Реализовать автоматическое снижение громкости вокальной дорожки вне припевов и повышение в припевах, создавая эффект "бэк-вокала" в аудиомиксе.

## Шаг пайплайна

Новый шаг `MIX_AUDIO` добавляется после `SEPARATE` и перед `TRANSCRIBE`.

### Итоговая последовательность шагов pipeline:

```
DOWNLOAD → ASK_LANGUAGE → GET_LYRICS → SEPARATE → TRANSCRIBE → MIX_AUDIO
→ CORRECT_TRANSCRIPT → ALIGN → GENERATE_ASS → RENDER_VIDEO → SEND_VIDEO
```

---

## Детальный план реализации

### 1. Определение припевов через `msaf` (spectral clustering)

- Добавить зависимость `msaf` в `pyproject.toml` через `uv add msaf`.
- Создать класс `ChorusDetector` в [`app/chorus_detector.py`](../app/chorus_detector.py):
  - Метод `detect(audio_file: str) -> list[tuple[float, float]]` — возвращает список временных отрезков `(start_sec, end_sec)` для каждого припева.
  - Использовать алгоритм `msaf.process(audio_file, boundaries_id="sf", labels_id="sc")` (spectral clustering).
  - Логировать найденные сегменты на уровне `DEBUG`.

### 2. Создание разметки целевой громкости вокала (`volume_segments`)

- На основе найденных припевов сформировать список сегментов `VolumeSegment(start, end, volume)`:
  - Во время припевов: `volume = CHORUS_BACKVOCAL_VOLUME` (из конфигурации, по умолчанию `0.3` = 30%).
  - В остальных моментах: `volume = AUDIO_MIX_VOICE_VOLUME` (из конфигурации, уже существует, по умолчанию `0.4` = 40%).
- Сохранить разметку в JSON-файл `{track_stem}_volume_segments.json` в папке трека.
- Добавить поле `volume_segments_file` в [`PipelineState`](../app/models.py).

### 3. Предусмотреть расширяемость обработки вокала

- Создать класс `VocalProcessor` в [`app/vocal_processor.py`](../app/vocal_processor.py):
  - Метод `process(vocal_file: str, volume_segments: list[VolumeSegment], output_file: str) -> str` — применяет разметку громкости к вокальной дорожке.
  - Реализовать применение громкости через `ffmpeg` с фильтром `volume` по временным сегментам.
  - Предусмотреть заглушки/хуки для будущих фильтров: реверберация, эхо, эквалайзер и т.д. (через список `processors: list[Callable]` или конфигурируемые флаги).
  - Добавить параметры конфигурации для включения/отключения каждого типа обработки:
    - `VOCAL_REVERB_ENABLED` (по умолчанию `false`)
    - `VOCAL_ECHO_ENABLED` (по умолчанию `false`)

### 4. Интеграция шага `MIX_AUDIO` в `KaraokePipeline`

- Добавить `MIX_AUDIO` в `PipelineStep` (enum в [`app/models.py`](../app/models.py)).
- Реализовать метод `_step_mix_audio()` в [`app/pipeline.py`](../app/pipeline.py):
  - Вызвать `ChorusDetector.detect(vocal_file)` для получения временных отрезков припевов.
  - Сформировать `volume_segments` на основе результата.
  - Вызвать `VocalProcessor.process()` для создания обработанной вокальной дорожки.
  - Сохранить путь к обработанному файлу в `PipelineState.processed_vocal_file`.
- Добавить поле `processed_vocal_file` в [`PipelineState`](../app/models.py).

### 5. Создание выходного MP3-файла микса

- На шаге `MIX_AUDIO` создать отдельный MP3-файл: `{track_stem}_backvocal_mix.mp3`.
- Файл содержит: `instrumental + processed_vocal` (с применённой разметкой громкости).
- Использовать `ffmpeg` с `amix` или `amerge` + `volume` фильтрами.
- Сохранить путь к файлу в `PipelineState.backvocal_mix_file`.
- Добавить поле `backvocal_mix_file` в [`PipelineState`](../app/models.py).

### 6. Обновление шага `RENDER_VIDEO`

- Добавить четвёртую аудиодорожку в итоговый MP4: **Instrumental + BackVocal** (из `backvocal_mix_file`).
- Если `backvocal_mix_file` отсутствует — дорожка не добавляется (обратная совместимость).

---

## Новые параметры конфигурации (`.env`)

| Параметр | По умолчанию | Описание |
|---|---|---|
| `CHORUS_BACKVOCAL_VOLUME` | `0.3` | Громкость вокала в припевах (30%) |
| `VOCAL_REVERB_ENABLED` | `false` | Включить реверберацию вокала |
| `VOCAL_ECHO_ENABLED` | `false` | Включить эхо вокала |
| `MIX_AUDIO_ENABLED` | `true` | Включить/отключить шаг `MIX_AUDIO` целиком |

---

## Новые файлы

| Файл | Описание |
|---|---|
| [`app/chorus_detector.py`](../app/chorus_detector.py) | Класс `ChorusDetector` — определение припевов через `msaf` |
| [`app/vocal_processor.py`](../app/vocal_processor.py) | Класс `VocalProcessor` — обработка вокала с разметкой громкости |

---

## Изменения в существующих файлах

| Файл | Изменения |
|---|---|
| [`app/models.py`](../app/models.py) | Добавить `MIX_AUDIO` в `PipelineStep`; добавить поля `volume_segments_file`, `processed_vocal_file`, `backvocal_mix_file` в `PipelineState` |
| [`app/pipeline.py`](../app/pipeline.py) | Добавить шаг `MIX_AUDIO` в `_ORDERED_STEPS`; реализовать `_step_mix_audio()` |
| [`app/video_renderer.py`](../app/video_renderer.py) | Добавить опциональную четвёртую аудиодорожку **Instrumental + BackVocal** |
| [`app/config.py`](../app/config.py) | Добавить новые параметры конфигурации |
| [`example.env`](../example.env) | Добавить новые параметры с комментариями |
| [`pyproject.toml`](../pyproject.toml) | Добавить зависимость `msaf` |

---

## Проверка

- После выполнения шага `MIX_AUDIO` в папке трека появляется файл `{track_stem}_backvocal_mix.mp3`.
- В итоговом MP4 доступна четвёртая аудиодорожка **Instrumental + BackVocal**.
- В припевах громкость вокала соответствует `CHORUS_BACKVOCAL_VOLUME`, вне припевов — `AUDIO_MIX_VOICE_VOLUME`.
- При `MIX_AUDIO_ENABLED=false` шаг пропускается, пайплайн продолжает работу без изменений.
