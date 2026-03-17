# План итерации 43: Рефакторинг VolumeSegment и универсальный метод объединения сегментов

## Цель
Провести рефакторинг класса [`VolumeSegment`](app/chorus_detector.py:66) для унификации структуры данных и создать универсальный метод объединения соседних сегментов, заменяющий текущие `_merge_short_segments` и `group_volume_segments`.

## Мотивация

### Текущие проблемы:
1. **Непоследовательность формата `scores`**:
   - В исходных сегментах: `scores: dict[str, float]` — плоская структура метрик
   - В группах (после `group_volume_segments`): `scores: dict` с полями `"id_group"` и `"scores": list[dict]`
   
2. **Дублирование логики объединения**:
   - [`_merge_short_segments()`](app/chorus_detector.py:165) — объединяет сегменты < min_duration
   - [`group_volume_segments()`](app/chorus_detector.py:319) — объединяет соседние сегменты одинакового типа
   - Оба метода делают схожие операции, но с разными критериями

3. **Сложность обработки в потребителях**:
   - [`TrackVisualizer`](app/track_visualizer.py:534) вынужден проверять `isinstance(scores, list)` для определения формата
   - Нет единого способа работы с метриками сегментов

## Новая архитектура

### 1. Структура SegmentScore

```python
@dataclass
class SegmentScore:
    """Метрики отдельного суб-сегмента.
    
    Каждый суб-сегмент соответствует одной границе от msaf,
    объединённые сегменты хранят массив таких scores.
    """
    id: int                          # Порядковый номер суб-сегмента
    vocal_energy: float = 0.0
    chroma_variance: float = 0.0
    sim_score: float = 0.0
    hpss_score: float = 0.0
    tempo_score: float = 0.0
```

### 2. Изменённая структура VolumeSegment

```python
@dataclass
class VolumeSegment:
    """Временной сегмент с заданной громкостью вокала.
    
    scores ВСЕГДА список (list[SegmentScore]), даже для 
    несгруппированных сегментов — тогда список содержит 1 элемент.
    """
    start: float
    end: float
    volume: float
    segment_type: str | None = None
    backend: str | None = None
    scores: list[SegmentScore] = field(default_factory=list)  # ВСЕГДА list
    id: int = 0  # id первого суб-сегмента (scores[0].id)
```

### 3. Универсальный метод merge_segments

```python
from collections.abc import Callable


def merge_segments(
    segments: list[VolumeSegment],
    should_merge: Callable[[VolumeSegment, VolumeSegment], bool],
) -> list[VolumeSegment]:
    """Универсальный метод объединения соседних сегментов.
    
    Проходим по сегментам слева направо, группируя соседние,
    для которых should_merge(prev, current) возвращает True.
    
    Алгоритм объединения (единый для всех случаев):
    - start = start первого сегмента в группе
    - end = end последнего сегмента в группе
    - segment_type = тип первого сегмента (должны совпадать у всех в группе)
    - backend = backend первого сегмента
    - scores = конкатенация всех scores в хронологическом порядке
    - id = id первого сегмента
    - volume = вычисляется по типу через _get_volume_for_segment_type
      с использованием self.chorus_volume и self.default_volume из ChorusDetector
    
    Parameters
    ----------
    segments:
        Исходный список сегментов (будет отсортирован по start).
    should_merge:
        Функция (prev, current) -> bool, определяющая объединение.
        Для группировки по типу: проверка segment_type.
        Для объединения коротких: проверка длительности current.
        
    Returns
    -------
    list[VolumeSegment]
        Список объединённых сегментов.
    """
```

### 4. Предикаты для should_merge

```python
def should_merge_same_type(
    prev: VolumeSegment,
    current: VolumeSegment,
) -> bool:
    """Предикат для объединения сегментов одинакового типа."""
    prev_type = prev.segment_type or "unknown"
    curr_type = current.segment_type or "unknown"
    return prev_type == curr_type


class ChorusDetector:
    """Определяет временные отрезки сегментов в аудиофайле."""
    
    def __init__(
        self,
        min_duration_sec: float = 5.0,
        vocal_silence_threshold: float = 0.05,
        boundary_merge_tolerance_sec: float = 2.0,
        chorus_volume: float = 0.4,
        default_volume: float = 0.2,
    ) -> None:
        self._min_duration = min_duration_sec
        self._vocal_silence_threshold = vocal_silence_threshold
        self._boundary_merge_tolerance = boundary_merge_tolerance_sec
        self._chorus_volume = chorus_volume
        self._default_volume = default_volume
    
    def should_merge_short(
        self,
        prev: VolumeSegment,
        current: VolumeSegment,
    ) -> bool:
        """Предикат для объединения коротких сегментов.
        
        Объединяем, если ТЕКУЩИЙ сегмент короче min_duration.
        Использует self._min_duration из настроек ChorusDetector.
        """
        return (current.end - current.start) < self._min_duration
```

**Важно**: Разница между "объединением по типу" и "объединением коротких" находится
ТОЛЬКО в предикате `should_merge`. Сам процесс объединения идентичен — конкатенация
scores, расширение границ, вычисление volume по типу через `_get_volume_for_segment_type`
с использованием настроек `self._chorus_volume` и `self._default_volume`.

## Файлы для изменения

### 5.1. Основные изменения

| Файл | Изменения |
|------|-----------|
| [`app/chorus_detector.py`](app/chorus_detector.py) | Новый класс `SegmentScore`, изменить `VolumeSegment.scores: list[SegmentScore]`, новый `merge_segments()`, обновить `build_volume_segments()`, `save/load_volume_segments()`, удалить устаревшие функции |
| [`app/pipeline.py`](app/pipeline.py) | Импорт `SegmentScore`, обновить вызовы `group_volume_segments` → `merge_segments` с соответствующим предикатом |
| [`app/track_visualizer.py`](app/track_visualizer.py) | Упростить `_load_volume_segments()` и `_draw_segments_layer()` — scores всегда list, убрать проверки формата |
| [`app/vocal_processor.py`](app/vocal_processor.py) | Обновить тип импорта `VolumeSegment`, проверить `_build_volume_filter()` |
| [`app/ass_generator.py`](app/ass_generator.py) | Обновить доступ к scores как к list при чтении volume_segments_file |

### 5.2. Функции для удаления

| Старая функция | Причина удаления |
|---------------|------------------|
| `_merge_short_segments()` | Заменена на `merge_segments()` с предикатом `should_merge_short` |
| `group_volume_segments()` | Заменена на `merge_segments()` с предикатом `should_merge_same_type` |
| `_create_group_from_segments()` | Логика встроена в `merge_segments()` |
| `save_segment_groups()` | Формат объединён с `save_volume_segments()` — единый формат для всех |

## Детальное описание изменений

### 6.1. app/chorus_detector.py

#### 6.1.1. Новый класс SegmentScore (вставить перед VolumeSegment)

```python
@dataclass
class SegmentScore:
    """Метрики отдельного суб-сегмента.
    
    Attributes
    ----------
    id:
        Порядковый номер суб-сегмента (из msaf).
    vocal_energy:
        Нормализованная энергия вокала [0, 1].
    chroma_variance:
        Вариативность chroma features [0, 1].
    sim_score:
        Self-similarity score [0, 1].
    hpss_score:
        Harmonic-percussive separation score [0, 1].
    tempo_score:
        Rhythmic stability score [0, 1].
    """
    id: int
    vocal_energy: float = 0.0
    chroma_variance: float = 0.0
    sim_score: float = 0.0
    hpss_score: float = 0.0
    tempo_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в dict для JSON."""
        return {
            "id": self.id,
            "vocal_energy": self.vocal_energy,
            "chroma_variance": self.chroma_variance,
            "sim_score": self.sim_score,
            "hpss_score": self.hpss_score,
            "tempo_score": self.tempo_score,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SegmentScore":
        """Десериализовать из dict."""
        return cls(
            id=int(data.get("id", 0)),
            vocal_energy=float(data.get("vocal_energy", 0.0)),
            chroma_variance=float(data.get("chroma_variance", 0.0)),
            sim_score=float(data.get("sim_score", 0.0)),
            hpss_score=float(data.get("hpss_score", 0.0)),
            tempo_score=float(data.get("tempo_score", 0.0)),
        )
```

#### 6.1.2. Изменить VolumeSegment

```python
@dataclass
class VolumeSegment:
    """Временной сегмент с заданной громкостью вокала.
    
    scores ВСЕГДА список SegmentScore, даже для одиночного сегмента.
    
    Attributes
    ----------
    start, end, volume, segment_type, backend:
        Как раньше.
    scores:
        Список метрик суб-сегментов (всегда list, никогда dict).
        Для несгруппированного сегмента — список из 1 элемента.
    id:
        id первого суб-сегмента (= scores[0].id если scores не пуст).
    """
    start: float
    end: float
    volume: float
    segment_type: str | None = None
    backend: str | None = None
    scores: list[SegmentScore] = field(default_factory=list)
    id: int = 0

    @property
    def duration(self) -> float:
        """Длительность сегмента в секундах."""
        return self.end - self.start

    @property
    def subsegment_count(self) -> int:
        """Количество суб-сегментов в группе."""
        return len(self.scores)

    def get_first_id(self) -> int:
        """Получить id первого суб-сегмента."""
        return self.scores[0].id if self.scores else self.id

    def get_last_id(self) -> int:
        """Получить id последнего суб-сегмента."""
        return self.scores[-1].id if self.scores else self.id

    def get_id_range(self) -> str:
        """Вернуть строку диапазона id, например '#1-3'."""
        if not self.scores:
            return f"#{self.id}"
        first = self.get_first_id()
        last = self.get_last_id()
        if first == last:
            return f"#{first}"
        return f"#{first}-{last}"

    def to_dict(self) -> dict[str, Any]:
        """Сериализовать в dict для JSON."""
        result: dict[str, Any] = {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "volume": self.volume,
        }
        if self.segment_type is not None:
            result["segment_type"] = self.segment_type
        if self.backend is not None:
            result["backend"] = self.backend
        if self.scores:
            result["scores"] = [s.to_dict() for s in self.scores]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VolumeSegment":
        """Десериализовать из dict.
        
        Поддерживает ТОЛЬКО новый формат (scores как list[dict]).
        """
        scores_data = data.get("scores", [])
        scores: list[SegmentScore] = []
        
        if isinstance(scores_data, list):
            scores = [SegmentScore.from_dict(s) for s in scores_data]
        # Если scores не list — ошибка, старый формат не поддерживается
        
        return cls(
            start=float(data["start"]),
            end=float(data["end"]),
            volume=float(data["volume"]),
            segment_type=data.get("segment_type"),
            backend=data.get("backend"),
            scores=scores,
            id=int(data.get("id", 0)),
        )
```

#### 6.1.3. Обновить ChorusDetector.__init__

```python
class ChorusDetector:
    """Определяет временные отрезки сегментов в аудиофайле."""

    def __init__(
        self,
        min_duration_sec: float = 5.0,
        vocal_silence_threshold: float = 0.05,
        boundary_merge_tolerance_sec: float = 2.0,
        chorus_volume: float = 0.4,
        default_volume: float = 0.2,
    ) -> None:
        self._min_duration = min_duration_sec
        self._vocal_silence_threshold = vocal_silence_threshold
        self._boundary_merge_tolerance = boundary_merge_tolerance_sec
        self._chorus_volume = chorus_volume
        self._default_volume = default_volume
```

#### 6.1.4. Новая функция merge_segments (метод ChorusDetector)

```python
from collections.abc import Callable


class ChorusDetector:
    # ... __init__ ...
    
    def merge_segments(
        self,
        segments: list[VolumeSegment],
        should_merge: Callable[[VolumeSegment, VolumeSegment], bool],
    ) -> list[VolumeSegment]:
        """Универсальный метод объединения соседних сегментов.
        
        Проходим по сегментам слева направо, группируя соседние,
        для которых should_merge(prev, current) возвращает True.
        
        Алгоритм объединения (единый для всех случаев):
        - start = start первого сегмента в группе
        - end = end последнего сегмента в группе
        - segment_type = тип первого сегмента
        - backend = backend первого сегмента
        - scores = конкатенация всех scores в хронологическом порядке
        - id = id первого сегмента
        - volume = вычисляется по типу через _get_volume_for_segment_type
          с использованием self._chorus_volume и self._default_volume
        
        Parameters
        ----------
        segments:
            Исходный список сегментов (будет отсортирован по start).
        should_merge:
            Функция (prev, current) -> bool, определяющая объединение.
            
        Returns
        -------
        list[VolumeSegment]
            Список объединённых сегментов.
        """
        if not segments:
            return []
        
        # Сортируем по start
        sorted_segs = sorted(segments, key=lambda s: s.start)
        
        groups: list[list[VolumeSegment]] = []
        current_group: list[VolumeSegment] = [sorted_segs[0]]
        
        for seg in sorted_segs[1:]:
            prev = current_group[-1]
            if should_merge(prev, seg):
                current_group.append(seg)
            else:
                groups.append(current_group)
                current_group = [seg]
        
        if current_group:
            groups.append(current_group)
        
        # Создаём VolumeSegment для каждой группы
        result: list[VolumeSegment] = []
        for group in groups:
            merged = self._combine_group(group)
            result.append(merged)
        
        return result

    def _combine_group(
        self,
        segs: list[VolumeSegment],
    ) -> VolumeSegment:
        """Объединить группу сегментов в один."""
        if not segs:
            raise ValueError("Cannot combine empty group")
        if len(segs) == 1:
            return segs[0]
        
        first = segs[0]
        last = segs[-1]
        segment_type = first.segment_type or "unknown"
        
        # Вычисляем volume по типу через _get_volume_for_segment_type
        volume = _get_volume_for_segment_type(
            segment_type, self._chorus_volume, self._default_volume
        )
        
        # Конкатенируем все scores
        all_scores: list[SegmentScore] = []
        for seg in segs:
            all_scores.extend(seg.scores)
        
        return VolumeSegment(
            start=first.start,
            end=last.end,
            volume=volume,
            segment_type=segment_type,
            backend=first.backend,
            scores=all_scores,
            id=first.id,
        )

    def should_merge_short(
        self,
        prev: VolumeSegment,
        current: VolumeSegment,
    ) -> bool:
        """Предикат для объединения коротких сегментов.
        
        Объединяем, если ТЕКУЩИЙ сегмент короче min_duration.
        Использует self._min_duration из настроек ChorusDetector.
        """
        return (current.end - current.start) < self._min_duration
```

#### 6.1.5. Предикат should_merge_same_type (module-level)

```python
def should_merge_same_type(
    prev: VolumeSegment,
    current: VolumeSegment,
) -> bool:
    """Предикат для объединения сегментов одинакового типа."""
    prev_type = prev.segment_type or "unknown"
    curr_type = current.segment_type or "unknown"
    return prev_type == curr_type
```

#### 6.1.6. Обновить build_volume_segments

```python
def build_volume_segments(
    segment_infos: list[SegmentInfo],
    audio_duration: float,
    chorus_volume: float,
    default_volume: float,
) -> list[VolumeSegment]:
    """Построить список сегментов громкости на основе segment_infos.
    
    Теперь всегда создаёт VolumeSegment с scores как list[SegmentScore].
    Для пустых/заполняемых сегментов использует _get_volume_for_segment_type(None).
    """
    result: list[VolumeSegment] = []
    sorted_infos = sorted(segment_infos, key=lambda s: s.start)
    current_pos = 0.0
    
    for info in sorted_infos:
        if info.start > current_pos + 0.01:
            # Заполняемый пробел — создаём сегмент с пустым scores
            # volume для пустого сегмента = default_volume (через None)
            result.append(VolumeSegment(
                start=current_pos,
                end=info.start,
                volume=_get_volume_for_segment_type(None, chorus_volume, default_volume),
                scores=[],  # Пустой массив — нет данных от детектора
            ))
        
        volume = _get_volume_for_segment_type(
            info.segment_type, chorus_volume, default_volume
        )
        
        # Создаём SegmentScore из info.scores
        scores_list = [SegmentScore(
            id=0,  # Будет присвоен позже
            vocal_energy=float(info.scores.get("vocal_energy", 1.0)),
            chroma_variance=float(info.scores.get("chroma_variance", 0.0)),
            sim_score=float(info.scores.get("sim_score", 0.0)),
            hpss_score=float(info.scores.get("hpss_score", 0.0)),
            tempo_score=float(info.scores.get("tempo_score", 0.0)),
        )]
        
        result.append(VolumeSegment(
            start=info.start,
            end=info.end,
            volume=volume,
            segment_type=info.segment_type,
            backend=info.backend,
            scores=scores_list,
        ))
        current_pos = info.end
    
    # Заполняем пробел после последнего
    if current_pos < audio_duration - 0.01:
        result.append(VolumeSegment(
            start=current_pos,
            end=audio_duration,
            volume=_get_volume_for_segment_type(None, chorus_volume, default_volume),
            scores=[],
        ))
    
    # Присваиваем id по порядку
    for idx, seg in enumerate(result, start=1):
        seg.id = idx
        # Обновляем id в scores, если есть
        if seg.scores:
            seg.scores[0].id = idx
    
    return result
```

**Примечание**: Старый fallback с `chorus_segments: list[tuple[float, float]]` удалён,
так как теперь всегда используется `segment_infos: list[SegmentInfo]`.

#### 6.1.7. Обновить save_volume_segments / load_volume_segments

```python
def save_volume_segments(
    segments: list[VolumeSegment],
    output_path: Path,
) -> None:
    """Сохранить разметку громкости в JSON.
    
    Использует новый формат с scores как list[SegmentScore].
    """
    _logger = logging.getLogger(__name__)
    data = [seg.to_dict() for seg in segments]
    
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _logger.debug(
        "save_volume_segments: saved %d segments to '%s'",
        len(segments),
        output_path,
    )


def load_volume_segments(input_path: Path) -> list[VolumeSegment]:
    """Загрузить разметку громкости из JSON.
    
    Поддерживает ТОЛЬКО новый формат: scores как list[dict].
    """
    _logger = logging.getLogger(__name__)
    if not input_path.exists():
        raise FileNotFoundError(f"Файл разметки громкости не найден: {input_path}")
    
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Некорректный JSON в файле '{input_path}': {exc}") from exc
    
    segments = [VolumeSegment.from_dict(item) for item in data]
    
    _logger.debug(
        "load_volume_segments: loaded %d segments from '%s'",
        len(segments),
        input_path,
    )
    return segments
```

#### 6.1.8. Удалить устаревшие функции

Удалить полностью:
- `_merge_short_segments()` — заменена на `detector.merge_segments(detector.should_merge_short)`
- `group_volume_segments()` — заменена на `detector.merge_segments(should_merge_same_type)`
- `_create_group_from_segments()` — встроена в `_combine_group`
- `save_segment_groups()` — единый формат через `save_volume_segments`
- Старый fallback в `build_volume_segments` с параметром `chorus_segments`

### 6.2. app/pipeline.py

#### 6.2.1. Обновить импорты

```python
from .chorus_detector import (
    ChorusDetector,
    SegmentScore,
    VolumeSegment,
    build_volume_segments,
    load_volume_segments,
    save_volume_segments,
    should_merge_same_type,
)
```

#### 6.2.2. Обновить _step_detect_chorus

```python
async def _step_detect_chorus(self) -> None:
    """Detect chorus segments and build volume_segments_file."""
    # ... код проверок и detect ...
    
    detector = ChorusDetector(
        min_duration_sec=self._settings.chorus_min_duration_sec,
        vocal_silence_threshold=self._settings.chorus_vocal_silence_threshold,
        boundary_merge_tolerance_sec=self._settings.chorus_boundary_merge_tolerance_sec,
        chorus_volume=self._settings.chorus_backvocal_volume,
        default_volume=self._settings.audio_mix_voice_volume,
    )
    
    segment_infos = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: detector.detect(
            full_file_str,
            vocal_file=vocal_file_str,
        ),
    )
    
    # Step 2: Probe audio duration
    audio_duration = await self._probe_audio_duration(Path(full_file_str))
    if audio_duration is None:
        audio_duration = 0.0
    
    # Step 3: Build volume segments
    volume_segments = build_volume_segments(
        segment_infos=segment_infos,
        audio_duration=audio_duration,
        chorus_volume=self._settings.chorus_backvocal_volume,
        default_volume=self._settings.audio_mix_voice_volume,
    )
    
    # Step 4: Merge short segments (используем метод detector)
    volume_segments = detector.merge_segments(
        segments=volume_segments,
        should_merge=detector.should_merge_short,
    )
    
    # Save to JSON
    volume_segments_file = track_dir / f"{stem}_volume_segments.json"
    save_volume_segments(volume_segments, volume_segments_file)
    self._state.volume_segments_file = str(volume_segments_file)
    
    self._save_state()
```

#### 6.2.3. Обновить _step_generate_ass

```python
async def _step_generate_ass(self) -> None:
    # ... код до создания групп ...
    
    # Создаём группы сегментов из volume_segments
    segment_groups_path: Path | None = None
    if volume_segments_path and volume_segments_path.exists():
        try:
            volume_segments = load_volume_segments(volume_segments_path)
            if volume_segments:
                # Создаём detector для merge_segments с правильными настройками
                detector = ChorusDetector(
                    chorus_volume=self._settings.chorus_backvocal_volume,
                    default_volume=self._settings.audio_mix_voice_volume,
                )
                
                # Группируем сегменты по типу
                groups = detector.merge_segments(
                    segments=volume_segments,
                    should_merge=should_merge_same_type,
                )
                
                # Сохраняем группы в тот же формат
                segment_groups_path = track_dir / f"{stem}_segment_groups.json"
                save_volume_segments(groups, segment_groups_path)
                self._state.segment_groups_file = str(segment_groups_path)
                self._save_state()
                # ...
        except Exception as exc:
            # ...
```

### 6.3. app/track_visualizer.py

#### 6.3.1. Упростить _load_volume_segments

```python
def _load_volume_segments(self, path: Path) -> list[dict]:
    """Загрузить сегменты из volume_segments_file.
    
    Теперь scores ВСЕГДА list, формат унифицирован.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.warning(
                "TrackVisualizer._load_volume_segments: expected list, got %s",
                type(data).__name__,
            )
            return []
        return data
    except Exception as exc:
        logger.warning(
            "TrackVisualizer._load_volume_segments: failed to load '%s': %s",
            path,
            exc,
        )
        return []
```

#### 6.3.2. Упростить _draw_segments_layer

```python
def _draw_segments_layer(
    self,
    ax: Any,
    segments: list[dict],
    duration: float,
    y_bottom: float,
    height: float,
) -> None:
    """Нарисовать цветные прямоугольники сегментов.
    
    Теперь scores ВСЕГДА list[dict], проверка isinstance не нужна.
    """
    import matplotlib.patches as mpatches
    
    subseg_strip_height = height * 0.22
    group_rect_height = height - subseg_strip_height
    
    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        seg_type = seg.get("segment_type") or "unknown"
        volume = float(seg.get("volume", 0.0))
        
        # scores всегда list в новом формате
        scores = seg.get("scores") or []
        if not isinstance(scores, list):
            scores = []
        
        seg_id = seg.get("id", 0)
        
        # Вычисляем метрики как среднее по scores
        if scores:
            vocal_energy = float(np.mean([s.get("vocal_energy", 0.0) for s in scores]))
            sim_score = float(np.mean([s.get("sim_score", 0.0) for s in scores]))
            hpss_score = float(np.mean([s.get("hpss_score", 0.0) for s in scores]))
            # Диапазон id
            ids = [s.get("id", 0) for s in scores]
            id_range = f"#{min(ids)}-{max(ids)}" if len(ids) > 1 else f"#{ids[0]}"
        else:
            vocal_energy = sim_score = hpss_score = 0.0
            id_range = f"#{seg_id}"
        
        # ... остальная логика отрисовки без изменений ...
```

#### 6.3.3. Упростить _draw_metrics_layer

```python
def _draw_metrics_layer(
    self,
    ax: Any,
    segments: list[dict],
    duration: float,
    y_bottom: float,
    height: float,
) -> None:
    """Нарисовать ступенчатые графики метрик.
    
    Теперь scores ВСЕГДА list, рисуем метрики для каждого элемента.
    """
    if not segments:
        return
    
    metrics_data: dict[str, list[tuple[float, float, float]]] = {
        "vocal_energy": [],
        "sim_score": [],
        "hpss_score": [],
    }
    
    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        scores = seg.get("scores") or []
        
        if not isinstance(scores, list):
            scores = []
        
        if end <= start:
            continue
        
        if scores:
            # Разбиваем временной интервал на подинтервалы
            seg_duration = end - start
            sub_duration = seg_duration / len(scores)
            for i, sub_score in enumerate(scores):
                sub_start = start + i * sub_duration
                sub_end = sub_start + sub_duration
                metrics_data["vocal_energy"].append(
                    (sub_start, sub_end, float(sub_score.get("vocal_energy", 0.0)))
                )
                metrics_data["sim_score"].append(
                    (sub_start, sub_end, float(sub_score.get("sim_score", 0.0)))
                )
                metrics_data["hpss_score"].append(
                    (sub_start, sub_end, float(sub_score.get("hpss_score", 0.0)))
                )
        else:
            # Нет данных — нулевые метрики
            for metric_name in metrics_data:
                metrics_data[metric_name].append((start, end, 0.0))
    
    # ... остальная логика рисования без изменений ...
```

### 6.4. app/vocal_processor.py

Проверить, что импорт `VolumeSegment` работает корректно с новой структурой.

```python
from .chorus_detector import VolumeSegment  # Работает без изменений
```

Метод `_build_volume_filter` использует только `seg.start`, `seg.end`, `seg.volume` — эти поля не изменились.

### 6.5. app/ass_generator.py

Проверить, используется ли `volume_segments_file` и в каком формате ожидаются scores.
Если используются метрики из scores — обновить доступ к `scores` как к list.

## Тестирование

### 7.1. Unit-тесты для новых функций

```python
# tests/test_chorus_detector.py (или создать)

def test_segment_score_to_dict():
    score = SegmentScore(id=1, vocal_energy=0.5, sim_score=0.8)
    d = score.to_dict()
    assert d["id"] == 1
    assert d["vocal_energy"] == 0.5

def test_volume_segment_from_dict():
    # Новый формат только
    data = {
        "id": 5,
        "start": 10.0,
        "end": 20.0,
        "volume": 0.4,
        "scores": [
            {"id": 5, "vocal_energy": 0.6, "sim_score": 0.7}
        ],
    }
    seg = VolumeSegment.from_dict(data)
    assert len(seg.scores) == 1
    assert seg.scores[0].vocal_energy == 0.6

def test_merge_segments_combine():
    detector = ChorusDetector(chorus_volume=0.4, default_volume=0.2)
    segs = [
        VolumeSegment(0, 10, 0.4, segment_type="verse", id=1, scores=[SegmentScore(1)]),
        VolumeSegment(10, 20, 0.4, segment_type="verse", id=2, scores=[SegmentScore(2)]),
        VolumeSegment(20, 30, 0.2, segment_type="chorus", id=3, scores=[SegmentScore(3)]),
    ]
    result = detector.merge_segments(
        segs,
        should_merge_same_type,
    )
    assert len(result) == 2  # verse группа + chorus
    assert result[0].subsegment_count == 2
    assert result[1].subsegment_count == 1

def test_merge_segments_short():
    detector = ChorusDetector(min_duration_sec=3.0, chorus_volume=0.4, default_volume=0.2)
    segs = [
        VolumeSegment(0, 10, 0.4, id=1, scores=[SegmentScore(1)]),
        VolumeSegment(10, 12, 0.4, id=2, scores=[SegmentScore(2)]),  # Короткий
        VolumeSegment(12, 25, 0.4, id=3, scores=[SegmentScore(3)]),
    ]
    result = detector.merge_segments(
        segs,
        detector.should_merge_short,
    )
    assert len(result) == 2  # 1+(2)+3 объединены
    assert result[0].subsegment_count == 2  # id 1 + id 2
```

### 7.2. Интеграционное тестирование

1. Запустить pipeline на тестовом треке
2. Проверить, что `volume_segments_file` создаётся с новым форматом
3. Проверить, что `segment_groups_file` создаётся корректно
4. Проверить визуализацию через `TrackVisualizer`
5. Проверить обработку в `VocalProcessor`
6. Проверить генерацию ASS через `AssGenerator`

## Миграция данных

### Новый формат volume_segments.json (ЕДИНСТВЕННЫЙ ПОДДЕРЖИВАЕМЫЙ):

```json
[
  {
    "id": 1,
    "start": 0.0,
    "end": 10.5,
    "volume": 0.2,
    "segment_type": "intro",
    "scores": [
      {
        "id": 1,
        "vocal_energy": 0.1,
        "sim_score": 0.3,
        "hpss_score": 0.2,
        "chroma_variance": 0.0,
        "tempo_score": 0.0
      }
    ]
  }
]
```

### Формат segment_groups.json (теперь тот же формат):

```json
[
  {
    "id": 1,
    "start": 0.0,
    "end": 35.2,
    "volume": 0.2,
    "segment_type": "verse",
    "scores": [
      {"id": 1, "vocal_energy": 0.5, ...},
      {"id": 2, "vocal_energy": 0.6, ...},
      {"id": 3, "vocal_energy": 0.4, ...}
    ]
  }
]
```

## Этапы реализации

1. **Создание новых классов** — `SegmentScore`, обновлённый `VolumeSegment`
2. **Обновление ChorusDetector.__init__** — добавить `chorus_volume` и `default_volume`
3. **Реализация `merge_segments` как метода ChorusDetector** — единый алгоритм
4. **Реализация `should_merge_short` как метода ChorusDetector** — использует `self._min_duration`
5. **Обновление сериализации** — `to_dict` / `from_dict` (только новый формат)
6. **Обновление `build_volume_segments`** — удалить fallback, использовать только `segment_infos`
7. **Обновление pipeline** — использование новых функций с предикатами
8. **Обновление visualizer** — упрощение кода (scores всегда list)
9. **Удаление устаревших функций** — `_merge_short_segments`, `group_volume_segments`, `save_segment_groups`
10. **Проверка и исправление всех импортов и вызовов** в проекте
11. **Тестирование** — unit + интеграционное

## Ожидаемые результаты

- Единый формат данных для сегментов и групп сегментов
- Упрощённая логика в потребителях (визуализатор, генератор ASS)
- Универсальный метод объединения с гибкими критериями (только через should_merge)
- Единый алгоритм объединения для всех случаев
- Более чистая и поддерживаемая архитектура
- Отсутствие legacy-кода и обратной совместимости

## Проверка импортов и зависимостей

После внесения изменений необходимо проверить следующие файлы на корректность импортов:

```bash
# Проверить все импорты chorus_detector
grep -r "from.*chorus_detector.*import\|import.*chorus_detector" app/

# Проверить использование VolumeSegment
grep -r "VolumeSegment" app/ --include="*.py"

# Проверить использование удаляемых функций
grep -r "_merge_short_segments\|group_volume_segments\|save_segment_groups\|load_segment_groups" app/ --include="*.py"
```

Все найденные импорты и вызовы устаревших функций должны быть исправлены.
