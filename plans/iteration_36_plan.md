# Итерация 36 — Доработка ChorusDetector

## Цель

Переработать [`ChorusDetector`](app/chorus_detector.py:289) так, чтобы он:
1. Сначала собирал и агрегировал всю информацию о сегментах из двух файлов (`track_source` и `vocal_file`) через msaf.
2. Объединял сегменты по сходству границ, используя отсутствие сигнала в `vocal_file` как признак проигрыша/инструментала.
3. Обогащал каждый сегмент дополнительными признаками из librosa.
4. Классифицировал сегменты по расширенному набору типов: `chorus`, `verse`, `bridge`, `intro`, `outro`, `instrumental`.

---

## Архитектурный обзор

### Текущее состояние

- [`detect_with_info(audio_file)`](app/chorus_detector.py:317) принимает **один** файл.
- Три бэкенда: `msaf`, `librosa`, `hybrid` — работают независимо.
- Типы сегментов: только `"chorus"` и `"non-chorus"`.
- Шаг [`_step_detect_chorus()`](app/pipeline.py:850) передаёт только `track_source`.
- В [`app/config.py`](app/config.py) есть параметры `chorus_detector_backend`, `chorus_min_duration_sec`, `chorus_max_duration_sec`.
- В [`app/pipeline.py`](app/pipeline.py:880) `ChorusDetector` создаётся с параметром `backend`.

### Новый подход

```
track_source ──► msaf.process() ──► границы A
                                         │
vocal_file ───► msaf.process() ──► границы B
                                         │
                              объединение границ
                                         │
                              сегменты + vocal_energy
                                         │
                              обогащение librosa
                                         │
                              классификация типов
                                         │
                              list[SegmentInfo]
```

---

## Детальный план реализации

### Шаг 1. Переработать класс [`ChorusDetector`](app/chorus_detector.py:289) — удалить логику бэкендов

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

- Удалить параметр `backend` из `__init__()` — логика выбора бэкенда больше не нужна.
- Удалить методы: `_detect_msaf_with_info()`, `_detect_librosa_with_info()`, `_detect_hybrid_with_info()`, `_merge_segments_with_source()`.
- Удалить вспомогательные методы librosa-бэкенда: `_compute_chroma()`, `_compute_self_similarity()`, `_compute_boundaries()`, `_hpss_energy()`, `_compute_tempogram_stability()`.
- Удалить вспомогательные методы msaf-бэкенда: `_build_segments_from_boundaries()`, `_pick_chorus_segments()`, `_filter_by_duration()`.
- **Полностью удалить** метод `detect_with_info()`.
- Переименовать `_detect_dual_file()` в публичный метод `detect()`.

**Новая сигнатура `__init__`:**
```python
def __init__(
    self,
    min_duration_sec: float = 5.0,
    vocal_silence_threshold: float = 0.05,
    boundary_merge_tolerance_sec: float = 2.0,
) -> None:
```

> ⚠️ **Важно:** параметр `backend` удаляется из `__init__`. Это означает, что в [`app/pipeline.py`](app/pipeline.py:880) нужно убрать `backend=self._settings.chorus_detector_backend` при создании `ChorusDetector`. Параметры `chorus_detector_backend`, `chorus_min_duration_sec`, `chorus_max_duration_sec` в [`app/config.py`](app/config.py:92) — удалить или заменить новыми.

---

### Шаг 2. Реализовать новый публичный метод `detect()`

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

Единственный публичный метод класса — `detect()`:

```python
def detect(
    self,
    audio_file: str,
    vocal_file: str | None = None,
) -> list[SegmentInfo]:
    """Определить сегменты с расширенной информацией.
    
    Если vocal_file передан — использует двухфайловый подход:
    объединяет границы из обоих файлов и детектирует instrumental.
    Если vocal_file не передан — использует только track_source,
    без детектирования instrumental.
    """
```

**Логика:**
- Если `vocal_file` передан → двухфайловый подход (шаги 2.1–2.3 + шаг 3 + шаг 4).
- Если `vocal_file` не передан → однофайловый подход: только `msaf.process(audio_file)`, пропустить шаг 2.3 (vocal_energy), не детектировать `"instrumental"`.

#### Шаг 2.1 — Определение границ через msaf

```python
boundaries_full, labels_full = msaf.process(audio_file, boundaries_id="sf", labels_id="scluster")
if vocal_file:
    boundaries_vocal, labels_vocal = msaf.process(vocal_file, boundaries_id="sf", labels_id="scluster")
```

Если msaf вернул ошибку для одного из файлов — зафиксировать в логе и продолжить только с доступным результатом.

#### Шаг 2.2 — Объединение границ

Алгоритм:
1. Собрать все временные метки границ из обоих файлов в единый отсортированный список.
2. Удалить дубликаты с допуском `boundary_merge_tolerance_sec` (из конфигурации, по умолчанию 2.0 сек).
3. Построить сегменты `(start, end)` из объединённых границ.
4. Применить фильтрацию по минимальной длительности `min_duration_sec` (из конфигурации, по умолчанию 5.0 сек).

```python
merged_boundaries = _merge_boundaries(
    list(boundaries_full),
    list(boundaries_vocal) if vocal_file else [],
    tolerance_sec=self._boundary_merge_tolerance_sec,
)
segments = _boundaries_to_segments(merged_boundaries)
segments = [(s, e) for s, e in segments if (e - s) >= self._min_duration]
```

#### Шаг 2.3 — Определение vocal_energy для каждого сегмента (только если vocal_file передан)

```python
if vocal_file:
    vocal_energy_list = _compute_vocal_energy_per_segment(vocal_file, segments)
else:
    vocal_energy_list = [1.0] * len(segments)  # нет данных — считаем вокал везде
```

Сегменты с `vocal_energy < vocal_silence_threshold` → кандидаты на `"instrumental"`.

---

### Шаг 3. Реализовать обогащение сегментов признаками librosa

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

Новый приватный метод:

```python
def _enrich_segments_with_librosa(
    self,
    audio_file: str,
    segments: list[tuple[float, float]],
    vocal_energy_list: list[float],
) -> list[dict]:
    """Вычислить librosa-признаки для каждого сегмента.
    
    Returns: список словарей с признаками для каждого сегмента.
    """
```

Для каждого сегмента вычислить:

| Признак | Метод librosa | Описание |
|---------|--------------|----------|
| `sim_score` | `recurrence_matrix` по chroma | Повторяемость сегмента |
| `hpss_score` | `effects.hpss` + RMS | Энергия гармоники |
| `tempo_score` | `feature.tempogram` | Ритмическая стабильность |
| `vocal_energy` | из шага 2.3 | Наличие вокала |
| `chroma_variance` | `feature.chroma_cqt` | Мелодическая сложность |

Все признаки нормализуются в `[0, 1]`.

> ⚠️ **Важно:** `vocal_energy` передаётся в метод из шага 2.3, а не вычисляется заново через librosa. Это позволяет избежать двойной загрузки `vocal_file`.

---

### Шаг 4. Реализовать классификацию типов сегментов

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

Новый приватный метод:

```python
def _classify_segment(
    self,
    features: dict,
    segment_index: int,
    total_segments: int,
    all_features: list[dict],  # для вычисления медиан
) -> str:
```

#### Правила классификации (в порядке приоритета):

```
1. vocal_energy < vocal_silence_threshold (0.05)
   → "instrumental"

2. segment_index == 0 AND duration < 60 сек
   → "intro"

3. segment_index == total_segments - 1 AND duration < 60 сек
   → "outro"

4. sim_score > median(sim_score) + 0.1
   AND hpss_score > median(hpss_score)
   → "chorus"

5. sim_score < median(sim_score) - 0.1
   AND vocal_energy > vocal_silence_threshold
   → "verse"

6. tempo_score < median(tempo_score) - 0.2
   AND vocal_energy > vocal_silence_threshold
   → "bridge"

7. иначе → "verse"  (fallback)
```

Пороги вычисляются динамически на основе медианы признаков по всем сегментам трека.

> ⚠️ **Важно:** правило 1 (`"instrumental"`) должно проверяться **до** правил 2 и 3 (`"intro"`, `"outro"`). Иначе первый сегмент без вокала будет ошибочно помечен как `"intro"`.

---

### Шаг 5. Обновить [`SegmentInfo`](app/chorus_detector.py:37)

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

Расширить поле `segment_type` для поддержки новых значений:

```python
@dataclass
class SegmentInfo:
    start: float
    end: float
    segment_type: str  # "chorus" | "verse" | "bridge" | "intro" | "outro" | "instrumental"
    backend: str       # теперь всегда "dual_file" или "single_file"
    scores: dict[str, float | int | str] = field(default_factory=dict)
```

Добавить в `scores` новые поля:
- `vocal_energy` — средняя энергия вокала в сегменте
- `chroma_variance` — дисперсия хроматограммы
- `sim_score`, `hpss_score`, `tempo_score` — librosa-признаки

> ⚠️ **Важно:** тип `"non-chorus"` удаляется. Все места в коде, где проверяется `segment_type == "non-chorus"`, нужно обновить.

---

### Шаг 6. Обновить [`build_volume_segments()`](app/chorus_detector.py:106)

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

Обновить логику назначения громкости для новых типов сегментов:

```python
def _get_volume_for_segment_type(
    segment_type: str,
    chorus_volume: float,
    default_volume: float,
) -> float:
    if segment_type == "chorus":
        return chorus_volume
    elif segment_type == "instrumental":
        return default_volume  # инструментал — вокал на стандартной громкости (или 0.0 по желанию)
    else:
        return default_volume  # verse, bridge, intro, outro
```

> ⚠️ **Вопрос для уточнения:** для `"instrumental"` сегментов — какую громкость использовать? В текущем плане `default_volume`, но логично было бы `0.0` (нет вокала → нет в миксе). Уточнить у заказчика.

Также обновить [`build_volume_segments()`](app/chorus_detector.py:106): убрать проверку `segment_type == "non-chorus"`, заменить на новую логику через `_get_volume_for_segment_type()`.

---

### Шаг 7. Обновить [`_step_detect_chorus()`](app/pipeline.py:850) в pipeline

**Файл:** [`app/pipeline.py`](app/pipeline.py)

1. Убрать параметр `backend` при создании `ChorusDetector` (строка 881).
2. Заменить вызов `detector.detect_with_info(...)` на `detector.detect(...)`.
3. Передать `vocal_file` в `detect()`.
4. Обновить создание `ChorusDetector` с новыми параметрами конфигурации.

```python
detector = ChorusDetector(
    min_duration_sec=self._settings.chorus_min_duration_sec,
    vocal_silence_threshold=self._settings.chorus_vocal_silence_threshold,
    boundary_merge_tolerance_sec=self._settings.chorus_boundary_merge_tolerance_sec,
)
segment_infos = await asyncio.get_event_loop().run_in_executor(
    None,
    lambda: detector.detect(
        full_file_str,
        vocal_file=self._state.vocal_file,  # ← передать vocal_file
    ),
)
```

---

### Шаг 8. Обновить конфигурацию

**Файлы:** [`app/config.py`](app/config.py), [`example.env`](example.env)

**Удалить** устаревшие параметры:
- `chorus_detector_backend` / `CHORUS_DETECTOR_BACKEND`
- `chorus_max_duration_sec` / `CHORUS_MAX_DURATION_SEC`

**Переименовать:**
- `chorus_min_duration_sec` / `CHORUS_MIN_DURATION_SEC` → оставить, но изменить значение по умолчанию с `15.0` на `5.0`

**Добавить новые:**

| Параметр в config.py | Переменная .env | По умолчанию | Описание |
|---------------------|----------------|-------------|----------|
| `chorus_vocal_silence_threshold` | `CHORUS_VOCAL_SILENCE_THRESHOLD` | `0.05` | Порог энергии вокала для определения инструментального сегмента |
| `chorus_boundary_merge_tolerance_sec` | `CHORUS_BOUNDARY_MERGE_TOLERANCE_SEC` | `2.0` | Допуск (сек) при объединении границ из двух файлов |

---

### Шаг 9. Добавить вспомогательные функции (вне класса)

**Файл:** [`app/chorus_detector.py`](app/chorus_detector.py)

```python
def _merge_boundaries(
    boundaries_a: list[float],
    boundaries_b: list[float],
    tolerance_sec: float = 2.0,
) -> list[float]:
    """Объединить два списка границ с допуском.
    
    Алгоритм: объединить, отсортировать, удалить дубликаты
    (если разница между соседними < tolerance_sec — оставить первый).
    """

def _boundaries_to_segments(
    boundaries: list[float],
) -> list[tuple[float, float]]:
    """Построить сегменты (start, end) из отсортированного списка границ."""

def _compute_vocal_energy_per_segment(
    vocal_file: str,
    segments: list[tuple[float, float]],
) -> list[float]:
    """Вычислить среднюю RMS-энергию вокала для каждого сегмента.
    
    Использует librosa.load() + librosa.feature.rms().
    Возвращает нормализованные значения в [0, 1].
    """
```

---

### Шаг 10. Написать новый скрипт тестирования

**Файл:** [`scripts/test_chorus_detector_new.py`](scripts/test_chorus_detector_new.py)

Проверять строго новый двухфайловый режим.
Названия файлов для теста — прописать хардкодом:

```python
data_dir = Path("\\\\192.168.0.200") / "docker" / "karaoke" / "music" / "Godsmack - Nothing Else Matters"

full_track_path = data_dir / "Godsmack - Nothing Else Matters.mp3"
vocal_path = data_dir / "Godsmack - Nothing Else Matters_(Vocals).mp3"
volume_segments_file = data_dir / "Godsmack - Nothing Else Matters_volume_segments.json"
```

**Задачи теста:**
1. Запустить `detector.detect(full_track_path, vocal_file=vocal_path)` и вывести все сегменты с типами.
2. Запустить `build_volume_segments()` и сохранить результат в `volume_segments_file`.
3. Прочитать `volume_segments_file` и убедиться, что каждый сегмент содержит все поля: `start`, `end`, `volume`, `segment_type`, `backend`, `scores`.
4. Вывести сводную таблицу: тип сегмента → количество, суммарная длительность.
5. Для каждого сегмента вывести: тайминг, тип, `vocal_energy`, `sim_score`, `hpss_score`, `tempo_score`.

**Дополнительно:** запустить в режиме без `vocal_file` и сравнить результаты.

---

## Изменяемые файлы

| Файл | Тип изменений |
|------|--------------|
| [`app/chorus_detector.py`](app/chorus_detector.py) | Полная переработка: удаление логики бэкендов, новый публичный метод `detect()`, новые вспомогательные функции, расширенная классификация |
| [`app/pipeline.py`](app/pipeline.py) | Обновление создания `ChorusDetector` (убрать `backend`), замена `detect_with_info()` на `detect()`, передача `vocal_file` |
| [`app/config.py`](app/config.py) | Удалить `chorus_detector_backend`, `chorus_max_duration_sec`; добавить `chorus_vocal_silence_threshold`, `chorus_boundary_merge_tolerance_sec`; изменить дефолт `chorus_min_duration_sec` с 15.0 на 5.0 |
| [`example.env`](example.env) | Обновить параметры конфигурации |
| [`scripts/test_chorus_detector_new.py`](scripts/test_chorus_detector_new.py) | Новый скрипт тестирования двухфайлового режима |

---

## Что НЕ меняется

- Интерфейс [`VolumeSegment`](app/chorus_detector.py:73) — структура dataclass сохраняется.
- Функции [`save_volume_segments()`](app/chorus_detector.py:191) и [`load_volume_segments()`](app/chorus_detector.py:234) — сохраняются без изменений.
- Поля [`SegmentInfo`](app/chorus_detector.py:37) (`start`, `end`, `segment_type`, `backend`, `scores`) — сохраняются, расширяются новыми значениями.
- Шаг [`MIX_AUDIO`](app/pipeline.py:933) — не меняется, работает с `volume_segments_file`.

---

## Проверка

- После выполнения шага `DETECT_CHORUS` в `volume_segments_file` появляются сегменты с типами `chorus`, `verse`, `bridge`, `intro`, `outro`, `instrumental`.
- Сегменты с отсутствием вокала в `vocal_file` корректно помечаются как `"instrumental"`.
- При `vocal_file=None` алгоритм работает только через `msaf` на `track_source`, без детектирования `"instrumental"`.
- Скрипт [`scripts/test_chorus_detector_new.py`](scripts/test_chorus_detector_new.py) успешно запускается с двумя файлами и выводит расширенную классификацию.
- Пайплайн проходит шаг `DETECT_CHORUS` без ошибок, `volume_segments_file` корректно используется в шаге `MIX_AUDIO`.
- Метод `detect_with_info()` полностью удалён — все вызовы заменены на `detector.detect()`.
