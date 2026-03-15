# Итерация 7 — Получение текста песни

- [x] **Итерация 7 — Получение текста песни**
  - Реализация шага `GET_LYRICS`: сначала авто-поиск текста через sub-pipeline поставщиков, при неудаче — запрос полного текста у пользователя.
  - Sub-pipeline авто-поиска (`GET_LYRICS`) последовательно пробует следующих поставщиков (каждый включается/отключается через конфигурацию):
    - [x] Поиск через `Lyrica` ([github.com/Wilooper/Lyrica](https://github.com/Wilooper/Lyrica)) — управляется флагом `LYRICS_ENABLE_LYRICA` (по умолчанию `false`)
    - [ ] Поиск через `lyrics-lib` ([npmjs.com/package/lyrics-lib](https://www.npmjs.com/package/lyrics-lib)) — управляется флагом `LYRICS_ENABLE_LYRICSLIB` (по умолчанию `false`)
    - [x] Поиск через `Genius API` (`lyricsgenius`) — управляется флагом `LYRICS_ENABLE_GENIUS` (по умолчанию `true` при наличии `GENIUS_TOKEN`)
  - Если все поставщики вернули `None` — запрос текста у пользователя через FSM (`LyricsStates.waiting_for_lyrics`)
  - [x] Добавить в `app/config.py` параметры: `LYRICS_ENABLE_LYRICA`, `LYRICS_ENABLE_LYRICSLIB`, `LYRICS_ENABLE_GENIUS`
  - [x] Реализовать `LyricsService` с поддержкой `Genius API` (`lyricsgenius`)
  - [x] Реализовать `LyricsNotFoundError` и обработку в `KaraokePipeline._step_get_lyrics()`
  - [x] Реализовать FSM-хендлер `handle_lyrics_input` для ручного ввода текста
  - [x] Реализовать поставщика `Lyrica` в `LyricsService` (когда `LYRICS_ENABLE_LYRICA=true`)
  - [ ] Реализовать поставщика `lyrics-lib` в `LyricsService` (когда `LYRICS_ENABLE_LYRICSLIB=true`)
  - Проверка: бот либо сам находит текст (через одного из включённых поставщиков), либо корректно запрашивает и сохраняет текст у пользователя, сообщает результат.
