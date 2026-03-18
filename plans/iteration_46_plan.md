# План итерации 46: Детальные метрики в слое визуализации

## Краткое описание
Добавление агрегированных метрик с шагом 1 секунда (`T_metrics_aggregate=1s`) в отдельный файл `{track_stem}_metrics.json` и отображение детальных линий в слое метрик `TrackVisualizer` наряду с текущими агрегированными по сегментам линиями.

---

## Цели

1. **ChorusDetector**: вычислять метрики `vocal_energy`, `chroma_variance`, `hpss_score` с временным шагом 1 секунда (один проход с frame-level признаками)
2. **Формат хранения**: сохранять детальные метрики в отдельный файл `{track_stem}_metrics.json` (плоский массив точек)
3. **TrackVisualizer**: отрисовывать в слое метрик две линии для каждой метрики — текущую (по сегментам) и детальную (по 1с из отдельного файла)
4. **PipelineState**: добавить поле `detailed_metrics_file` для пути к файлу метрик

---

## 1. Модификация структуры данных (`app/chorus_detector.py`)

### 1.1 Новый dataclass `MetricsPoint`

```python
@dataclass
class MetricsPoint:
    """Точка метрики с временным шагом 1 секунда.
    
    Attributes
    ----------
    time:
        Временная метка в секундах (0, 1, 2, ...).
    vocal_energy:
        Нормализованная энергия вокала [0, 1].
    chroma_variance:
        Вариативность chroma features [0, 1].
    hpss_score:
        Harmonic-percussive separation score [0, 1].
    """
    time: float
    vocal_energy: float = 0.0
    chroma_variance: float = 0.0
    hpss_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "vocal_energy": self.vocal_energy,
            "chroma_variance": self.chroma_variance,
            "hpss_score": self.hpss_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricsPoint":
        return cls(
            time=float(data.get("time", 0.0)),
            vocal_energy=float(data.get("vocal_energy", 0.0)),
            chroma_variance=float(data.get("chroma_variance", 0.0)),
            hpss_score=float(data.get("hpss_score", 0.0)),
        )
```

### 1.2 Функции работы с файлом метрик

```python
def save_detailed_metrics(
    metrics: list[MetricsPoint],
    output_path: Path,
) -> None:
    """Сохранить детальные метрики в JSON-файл.
    
    Формат: плоский массив объектов с полями time, vocal_energy, chroma_variance, hpss_score.
    """
    data = [m.to_dict() for m in metrics]
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def load_detailed_metrics(input_path: Path) -> list[MetricsPoint]:
    """Загрузить детальные метрики из JSON-файла."""
    if not input_path.exists():
        return []
    data = json.loads(input_path.read_text(encoding="utf-8"))
    return [MetricsPoint.from_dict(m) for m in data]
```

---

## 2. Оптимизированное вычисление метрик (один проход)

### 2.1 Рефакторинг: разделение извлечения признаков и агрегации

Существующие методы `_compute_vocal_energy_per_segment()` и `_enrich_segments_with_librosa()` загружают аудио и вычисляют признаки заново. Для оптимизации вводим **двухэтапный подход**:

1. **Извлечение признаков (1 раз)** — frame-level данные
2. **Агрегация (2 раза)** — по сегментам и по секундам

### 2.2 Новый метод `_extract_frame_features()`

```python
@dataclass
class FrameFeatures:
    """Frame-level признаки аудио для двухэтапной агрегации.
    
    Содержит сырые frame-level данные, из которых можно вычислить
    все агрегированные метрики: по сегментам и по секундам.
    
    Attributes
    ----------
    times:
        Временные метки кадров в секундах (массив float, shape: [n_frames]).
        Для hop_length=512 и sr=22050: frames_per_sec ≈ 43.07
    
    vocal_energy:
        RMS энергия вокала для каждого кадра [0, 1], shape: [n_frames].
        Вычисляется из vocal_file или audio_file.
    
    chroma:
        Chroma features, shape: [12, n_frames].
        Нужно для: chroma_variance (var по 12 бинам) и sim_score (recurrence_matrix).
    
    rms_harmonic:
        RMS гармонической части после HPSS, shape: [n_samples] или [n_frames].
        Нужно для: hpss_score.
    
    tempo:
        Tempogram (rhythmic stability), shape: [n_tempo_bins, n_frames].
        Нужно для: tempo_score (CV по доминантному темпу).
    
    sr:
        Sample rate (обычно 22050).
    
    hop_length:
        Hop length для фреймов (обычно 512).
    """
    times: np.ndarray
    vocal_energy: np.ndarray
    chroma: np.ndarray
    rms_harmonic: np.ndarray  
    tempo: np.ndarray
    sr: int = 22050
    hop_length: int = 512
    
    @property
    def frames_per_sec(self) -> float:
        return self.sr / self.hop_length
    
    @property
    def duration(self) -> float:
        return len(self.times) / self.frames_per_sec


def _extract_frame_features(
    self,
    audio_file: str,
    vocal_file: str | None,
) -> FrameFeatures | None:
    """Извлечь frame-level признаки из аудио (один проход).
    
    Загружает аудио один раз, вычисляет все признаки на уровне кадров.
    Возвращает данные для последующей агрегации.
    
    Parameters
    ----------
    audio_file:
        Путь к основному аудиофайлу.
    vocal_file:
        Путь к файлу вокала (опционально).
        
    Returns
    -------
    FrameFeatures | None
        Frame-level признаки или None при ошибке.
    """
```

**Алгоритм:**
1. Загрузить аудио через `librosa.load()` один раз
2. Вычислить frame-level признаки:
   - `vocal_energy` — RMS из vocal_file или audio_file
   - `chroma` — `librosa.feature.chroma_cqt()`
   - `chroma_variance` — rolling variance по chroma
   - `hpss_score` — RMS гармонической части после `librosa.effects.hpss()`
   - `sim_matrix` — для segment-level агрегации (опционально)
3. Вернуть `FrameFeatures` с массивами одинаковой длины

### 2.3 Метод `_aggregate_segment_features()`

Агрегирует frame-level признаки по границам сегментов для `SegmentInfo.scores`.

```python
def _aggregate_segment_features(
    self,
    frame_features: FrameFeatures,
    segments: list[tuple[float, float]],
) -> list[dict]:
    """Агрегировать frame-level признаки по сегментам.
    
    Вычисляет: sim_score, hpss_score, tempo_score, vocal_energy, chroma_variance
    
    Parameters
    ----------
    frame_features:
        Frame-level признаки из `_extract_frame_features()`.
    segments:
        Список кортежей (start, end) для каждого сегмента.
        
    Returns
    -------
    list[dict]
        Список словарей с агрегированными признаками.
    """
    raw_features = []
    
    for start, end in segments:
        f_start = int(start * frame_features.frames_per_sec)
        f_end = int(end * frame_features.frames_per_sec)
        
        # vocal_energy: mean по кадрам сегмента
        vocal_energy = float(np.mean(
            frame_features.vocal_energy[f_start:f_end]
        ))
        
        # chroma_variance: mean(var(chroma, axis=0)) по кадрам сегмента
        seg_chroma = frame_features.chroma[:, f_start:f_end]
        chroma_variance = float(np.mean(np.var(seg_chroma, axis=1)))
        
        # hpss_score: mean(rms_harmonic) по времени сегмента
        # Нужно интерполировать rms_harmonic из sample-based в frame-based
        rms_interp = np.interp(
            np.arange(len(frame_features.times)),
            np.linspace(0, len(frame_features.times), len(frame_features.rms_harmonic)),
            frame_features.rms_harmonic
        )
        hpss_score = float(np.mean(rms_interp[f_start:f_end]))
        
        # tempo_score: CV по доминантному темпу
        seg_tempo = frame_features.tempo[:, f_start:f_end]
        dominant_idx = np.argmax(np.mean(seg_tempo, axis=1))
        tempo_series = seg_tempo[dominant_idx, :]
        std = float(np.std(tempo_series))
        mean = float(np.mean(tempo_series))
        cv = std / (mean + 1e-8)
        tempo_score = max(0.0, 1.0 - cv)
        
        # sim_score: извлекаем из chroma сегмента
        # Нормализуем chroma и вычисляем self-similarity
        chroma_norm = librosa.util.normalize(seg_chroma, axis=0)
        sim_matrix = librosa.segment.recurrence_matrix(
            chroma_norm, mode="affinity", metric="cosine", sparse=False
        )
        sim_score = float(np.mean(sim_matrix))
        
        raw_features.append({
            "vocal_energy": vocal_energy,
            "chroma_variance": chroma_variance,
            "hpss_score": hpss_score,
            "tempo_score": tempo_score,
            "sim_score": sim_score,
        })
    
    # Нормализация tempo_score и chroma_variance по всем сегментам
    # (как в текущем _enrich_segments_with_librosa())
    tempo_scores = [f["tempo_score"] for f in raw_features]
    max_tempo = max(tempo_scores) if tempo_scores else 0.0
    if max_tempo > 0:
        for f in raw_features:
            f["tempo_score"] = f["tempo_score"] / max_tempo
    
    chroma_vars = [f["chroma_variance"] for f in raw_features]
    max_chroma_var = max(chroma_vars) if chroma_vars else 0.0
    if max_chroma_var > 0:
        for f in raw_features:
            f["chroma_variance"] = f["chroma_variance"] / max_chroma_var
    
    return raw_features
```

### 2.4 Метод `_aggregate_detailed_metrics()`

Агрегирует frame-level признаки по 1-секундным окнам для детального файла метрик.

```python
def _aggregate_detailed_metrics(
    self,
    frame_features: FrameFeatures,
    duration: float,
    aggregate_sec: float = 1.0,
) -> list[MetricsPoint]:
    """Агрегировать frame-level признаки по временным окнам.
    
    Для детальных метрик сохраняем: vocal_energy, chroma_variance, hpss_score
    (без sim_score и tempo_score, т.к. они требуют контекста всего сегмента).
    
    Parameters
    ----------
    frame_features:
        Frame-level признаки из `_extract_frame_features()`.
    duration:
        Общая длительность трека в секундах.
    aggregate_sec:
        Шаг агрегации в секундах (по умолчанию 1.0).
        
    Returns
    -------
    list[MetricsPoint]
        Список точек метрик с шагом aggregate_sec.
    """
    metrics = []
    num_points = int(duration / aggregate_sec) + 1
    
    for i in range(num_points):
        t_start = i * aggregate_sec
        t_end = min(t_start + aggregate_sec, duration)
        
        f_start = int(t_start * frame_features.frames_per_sec)
        f_end = int(t_end * frame_features.frames_per_sec)
        
        # vocal_energy: mean по кадрам окна
        vocal_energy = float(np.mean(
            frame_features.vocal_energy[f_start:f_end]
        )) if f_end > f_start else 0.0
        
        # chroma_variance: mean(var по 12 бинам)
        seg_chroma = frame_features.chroma[:, f_start:f_end]
        chroma_variance = float(np.mean(np.var(seg_chroma, axis=0))) \
            if f_end > f_start and seg_chroma.shape[1] > 0 else 0.0
        
        # hpss_score: mean(rms_harmonic)
        # Интерполируем rms_harmonic к frame-level
        rms_interp = np.interp(
            np.arange(len(frame_features.times)),
            np.linspace(0, len(frame_features.times), len(frame_features.rms_harmonic)),
            frame_features.rms_harmonic
        )
        hpss_score = float(np.mean(rms_interp[f_start:f_end])) \
            if f_end > f_start else 0.0
        
        metrics.append(MetricsPoint(
            time=t_start,
            vocal_energy=vocal_energy,
            chroma_variance=chroma_variance,
            hpss_score=hpss_score,
        ))
    
    return metrics
```

### 2.5 Обновление метода `detect()`

```python
def detect(self, audio_file: str, vocal_file: str | None = None) -> list[SegmentInfo]:
    # ... существующий код получения границ и сегментов ...
    
    # === ОДИН проход извлечения признаков ===
    frame_features = self._extract_frame_features(audio_file, vocal_file)
    
    if frame_features is None:
        # Fallback: возвращаем сегменты без признаков
        return result
    
    # === Агрегация по сегментам (для SegmentInfo) ===
    segment_features = self._aggregate_segment_features(frame_features, segments)
    
    # === Агрегация по секундам (для VolumeSegment.metrics) ===
    detailed_metrics = self._aggregate_detailed_metrics(
        frame_features, audio_duration
    )
    
    # ... классификация сегментов с использованием segment_features ...
    
    # Сохраняем detailed_metrics для последующего использования
    self._last_detailed_metrics = detailed_metrics
    
    return result
```



---

---

## 5. Модификация `TrackVisualizer`

### 5.1 Новые цвета для детальных линий

```python
# Цвета метрик (существующие - для сегментов)
_METRIC_COLORS: dict[str, str] = {
    "vocal_energy": "#FF6B6B",
    "sim_score": "#4ECDC4",
    "hpss_score": "#45B7D1",
}

# Цвета для детальных метрик (новые - более светлые/прозрачные)
_DETAILED_METRIC_COLORS: dict[str, str] = {
    "vocal_energy": "#FFB3B3",  # светло-красный
    "chroma_variance": "#B3E6E6",  # светло-бирюзовый
    "hpss_score": "#A3D8F0",  # светло-голубой
}
```

### 5.2 Обновление `_draw_metrics_layer()`

Параметры:
```python
def _draw_metrics_layer(
    self,
    ax: Any,
    segments: list[dict],
    duration: float,
    y_bottom: float,
    height: float,
    detailed_metrics: list[dict] | None = None,  # <-- новый параметр
) -> None:
```

Алгоритм отрисовки:

1. **Отрисовка детальных линий (новое)**:
   - Если передан `detailed_metrics`, строим линии для `vocal_energy`, `chroma_variance`, `hpss_score`
   - Линии более тонкие (linewidth=0.8), полупрозрачные (alpha=0.5)
   - Стиль: сплошная линия

2. **Отрисовка сегментных линий (существующее)**:
   - Сохраняем текущую логику отрисовки на основе `scores`
   - Линии более толстые (linewidth=1.5), непрозрачные (alpha=0.85)
   - Стиль: ступенчатый график

3. **Легенда**:
   - Добавить обозначения детальных линий в легенду
   - Формат: "vocal_energy (detailed)"

### 5.3 Передача файла метрик в `generate()`

Обновить сигнатуру:
```python
def generate(
    self,
    output_path: Path,
    transcribe_json_file: Path | None = None,
    corrected_transcribe_json_file: Path | None = None,
    aligned_lyrics_file: Path | None = None,
    source_lyrics_file: Path | None = None,
    volume_segments_file: Path | None = None,
    detailed_metrics_file: Path | None = None,  # <-- новый параметр
    track_title: str = "",
) -> None:
```

### 5.4 Загрузка детальных метрик

Использовать существующую функцию `load_detailed_metrics()` из `chorus_detector.py`:

```python
def _load_detailed_metrics_for_viz(self, path: Path | None) -> list[dict]:
    """Загрузить детальные метрики для визуализации."""
    if not path or not path.exists():
        return []
    
    # Используем функцию из chorus_detector
    from app.chorus_detector import load_detailed_metrics
    metrics = load_detailed_metrics(path)
    
    # Конвертируем в dict для визуализатора
    return [m.to_dict() for m in metrics]
```

---

## 6. Интеграция в пайплайн

### 6.1 Обновление PipelineState (`app/models.py`)

Добавить поле:
```python
detailed_metrics_file: str | None = None  # Путь к {track_stem}_metrics.json
```

### 6.2 Шаг DETECT_CHORUS

В `KaraokePipeline._step_detect_chorus()`:
```python
# ChorusDetector.detect() возвращает tuple
segment_infos, detailed_metrics = chorus_detector.detect(track_source, vocal_file)

# Создаём volume_segments (без вложенных метрик)
volume_segments = build_volume_segments(
    chorus_segments=[(s.start, s.end) for s in segment_infos if s.segment_type == "chorus"],
    audio_duration=audio_duration,
    chorus_volume=settings.CHORUS_BACKVOCAL_VOLUME,
    default_volume=settings.AUDIO_MIX_VOICE_VOLUME,
    segment_infos=segment_infos,
)

# Сохраняем volume_segments (как раньше)
save_volume_segments(volume_segments, volume_segments_file)

# Сохраняем детальные метрики в отдельный файл
detailed_metrics_file = track_dir / f"{track_stem}_metrics.json"
save_detailed_metrics(detailed_metrics, detailed_metrics_file)

# Обновляем PipelineState
state.detailed_metrics_file = str(detailed_metrics_file)
```

### 6.3 Шаг GENERATE_ASS (визуализация)

Вызов визуализатора:
```python
visualizer.generate(
    output_path=visualization_file,
    volume_segments_file=volume_segments_file,
    detailed_metrics_file=Path(state.detailed_metrics_file) if state.detailed_metrics_file else None,
    # ... остальные параметры
)
```

### 6.3 Шаг GENERATE_ASS / визуализация

Вызов визуализатора:
```python
visualizer.generate(
    ...
    volume_segments_file=volume_segments_file,
    segment_groups_file=segment_groups_file,  # <-- передаём для детальных метрик
    ...
)
```

---

## 7. Архитектура потока данных (один проход)

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ChorusDetector.detect()                          │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Шаг 1: _extract_frame_features()                             │   │
│  │                                                               │   │
│  │  • librosa.load(audio_file) — 1 раз                           │   │
│  │  • vocal_energy (frame-level)                                 │   │
│  │  • chroma_variance (frame-level)                              │   │
│  │  • hpss_score (frame-level)                                   │   │
│  │  • sim_matrix (frame-level, для сегментов)                    │   │
│  │                                                               │   │
│  │  Возвращает: FrameFeatures (times, arrays[])                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                        │
│              ┌───────────────┴───────────────┐                       │
│              ▼                               ▼                       │
│  ┌──────────────────────────┐  ┌──────────────────────────┐         │
│  │ Агрегация по сегментам   │  │ Агрегация по секундам    │         │
│  │                          │  │                          │         │
│  │ _aggregate_segment_      │  │ _aggregate_detailed_     │         │
│  │ features()               │  │ metrics()                │         │
│  │                          │  │                          │         │
│  │ Для SegmentInfo.scores   │  │ Для detailed_metrics[]   │         │
│  │                          │  │                          │         │
│  │ • mean по каждому        │  │ • mean по окнам [t, t+1) │         │
│  │   сегменту               │  │ • T = 1 секунда          │         │
│  └──────────────────────────┘  └──────────────────────────┘         │
│              │                               │                       │
│              ▼                               ▼                       │
│  segment_infos[i].scores          detailed_metrics[]                 │
│    ↓                                      ↓                          │
│    ↓                              Сохраняем в                        │
│    ↓                              {stem}_metrics.json                │
│    ↓                                                                 │
│    ↓                              НЕ сохраняем внутри                │
│    ↓                              VolumeSegment!                     │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     TrackVisualizer                                  │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Слой метрик (_draw_metrics_layer)                             │   │
│  │                                                               │   │
│  │  Входные данные:                                              │   │
│  │  • volume_segments_file ──► scores[] ──► ступенчатый график  │   │
│  │  • detailed_metrics_file ─► metrics[] ─► плавная линия       │   │
│  │                                                               │   │
│  │  Отображаем:                                                  │   │
│  │  • vocal_energy (segments) — толстый, непрозрачный           │   │
│  │  • vocal_energy (detailed) — тонкий, полупрозрачный          │   │
│  │  • chroma_variance (segments + detailed)                     │   │
│  │  • hpss_score (segments + detailed)                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## 8. Конфигурация (опционально)

В `app/config.py` добавить параметр:
```python
METRICS_AGGREGATE_SEC: float = 1.0  # Шаг агрегации метрик в секундах
```

---

## 9. Тестирование

### 9.1 Unit-тесты

1. **MetricsPoint**:
   - Сериализация/десериализация
   - Корректность полей

2. **VolumeSegment с metrics**:
   - Сохранение и загрузка
   - Обратная совместимость (файл без metrics)

3. **`_extract_frame_features()`**:
   - Проверка длины возвращаемых массивов
   - Корректность диапазона значений [0, 1]
   - Работа без vocal_file

4. **`_aggregate_detailed_metrics()`**:
   - Проверка длины результата (≈ duration секунд)
   - Корректность временных меток (0, 1, 2, ...)

5. **`_combine_group()` с metrics**:
   - Проверка объединения метрик
   - Отсутствие дубликатов на границах

### 9.2 Интеграционные тесты

1. **Полный пайплайн**:
   - DETECT_CHORUS → проверка наличия metrics в JSON
   - Проверка единственности вызова librosa.load()
   - Группировка → проверка сохранности metrics
   - Визуализация → проверка PNG с детальными линиями

2. **Производительность**:
   - Время выполнения detect() не должно значительно увеличиться
   - Проверка, что аудио загружается только 1 раз

---

## 10. Файлы для изменения

| Файл | Изменения |
|------|-----------|
| `app/chorus_detector.py` | +`MetricsPoint`, +`FrameFeatures`, +`save_detailed_metrics()`, +`load_detailed_metrics()`, +`_extract_frame_features()`, +`_aggregate_detailed_metrics()`, рефакторинг `_enrich_segments_with_librosa()` → `_aggregate_segment_features()`, обновление `detect()` для возврата tuple |
| `app/models.py` | +`detailed_metrics_file` в `PipelineState` |
| `app/track_visualizer.py` | +`_DETAILED_METRIC_COLORS`, обновление `_draw_metrics_layer()`, +`detailed_metrics_file` параметр |
| `app/pipeline.py` | Интеграция вычисления и сохранения метрик в DETECT_CHORUS, передача `detailed_metrics_file` в визуализатор |
| `app/config.py` | +`METRICS_AGGREGATE_SEC` (опционально) |

---

## 11. Обратная совместимость

- Новый файл `{track_stem}_metrics.json` создаётся только при новых запусках DETECT_CHORUS
- Старые треки без этого файла визуализируются без детальных линий (график сегментов сохраняется)
- API `detect()` изменяется на возврат `tuple[list[SegmentInfo], list[MetricsPoint]]`
- Группировка сегментов не затрагивает детальные метрики (отдельный файл)

---

## 12. Пример JSON с детальными метриками

**Файл: `{track_stem}_metrics.json`**
```json
[
  {"time": 0.0, "vocal_energy": 0.02, "chroma_variance": 0.10, "hpss_score": 0.55},
  {"time": 1.0, "vocal_energy": 0.03, "chroma_variance": 0.11, "hpss_score": 0.54},
  {"time": 2.0, "vocal_energy": 0.05, "chroma_variance": 0.12, "hpss_score": 0.56},
  {"time": 3.0, "vocal_energy": 0.08, "chroma_variance": 0.15, "hpss_score": 0.58},
  ...
]
```

**Преимущества отдельного файла:**
- Простая структура (плоский массив)
- Нет необходимости разбивать/склеивать при группировке сегментов
- Можно легко использовать для других целей (анализ, экспорт)
- Минимальные изменения в существующей структуре `VolumeSegment`

---

## 13. Визуальное представление

```
Слой метрик (TrackVisualizer):

vocal_energy (detailed)     ~~~~~~~~~~~~~~~~~~~~~  (тонкая, полупрозрачная)
vocal_energy (segments)     |=====|    |=====|    (толстая, ступенчатая)

chroma_variance (detailed)  ~~~~~~~~~~~~~~~~~~~~~  (тонкая, полупрозрачная)
chroma_variance (segments)  |=====|    |=====|    (толстая, ступенчатая)

hpss_score (detailed)       ~~~~~~~~~~~~~~~~~~~~~  (тонкая, полупрозрачная)
hpss_score (segments)       |=====|    |=====|    (толстая, ступенчатая)

Где:
- detailed: плавная линия по точкам каждой секунды
- segments: ступенчатый график, усреднённый по сегментам
```
