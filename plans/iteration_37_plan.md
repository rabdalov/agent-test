# Итерация 37 — Отображение данных сегментов в ASS субтитрах

## Цель

Добавить в ASS генератор слой `Dialogue: 0` с информацией из `volume_segments_file` для каждого сегмента трека. Данные отображаются в верхней части экрана (1/4 высоты сверху) с использованием стиля `Segments`.

---

## Описание задачи

### Что нужно сделать

В шаге `GENERATE_ASS` дополнительно читать файл `volume_segments_file` (если он существует в `PipelineState`) и для каждого сегмента добавлять строку `Dialogue: 0` со следующими полями из `scores`:

- `segment_type` — тип сегмента (`chorus`, `verse`, `bridge`, `intro`, `outro`, `instrumental`)
- `volume` — громкость вокала в сегменте
- `vocal_energy` — энергия вокала
- `chroma_variance` — дисперсия хроматограммы
- `sim_score` — оценка повторяемости
- `hpss_score` — оценка гармонической энергии

### Формат отображения

Каждый сегмент отображается в виде одной строки текста в верхней части экрана:

```
[chorus] vol:0.30 energy:0.72 chroma:0.45 sim:0.81 hpss:0.63
```

### Позиционирование

Стиль `Segments` располагается на 1/4 высоты экрана сверху (при `PlayResY: 1080` — это `270px` от верха).

Используется `Alignment=8` (top-centre) с `MarginV=270`.

---

## Структура `volume_segments_file`

Файл `{track_stem}_volume_segments.json` содержит список объектов [`VolumeSegment`](../app/chorus_detector.py):

```json
[
  {
    "start": 0.0,
    "end": 15.3,
    "volume": 0.4,
    "segment_type": "intro",
    "backend": "dual_file",
    "scores": {
      "vocal_energy": 0.12,
      "chroma_variance": 0.33,
      "sim_score": 0.45,
      "hpss_score": 0.51,
      "tempo_score": 0.60
    }
  },
  ...
]
```

---

## Детальный план реализации

### Шаг 1. Добавить стиль `Segments` в ASS-заголовок

**Файл:** [`app/ass_generator.py`](../app/ass_generator.py)

Добавить строку стиля `Segments` в `_ASS_HEADER_TEMPLATE` после строки `Title`:

```
Style: Segments,   Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,8,30,30,270,1
```

Параметры стиля `Segments`:
- Шрифт: `Arial`, размер `60` (аналогично `Title`)
- Цвет: белый `&H00FFFFFF`
- `Alignment=8` — top-centre (верхний центр)
- `MarginV=270` — отступ 270px от верха (1/4 от 1080px)

> **Примечание:** Размер шрифта `60` — это значение по умолчанию. В шаблоне используется `{font_size}`, поэтому стиль `Segments` также будет использовать `{font_size}`.

### Шаг 2. Обновить сигнатуру метода `generate()`

**Файл:** [`app/ass_generator.py`](../app/ass_generator.py)

Добавить опциональный параметр `volume_segments_path`:

```python
def generate(
    self,
    aligned_json_path: Path,
    output_ass_path: Path,
    track_title: str = "",
    volume_segments_path: Path | None = None,
) -> None:
```

### Шаг 3. Реализовать загрузку и генерацию диалогов сегментов

**Файл:** [`app/ass_generator.py`](../app/ass_generator.py)

Добавить приватный метод `_build_segment_info_dialogues()`:

```python
@staticmethod
def _build_segment_info_dialogues(
    volume_segments: list[dict],
) -> list[str]:
    """Build Dialogue lines for volume segments info overlay.

    Each segment produces one Dialogue line in the Segments style,
    displayed at the top-quarter of the screen for the duration of
    the segment.

    Format: [segment_type] vol:{volume:.2f} energy:{vocal_energy:.2f}
            chroma:{chroma_variance:.2f} sim:{sim_score:.2f} hpss:{hpss_score:.2f}
    """
    lines: list[str] = []
    for seg in volume_segments:
        start: float = seg.get("start", 0.0)
        end: float = seg.get("end", 0.0)
        seg_type: str = seg.get("segment_type", "unknown")
        volume: float = seg.get("volume", 0.0)
        scores: dict = seg.get("scores", {})

        vocal_energy: float = scores.get("vocal_energy", 0.0)
        chroma_variance: float = scores.get("chroma_variance", 0.0)
        sim_score: float = scores.get("sim_score", 0.0)
        hpss_score: float = scores.get("hpss_score", 0.0)

        text = (
            f"[{seg_type}] "
            f"vol:{volume:.2f} "
            f"energy:{vocal_energy:.2f} "
            f"chroma:{chroma_variance:.2f} "
            f"sim:{sim_score:.2f} "
            f"hpss:{hpss_score:.2f}"
        )

        lines.append(
            f"Dialogue: 0,"
            f"{_format_ass_time(start)},"
            f"{_format_ass_time(end)},"
            f"Segments,,0,0,0,,{text}\n"
        )
    return lines
```

### Шаг 4. Интегрировать генерацию в метод `generate()`

**Файл:** [`app/ass_generator.py`](../app/ass_generator.py)

В методе `generate()` после генерации строки `Title` добавить блок:

```python
# Volume segments info overlay (optional)
if volume_segments_path is not None and volume_segments_path.exists():
    try:
        volume_segments: list[dict] = json.loads(
            volume_segments_path.read_text(encoding="utf-8")
        )
        lines.extend(self._build_segment_info_dialogues(volume_segments))
        logger.info(
            "AssGenerator: added %d segment info overlays from '%s'",
            len(volume_segments),
            volume_segments_path,
        )
    except Exception as exc:
        logger.warning(
            "AssGenerator: failed to load volume_segments_file '%s': %s",
            volume_segments_path,
            exc,
        )
```

### Шаг 5. Обновить вызов `generate()` в пайплайне

**Файл:** [`app/pipeline.py`](../app/pipeline.py)

В методе `_step_generate_ass()` передать `volume_segments_path` при наличии:

```python
volume_segments_path: Path | None = None
if self._state.volume_segments_file:
    vsp = Path(self._state.volume_segments_file)
    if vsp.exists():
        volume_segments_path = vsp

generator.generate(
    aligned_json_path=Path(aligned_path),
    output_ass_path=ass_path,
    track_title=self._state.track_stem or "",
    volume_segments_path=volume_segments_path,
)
```

---

## Изменяемые файлы

| Файл | Тип изменений |
|------|--------------|
| [`app/ass_generator.py`](../app/ass_generator.py) | Добавить стиль `Segments` в заголовок; добавить параметр `volume_segments_path` в `generate()`; добавить метод `_build_segment_info_dialogues()` |
| [`app/pipeline.py`](../app/pipeline.py) | Передать `volume_segments_path` в вызов `generator.generate()` в методе `_step_generate_ass()` |

---

## Что НЕ меняется

- Структура [`VolumeSegment`](../app/chorus_detector.py) — читается как есть из JSON.
- Логика генерации субтитров для слов и сегментов — не затрагивается.
- Стили `ActiveLine`, `Highlight`, `NextLine`, `Title` — не изменяются.
- Если `volume_segments_file` отсутствует или не задан — поведение прежнее (обратная совместимость).

---

## Пример результирующего ASS

```ass
[Script Info]
Title: Godsmack - Nothing Else Matters
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, ...
Style: ActiveLine, Arial,60,...
Style: Highlight,  Arial,60,...
Style: NextLine,   Arial,60,...
Style: Title,      Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,8,30,30,50,1
Style: Segments,   Arial,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,8,30,30,270,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:04:55.05,Title,,0,0,0,,Godsmack - Nothing Else Matters
Dialogue: 0,0:00:00.00,0:00:15.30,Segments,,0,0,0,,[intro] vol:0.40 energy:0.12 chroma:0.33 sim:0.45 hpss:0.51
Dialogue: 0,0:00:15.30,0:00:45.80,Segments,,0,0,0,,[verse] vol:0.40 energy:0.65 chroma:0.52 sim:0.38 hpss:0.44
Dialogue: 0,0:00:45.80,0:01:20.10,Segments,,0,0,0,,[chorus] vol:0.30 energy:0.88 chroma:0.71 sim:0.82 hpss:0.79
...
Dialogue: 0,0:00:00.00,0:00:15.30,ActiveLine,,0,0,0,,Текст первой строки
...
```

---

## Проверка

- После выполнения шага `GENERATE_ASS` при наличии `volume_segments_file` в ASS-файле присутствуют строки `Dialogue: 0` со стилем `Segments`.
- Каждая строка `Segments` соответствует одному сегменту из `volume_segments_file` с корректными таймингами.
- Текст строки содержит все шесть полей: `segment_type`, `volume`, `vocal_energy`, `chroma_variance`, `sim_score`, `hpss_score`.
- При отсутствии `volume_segments_file` ASS-файл генерируется без изменений (обратная совместимость).
- Стиль `Segments` отображается на 1/4 высоты экрана сверху (MarginV=270 при PlayResY=1080).
