# Итерация 39 — Интеграция TrackVisualizer в пайплайн

## Цель

Подключить модуль [`app/track_visualizer.py`](../app/track_visualizer.py) (реализованный в итерации 38) к пайплайну: добавить опциональный вызов генерации PNG-визуализации в шаге `GENERATE_ASS`, управляемый конфигурационным флагом `TRACK_VISUALIZATION_ENABLED`.

> **Предусловие:** итерация 38 завершена — класс `TrackVisualizer` реализован и протестирован через скрипт [`scripts/visualize_track.py`](../scripts/visualize_track.py).

---

## Описание задачи

После завершения шага `GENERATE_ASS` пайплайн опционально вызывает `TrackVisualizer.generate()`, передавая пути к доступным артефактам из `PipelineState`. Результирующий PNG сохраняется в папке трека, путь записывается в `PipelineState.visualization_file`.

При ошибке генерации визуализации пайплайн **не прерывается** — ошибка логируется как WARNING.

---

## Детальный план реализации

### Шаг 1. Добавить поле `visualization_file` в `PipelineState`

**Файл:** [`app/models.py`](../app/models.py)

```python
class PipelineState(BaseModel):
    ...
    visualization_file: str | None = None  # Путь к PNG-файлу визуализации timeline
```

### Шаг 2. Добавить параметр конфигурации

**Файл:** [`app/config.py`](../app/config.py)

```python
# Track visualization settings
# Enable/disable PNG timeline visualization after GENERATE_ASS step (default: false)
track_visualization_enabled: bool = False
```

В методе `from_env()`:

```python
"track_visualization_enabled": os.getenv("TRACK_VISUALIZATION_ENABLED", "false").lower() in ("true", "1", "yes"),
```

**Файл:** [`example.env`](../example.env)

```env
# Включить генерацию PNG-визуализации timeline сегментов после шага GENERATE_ASS (default: false)
TRACK_VISUALIZATION_ENABLED=false
```

### Шаг 3. Добавить вызов `TrackVisualizer` в шаг `GENERATE_ASS`

**Файл:** [`app/pipeline.py`](../app/pipeline.py)

В методе `_step_generate_ass()` после успешной генерации ASS-файла добавить блок:

```python
# Опциональная визуализация сегментов
if self._settings.track_visualization_enabled:
    from app.track_visualizer import TrackVisualizer
    viz_path = track_dir / f"{stem}_timeline.png"
    visualizer = TrackVisualizer()
    try:
        visualizer.generate(
            output_path=viz_path,
            transcribe_json_file=(
                Path(self._state.transcribe_json_file)
                if self._state.transcribe_json_file else None
            ),
            corrected_transcribe_json_file=(
                Path(self._state.corrected_transcribe_json_file)
                if self._state.corrected_transcribe_json_file else None
            ),
            aligned_lyrics_file=Path(aligned_path),
            source_lyrics_file=(
                Path(self._state.source_lyrics_file)
                if self._state.source_lyrics_file else None
            ),
            volume_segments_file=volume_segments_path,
            track_title=self._state.track_stem or "",
        )
        self._state.visualization_file = str(viz_path)
        self._save_state()
        logger.info(
            "TrackVisualizer: saved timeline to '%s'", viz_path
        )
    except Exception as exc:
        logger.warning(
            "TrackVisualizer: failed to generate visualization: %s", exc
        )
```

> Импорт `TrackVisualizer` выполняется внутри блока `if` — это позволяет не загружать `matplotlib` при `TRACK_VISUALIZATION_ENABLED=false`.

---

## Изменяемые файлы

| Файл | Тип изменений |
|------|--------------|
| [`app/models.py`](../app/models.py) | Добавить поле `visualization_file` в `PipelineState` |
| [`app/config.py`](../app/config.py) | Добавить параметр `track_visualization_enabled` |
| [`example.env`](../example.env) | Добавить `TRACK_VISUALIZATION_ENABLED=false` |
| [`app/pipeline.py`](../app/pipeline.py) | Добавить опциональный вызов `TrackVisualizer.generate()` в `_step_generate_ass()` |

---

## Что НЕ меняется

- Класс [`TrackVisualizer`](../app/track_visualizer.py) — без изменений
- Логика шага `GENERATE_ASS` — визуализация добавляется после основной генерации ASS
- Все остальные шаги пайплайна — без изменений
- При `TRACK_VISUALIZATION_ENABLED=false` (по умолчанию) поведение пайплайна идентично предыдущей версии

---

## Параметры конфигурации

| Параметр | Переменная .env | По умолчанию | Описание |
|---|---|---|---|
| `track_visualization_enabled` | `TRACK_VISUALIZATION_ENABLED` | `false` | Включить генерацию PNG-визуализации после шага GENERATE_ASS |

---

## Проверка

- При `TRACK_VISUALIZATION_ENABLED=true` после шага `GENERATE_ASS` в папке трека появляется файл `{track_stem}_timeline.png`
- Путь к PNG сохраняется в `PipelineState.visualization_file` и в `state.json`
- При ошибке генерации визуализации пайплайн продолжает работу, ошибка логируется как WARNING
- При `TRACK_VISUALIZATION_ENABLED=false` (по умолчанию) шаг пропускается, `matplotlib` не импортируется
- Горячая перезагрузка конфигурации (итерация 32) корректно применяет изменение `TRACK_VISUALIZATION_ENABLED`
