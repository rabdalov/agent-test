# Блок-схема шага ALIGN

```
┌─────────────────────────────────────────────────────────────┐
│                   KaraokePipeline._step_align()             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
              ┌───────────────────────────────┐
              │  Проверка артефактов в        │
              │  PipelineState:               │
              │  · transcribe_json_file       │
              │  · source_lyrics_file         │
              └───────────────────────────────┘
                       │             │
                  есть оба       нет хотя бы
                              одного из них
                       │             │
                       ▼             ▼
                               RuntimeError
                            ("шаг TRANSCRIBE /
                             GET_LYRICS не выполнен")
                       │
                       ▼
          ┌────────────────────────┐
          │  AlignmentService      │
          │  .align_timestamps()   │
          └────────────────────────┘
                       │
          ┌────────────┴─────────────┐
          ▼                          ▼
  ┌──────────────────┐      ┌─────────────────────┐
  │ load_transcription│      │ parse_lyrics_text() │
  │ _words()          │      │                     │
  │ Читает JSON       │      │ Читает TXT-файл     │
  └──────────────────┘      └─────────────────────┘
          │                          │
          ▼                          ▼
  List[WordWithTimestamp]   List[(ts | None, line)]
  · word: str                LRC-строки вида:
  · start_time: float         [MM:SS.xx] text
  · end_time: float           или просто: text
          │                          │
          └────────────┬─────────────┘
                       ▼
         ┌──────────────────────────────┐
         │   _select_strategy()         │
         │                              │
         │   Считает долю строк с LRC:  │
         │   stamped / total_lines      │
         └──────────────────────────────┘
                       │
          ┌────────────┴────────────────┐
          │                             │
    fraction ≥ 0.5               fraction < 0.5
  (по умолчанию,                (обычный текст /
   _LRC_THRESHOLD)               мало таймкодов)
          │                             │
          ▼                             ▼
  ┌───────────────┐            ┌─────────────────────┐
  │LrcDirectStrategy│          │SequenceAlignment    │
  │.align()         │          │Strategy.align()     │
  └───────────────┘            └─────────────────────┘
          │                             │
          │                             ▼
          │           ┌─────────────────────────────────┐
          │           │ 1. Flatten lyrics → word list   │
          │           │    + word_to_line[] mapping      │
          │           └─────────────────────────────────┘
          │                             │
          │                             ▼
          │           ┌─────────────────────────────────┐
          │           │ 2. needleman_wunsch()            │
          │           │                                 │
          │           │   seq_a = ASR words             │
          │           │   seq_b = lyrics words          │
          │           │                                 │
          │           │   Скоринг (_word_match_score):  │
          │           │   · точное совпадение → +2      │
          │           │   · префиксное совпадение → +1  │
          │           │   · несовпадение → −1           │
          │           │   · пропуск (gap) → −1          │
          │           │                                 │
          │           │   Возвращает: aligned_asr[],    │
          │           │              aligned_lyrics[]   │
          │           └─────────────────────────────────┘
          │                             │
          │                             ▼
          │           ┌─────────────────────────────────┐
          │           │ 3. Присвоение таймкодов         │
          │           │    matched pair:                │
          │           │      lyrics_word ← ASR times    │
          │           │    ASR gap → пропустить ASR     │
          │           │    lyrics gap → None (пропуск)  │
          │           └─────────────────────────────────┘
          │                             │
          │                             ▼
          │           ┌─────────────────────────────────┐
          │           │ 4. _interpolate_timestamps()    │
          │           │    для None-записей:            │
          │           │    · нет левого якоря → copy    │
          │           │      right.start_time           │
          │           │    · нет правого якоря → copy   │
          │           │      left.end_time              │
          │           │    · оба есть → линейная        │
          │           │      интерполяция между         │
          │           │      left.end и right.start     │
          │           └─────────────────────────────────┘
          │                             │
          ▼                             ▼
  ┌───────────────────────────────────────────────┐
  │  LrcDirectStrategy.align():                   │
  │                                               │
  │  1. Берёт LRC-таймкоды как start_time строки  │
  │  2. end_time строки = start_time следующей    │
  │     (или last ASR word end_time для последней)│
  │  3. Линейная интерполяция по словам строки:   │
  │     step = (end_ts - start_ts) / len(words)  │
  │     word_i: start = start_ts + i * step       │
  │             end   = start_ts + (i+1) * step   │
  └───────────────────────────────────────────────┘
          │
          └────────────────────────────┐
                                       ▼
                        ┌──────────────────────────┐
                        │ AlignedLyricsResult       │
                        │ · words: List[           │
                        │     WordWithTimestamp]    │
                        │ · lines: List[           │
                        │     LineWithTimestamp]    │
                        └──────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────┐
                        │   _sanitise()             │
                        │   · start_time ≥ 0        │
                        │   · end_time ≥ start_time │
                        │   · round(x, 3)           │
                        └──────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────┐
                        │   save_aligned_result()   │
                        │   {stem}.aligned.json:    │
                        │   {                       │
                        │     "words": [...],       │
                        │     "lines": [...]        │
                        │   }                       │
                        └──────────────────────────┘
                                       │
                                       ▼
                        ┌──────────────────────────┐
                        │  PipelineState update:    │
                        │  aligned_lyrics_file =    │
                        │  "{stem}.aligned.json"   │
                        └──────────────────────────┘
                                       │
                                       ▼
                               ✅ Шаг ALIGN завершён
```

## Параметры, влияющие на принятие решений

| Параметр | Где задаётся | Как влияет |
|---|---|---|
| `_LRC_THRESHOLD = 0.5` | [`AlignmentService`](../app/alignment_service.py:516) | Доля строк с LRC-таймкодами, при которой выбирается `LrcDirectStrategy` |
| `_MATCH_SCORE = 2` | [`alignment_service.py`](../app/alignment_service.py:143) | Оценка за точное совпадение слов в NW-алгоритме |
| `_MISMATCH_SCORE = -1` | [`alignment_service.py`](../app/alignment_service.py:144) | Штраф за несовпадение слов |
| `_GAP_PENALTY = -1` | [`alignment_service.py`](../app/alignment_service.py:145) | Штраф за пропуск (gap) в выравнивании |
| Формат JSON транскрипции | Внешний (`speeches.ai`) | Загрузка по `words[]` (верхний уровень) или `segments[].words[]` (вложенный) |
| Формат текста песни | Файл `source_lyrics_file` | LRC (`[MM:SS.xx]`) → `LrcDirectStrategy`; plain text → `SequenceAlignmentStrategy` |
| `vocal_file` в `PipelineState` | Шаг `SEPARATE` | Передаётся в `audio_file` (зарезервировано для forced alignment в будущем) |
