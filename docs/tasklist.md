## Прогресс по итерациям

| Итерация | Краткое описание                                                 | Статус       | Прогресс |
|----------|------------------------------------------------------------------|--------------|----------|
| 1        | Базовый Telegram-бот и echo-ответы                              | ✅ Завершена | ✅      |
| 2        | Приём аудиофайла и создание `track_id`                          | ✅ Завершена | ✅      |
| 3        | Приём произвольной ссылки (кроме `music.yandex.ru`, YouTube)    | ✅ Завершена | ✅      |
| 4        | Каркас доменного пайплайна без интеграций                       | ✅ Завершена | ✅      |
| 5        | Интеграция с `Demucs` (разделение дорожек)                      | ✅ Завершена | ✅      |
| 6        | Интеграция с `speeches.ai` (транскрибация)                      | ✅ Завершена | ✅      |
| 7        | Получение текста песни (авто + ручной ввод)                     | ✅ Завершена | ✅      |
| 8        | Выравнивание текста по таймкодам                                | ✅ Завершена | ✅      |
| 9        | Генерация субтитров `.ass`                                      | ✅ Завершена | ✅      |
| 10       | Рендеринг караоке-видео                                         | ✅ Завершена | ✅      |
| 11       | Продолжение пайплайна после ошибок (`/continue`)                | ✅ Завершена | ✅      |
| 12       | Корректировка таймингов в шаге ALIGN                            | ✅ Завершена | ✅      |
| 13       | Загрузка трека по ссылке с `music.yandex.ru`                    | ✅ Завершена | ✅      |
| 14       | Загрузка трека по ссылке с YouTube                              | ✅ Завершена | ✅      |
| 15       | UX, логирование                                                 | ✅ Завершена | ✅      |
| 16       | Настройка отправки видео в Telegram                             | ✅ Завершена | ✅      |
| 17       | Корректировка транскрипции с использованием LLM (CORRECT_TRANSCRIPT) | ✅ Завершена | ✅      |
| 18       | Перенос GET_LYRICS после DOWNLOAD с запросом языка у пользователя    | ✅ Завершена | ✅      |
| 19       | Корректировка шага GET_LYRICS (пропуск при наличии файла)          | ✅ Завершена | ✅      |
| 20       | Рендеринг видео с несколькими аудиодорожками (Instrumental, Original, Instrumental+Voice) | ✅ Завершена | ✅      |
| 21       | Рендеринг видео с миксом Instrumental + Voice 40%                  | ✅ Завершена | ✅      |
| 22       | Формирование ссылки на MP4 для скачивания                          | ✅ Завершена | ✅      |
| 23       | Уведомление администратора о запросах от неавтор. пользователей    | ✅ Завершена | ✅      |
| 24       | Корректировка уведомлений пайплайна                               | ✅ Завершена | ✅      |
| 25       | Исправление ошибок в уведомлениях пользователя по ходу pipeline   | ✅ Завершена | ✅      |
| 26       | Исправление имени файла и директории                              | ✅ Завершена | ✅      |
| 27       | Удаление лишней информации из transcribe по результатам шага TRANSCRIBE | ✅ Завершена | ✅      |
| 28       | Исследование и исправление загрузки FLAC с Яндекс Музыки          | ✅ Завершена | ✅      |
| 29       | Реализация поиска трека (/search)                                | ✅ Завершена | ✅      |
| 30       | Стандартизация pipeline: рефакторинг шагов и унификация загрузки  | ✅ Завершена | ✅      |
| 31       | Детальное логирование сообщений пользователей и бота              | ✅ Завершена | ✅      |
| 32       | Горячая перезагрузка конфигурации из `.env` без перезапуска бота  | ✅ Завершена | ✅      |
| 33       | Функция "бэк-вокал" в припевах из дорожки Vocal                   | ✅ Завершена | ✅      |
| 34       | Улучшение детектирования припевов в `ChorusDetector`               | ✅ Завершена | ✅      |
| 35       | Разделение шага MIX_AUDIO на DETECT_CHORUS и MIX_AUDIO             | ✅ Завершена | ✅      |
| 36       | Доработка ChorusDetector                                           | ✅ Завершена | ✅      |
| 37       | Отображение данных сегментов в ASS субтитрах                       | ✅ Завершена | ✅      |
| 38       | Графическая визуализация сегментирования трека                     | ✅ Завершена | ✅      |
| 39       | Интеграция TrackVisualizer в пайплайн                              | ✅ Завершена | ✅      |
| 40       | Группировка сегментов по типу (group_volume_segments)             | ✅ Завершена | ✅      |
| 41       | Корректировка границ слов/строк с учётом instrumental-сегментов   | ⏳ Не начата | 🔲      |
| 42       | Доработка шага GET_LYRICS: fallback на транскрипцию с выбором     | ✅ Завершена | ✅      |
| 43       | Рефакторинг VolumeSegment и универсальный merge_segments          | ✅ Завершена | ✅      |
| 44       | Команда /change для изменения типа сегмента                       | ✅ Завершена | ✅      |
| 45       | Countdown в instrumental-сегментах и превью следующей строки     | ✅ Завершена | ✅      |
| 46       | Детальные метрики (1с) в слое визуализации TrackVisualizer       | ✅ Завершена | ✅      |
| 47       | Исправление отрисовки подсегментов по фактическим границам       | ✅ Завершена | ✅      |
| 48       | Оценка UV vs Conda (анализ документации)                         | ✅ Завершена | ✅      |
| 49       | Сравнение Debian 13 vs Ubuntu 24 (GPU).                          | ✅ Завершена | ✅      |
| 50       | Документирование: Архитектура + Конфигурация                     | ✅ Завершена | ✅      |
| 51       | Документирование: Деплоймент                                       | ⏳ Не начата | 🔲      |
| 52       | Документирование: User-Guide                                       | ⏳ Не начата | 🔲      |
| 53       | Сравнение ffmpeg 8.0 vs 6.1 для проекта караоке-бота               | ✅ Завершена | ✅      |
| 54       | Команда /split для разделения сегмента на два подсегмента          | ✅ Завершена | ✅      |


_(Статусы и прогресс обновляются после каждой итерации.)_

---

## Итерационный план разработки

- [x] **Итерация 1 — Базовый Telegram-бот**
  - Минимальный бот на `aiogram` с командой `/start` и echo-ответом
  - Проверка запуска и ответа на текстовые сообщения

- [x] **Итерация 2 — Приём аудиофайла**
  - Поддержка отправки аудиофайла, генерация `track_id`, создание папки трека
  - Проверка: бот отвечает с `track_id` и путём сохранения

- [x] **Итерация 3 — Приём произвольной ссылки**
  - Поддержка произвольных HTTP(S)-ссылок (кроме `music.yandex.ru` и YouTube)
  - Проверка: бот отвечает с `track_id` и сведениями об источнике

- [x] **Итерация 4 — Каркас пайплайна без интеграций**
  - Класс пайплайна с шагами `DOWNLOAD → SEPARATE → TRANSCRIBE → GET_LYRICS → ALIGN → GENERATE_ASS → RENDER_VIDEO` (заглушки)
  - Проверка: бот логирует и сообщает прогресс по каждому шагу

- [x] **Итерация 5 — Разделение голоса и музыки (`Demucs`)**
  - Подключение `DemucsService`, шаг `SEPARATE`, сохранение двух аудиодорожек
  - Проверка: появляются файлы с голосом и музыкой

- [x] **Итерация 6 — Транскрибация голосовой дорожки (`speeches.ai`)**
  - Подключение `SpeechesClient`, шаг `TRANSCRIBE`, сохранение результата в JSON
  - Проверка: создаётся JSON с транскриптом

- [x] **Итерация 7 — Получение текста песни**
  - Реализация шага GET_LYRICS: авто-поиск через поставщиков (Lyrica, Genius API), при неудаче — запрос текста у пользователя через FSM
  - Поддержка конфигурации: `LYRICS_ENABLE_LYRICA`, `LYRICS_ENABLE_GENIUS`
  - Подробнее: [plans/iteration_7_plan.md](plans/iteration_7_plan.md)

- [x] **Итерация 8 — Выравнивание текста по таймкодам**
  - Шаг `ALIGN`: `AlignmentService` с двумя стратегиями — `LrcDirectStrategy` и `SequenceAlignmentStrategy` (алгоритм Нидлмана–Вунша)
  - Интеграция в `KaraokePipeline._step_align()`

- [x] **Итерация 9 — Генерация субтитров `.ass`**
  - Шаг `GENERATE_ASS`: `AssGenerator` с per-segment TextLine и per-word Highlight
  - Параметр конфигурации `ASS_FONT_SIZE`

- [x] **Итерация 10 — Рендеринг караоке-видео**
  - Шаг `RENDER_VIDEO`: `VideoRenderer` через `ffmpeg` с синтетическим фоном и субтитрами
  - Параметры: `VIDEO_WIDTH`, `VIDEO_HEIGHT`, `VIDEO_BACKGROUND_COLOR`, `VIDEO_FFMPEG_PRESET`, `VIDEO_FFMPEG_CRF`

- [x] **Итерация 11 — Продолжение после ошибок**
  - `PipelineState` с персистированием в `state.json`, команды `/continue` и `/step_*`
  - Три режима запуска: свежий / авто-возобновление при FAILED / явный `start_from_step`

- [x] **Итерация 12 — Корректировка таймингов в шаге ALIGN**
  - Добавление "(проигрыш)" перед первым словом строки, если его длительность превышает `MAX_WORD_TIME`
  - Параметры конфигурации: `MAX_WORD_TIME`, `NORMAL_WORD_TIME`
  - Подробнее: [plans/iteration_12_plan.md](plans/iteration_12_plan.md)

- [x] **Итерация 13 — Загрузка трека по ссылке с `music.yandex.ru`**
  - `YandexMusicDownloader` с методами `download()` и `fetch_lyrics()`
  - Автоматическое получение LRC-текста, пропуск шага `GET_LYRICS`, конфигурация `YANDEX_MUSIC_TOKEN`

- [x] **Итерация 14 — Загрузка трека по ссылке с YouTube**
  - `YouTubeDownloader` с методами `get_track_info()` и `download()`
  - Обработчики `_is_youtube_url()` и `_handle_youtube_url()` в `handlers_karaoke.py`

- [x] **Итерация 15 — Полировка UX и инфраструктуры**
  - Улучшение сообщений пользователю, базовое логирование

- [x] **Итерация 16 — Настройка отправки видео в Telegram**
  - Параметр `SEND_VIDEO_TO_USER` (`true`/`false`) для управления отправкой MP4 пользователю
  - При `false` пайплайн завершается без отправки, файл остаётся локально

- [x] **Итерация 17 — Корректировка транскрипции с LLM (CORRECT_TRANSCRIPT)**
  - Новый шаг `CORRECT_TRANSCRIPT` после `TRANSCRIBE`: LLM корректирует транскрипцию на основе текста песни
  - Параметры конфигурации LLM (провайдер, API ключ, модель)
  - Подробнее: [plans/iteration_17_plan.md](plans/iteration_17_plan.md)

- [x] **Итерация 18 — Перенос GET_LYRICS после DOWNLOAD с запросом языка**
  - GET_LYRICS перенесён между DOWNLOAD и SEPARATE в `_ORDERED_STEPS`
  - Убрана зависимость GET_LYRICS от TRANSCRIBE в `_STEP_REQUIRED_ARTIFACTS`

- [x] **Итерация 19 — Корректировка шага GET_LYRICS (пропуск при наличии файла)**
  - Проверка наличия `source_lyrics_file` перед поиском через поставщиков
  - Если файл найден и не пустой — шаг завершается без обращения к внешним сервисам
  - Подробнее: [plans/iteration_19_plan.md](plans/iteration_19_plan.md)

- [x] **Итерация 20 — Рендеринг видео с несколькими аудиодорожками**
  - MP4 с тремя дорожками: Instrumental, Original, Instrumental+Voice
  - Параметр `AUDIO_MIX_VOICE_VOLUME` для настройки громкости голоса в миксе
  - Подробнее: [plans/iteration_20_plan.md](plans/iteration_20_plan.md)

- [x] **Итерация 21 — Рендеринг видео с миксом Instrumental + Voice (объединена с итерацией 20)**
  - Функциональность микса включена в итерацию 20
  - Реализовано через ffmpeg `filter_complex` с `amix`

- [x] **Итерация 22 — Формирование ссылки на MP4 для скачивания**
  - Параметр `CONTENT_EXTERNAL_URL`, формирование ссылки `https://{external_url}/music?getfile=...`
  - Сохранение в `PipelineState.download_url`, отправка пользователю после завершения
  - Подробнее: [plans/iteration_22_plan.md](plans/iteration_22_plan.md)

- [x] **Итерация 23 — Уведомление администратора о неавторизованных пользователях**
  - Функция `_notify_admin_of_unauthorized_access` с кнопками "Добавить" / "Отклонить"
  - Хранение разрешённых/отклонённых пользователей (`user_id`, `user_name`), игнорирование повторных запросов от отклонённых
  - Подробнее: [plans/iteration_23_plan.md](plans/iteration_23_plan.md)

- [x] **Итерация 24 — Корректировка уведомлений пайплайна**
  - Уведомления о начале и завершении шага объединены в одно сообщение (редактирование по `message_id`)
  - При ошибке `CORRECT_TRANSCRIPT` — автоматический переход к следующему шагу; дублирование ошибок и финального сообщения на `ADMIN_ID`
  - Подробнее: [plans/iteration_24_plan.md](plans/iteration_24_plan.md)

- [x] **Итерация 25 — Исправление уведомлений по ходу pipeline**
  - Все уведомления редактируют первое сообщение-ответ бота на запрос пользователя
  - На каждый трек остаётся одно актуальное сообщение от бота
  - Подробнее: [plans/iteration_25_plan.md](plans/iteration_25_plan.md)

- [x] **Итерация 26 — Исправление имени файла и директории**
  - Нормализация `track_stem`: только буквы, цифры, дефис, пробел, подчёркивание; `track_subdir == track_stem`
  - Функция нормализации применяется во всех обработчиках
  - Подробнее: [plans/iteration_26_plan.md](plans/iteration_26_plan.md)

- [x] **Итерация 27 — Очистка транскрипции после шага TRANSCRIBE**
  - Оставить в корне только `duration`, `language`, `segments`, `words`
  - Секция `segments` приводится к формату: `id`, `start`, `end`, `text`
  - Подробнее: [plans/iteration_27_plan.md](plans/iteration_27_plan.md)

- [x] **Итерация 28 — Исправление загрузки FLAC с Яндекс Музыки**
  - Исследование структуры `DownloadInfo`, исправление логики выбора формата в `YandexMusicDownloader.download()`
  - Корректное назначение расширения `.flac` для lossless-формата
  - Подробнее: [plans/iteration_28_plan.md](plans/iteration_28_plan.md)

- [x] **Итерация 29 — Реализация поиска трека (/search)**
  - Команда `/search`: поиск по локальному хранилищу (топ-5, нормализация имён) и на Яндекс Музыке
  - Inline-кнопки выбора результата, обработка выбора с продолжением или перезапуском пайплайна
  - Подробнее: [plans/iteration_29_plan.md](plans/iteration_29_plan.md)

- [x] **Итерация 30 — Стандартизация pipeline: рефакторинг и унификация загрузки**
  - Единый шаг `DOWNLOAD` для всех источников (файл, URL, Яндекс, YouTube, локальный)
  - Новые шаги `ASK_LANGUAGE` и `SEND_VIDEO` перенесены в pipeline; `handlers_karaoke.py` сокращён до маршрутизации
  - Подробнее: [plans/iteration_30_plan.md](plans/iteration_30_plan.md)

- [x] **Итерация 31 — Детальное логирование сообщений (DEBUG)**
  - `UpdateLoggingMiddleware` исправлена: `logger.debug()` вместо `print()`, расширены поля (`user_id`, `chat_id`, тип контента)
  - `LoggingSession(AiohttpSession)` для перехвата всех исходящих вызовов к Telegram API
  - Подробнее: [plans/iteration_31_plan.md](plans/iteration_31_plan.md)

- [x] **Итерация 32 — Горячая перезагрузка конфигурации из `.env`**
  - `ConfigWatcher` в `app/config_watcher.py`: мониторинг `mtime`, перечитывание `.env`, обновление `Settings`
  - Параметры: `ENV_RELOAD_INTERVAL_SEC`, `ENV_RELOAD_ENABLED`; токен и уровень логирования не перезагружаются
  - Подробнее: [plans/iteration_32_plan.md](plans/iteration_32_plan.md)

- [x] **Итерация 33 — Функция "бэк-вокал" в припевах**
  - Новый шаг `MIX_AUDIO`: `ChorusDetector` определяет припевы, `VocalProcessor` применяет разметку громкости
  - Параметры: `CHORUS_BACKVOCAL_VOLUME`, `MIX_AUDIO_ENABLED`, `VOCAL_REVERB_ENABLED`, `VOCAL_ECHO_ENABLED`
  - Подробнее: [plans/iteration_33_plan.md](plans/iteration_33_plan.md)

- [x] **Итерация 34 — Улучшение детектирования припевов в `ChorusDetector`**
  - Новый подход на основе `librosa`: chroma features, self-similarity matrix, tempogram, HPSS
  - Параметры: `CHORUS_DETECTOR_BACKEND` (`msaf`/`librosa`/`hybrid`), `CHORUS_MIN_DURATION_SEC`, `CHORUS_MAX_DURATION_SEC`
  - Подробнее: [plans/iteration_34_plan.md](plans/iteration_34_plan.md)

- [x] **Итерация 35 — Разделение шага MIX_AUDIO на DETECT_CHORUS и MIX_AUDIO**
  - `DETECT_CHORUS`: запуск `ChorusDetector.detect()`, формирование и сохранение `volume_segments_file`
  - `MIX_AUDIO`: загрузка разметки, применение к вокалу, создание `backvocal_mix_file`
  - Подробнее: [plans/iteration_35_plan.md](plans/iteration_35_plan.md)

- [x] **Итерация 36 — Доработка ChorusDetector**
  - Определение границ сегментов по `track_source` и `vocal_file` через `msaf`, объединение сегментов
  - Обогащение сегментов данными из `librosa` (энергия, хрома, ритм), сохранение в `volume_segments_file`
  - Доработка правила определения припева: использование `mean_vocal_energy` для сегментов с `vocal_energy > threshold`
  - Подробнее: [plans/iteration_36_plan.md](plans/iteration_36_plan.md)

- [x] **Итерация 37 — Отображение данных сегментов в ASS субтитрах**
  - В `AssGenerator.generate()` добавить опциональный параметр `volume_segments_path`
  - Для каждого сегмента из `volume_segments_file` генерировать `Dialogue: 0` со стилем `Segments`
  - Отображаемые поля: `segment_type`, `volume`, `vocal_energy`, `chroma_variance`, `sim_score`, `hpss_score`
  - Стиль `Segments` аналогичен `Title`, расположен на 1/4 высоты экрана сверху (`MarginV=270`)
  - Подробнее: [plans/iteration_37_plan.md](plans/iteration_37_plan.md)

- [x] **Итерация 38 — Графическая визуализация сегментирования трека**
  - Новый отделяемый модуль `app/track_visualizer.py` — класс `TrackVisualizer`
  - Принимает только пути к файлам-артефактам: `transcribe_json_file`, `corrected_transcribe_json_file`, `aligned_lyrics_file`, `source_lyrics_file`, `volume_segments_file`
  - Формирует PNG-файл с timeline: слои сегментов, транскрипции, выровненного текста и метрик
  - Цветовая схема по типам сегментов (`chorus`, `verse`, `bridge`, `intro`, `outro`, `instrumental`)
  - Ступенчатые графики метрик: `vocal_energy`, `sim_score`, `hpss_score`
  - Скрипт `scripts/visualize_track.py` для ручного запуска по папке трека
  - Зависимость: `matplotlib` (добавить через `uv add matplotlib`)
  - Подробнее: [plans/iteration_38_plan.md](plans/iteration_38_plan.md)

- [x] **Итерация 39 — Интеграция TrackVisualizer в пайплайн**
   - Опциональный вызов `TrackVisualizer.generate()` в шаге `GENERATE_ASS` при `TRACK_VISUALIZATION_ENABLED=true`
   - Поле `visualization_file` в `PipelineState` — путь к сгенерированному PNG
   - Параметр конфигурации `TRACK_VISUALIZATION_ENABLED` (по умолчанию `false`)
   - Ошибки визуализации логируются как WARNING и не прерывают пайплайн
   - Импорт `matplotlib` выполняется только при включённом флаге
   - Подробнее: [plans/iteration_39_plan.md](plans/iteration_39_plan.md)

- [x] **Итерация 40 — Группировка сегментов по типу**
  - Метод `group_volume_segments` в классе `VolumeSegment` для объединения соседних сегментов с одинаковым типом
  - Сохранение в `{stem}_segment_groups.json` с массивом `scores` внутри групп
  - Обновление `TrackVisualizer` для работы с новым форматом: определение формата (dict/array), отрисовка групп, метрики по массивам
  - Подробнее: [plans/iteration_40_plan.md](plans/iteration_40_plan.md)

- [ ] **Итерация 41 — Корректировка границ слов/строк с учётом instrumental-сегментов**
  - Интеграция `volume_segments_file` в `AlignmentService` для уточнения границ привязки
  - Разметка слов только внутри non-instrumental сегментов
  - Алгоритм обработки пересечений: truncate, split, shift для слов и строк
  - Добавление `(проигрыш)` маркеров при корректировке
  - Подробнее: [plans/iteration_41_plan.md](plans/iteration_41_plan.md)

- [x] **Итерация 42 — Доработка шага GET_LYRICS с fallback на транскрипцию**
   - При отсутствии текста в сервисах показывать inline-кнопки: "📝 Транскрипция" / "📤 Загрузить"
   - При выборе "Транскрипция" — установка флага, продолжение до TRANSCRIBE, генерация текста из segments
   - Предпросмотр сгенерированного текста с кнопками "✅ Ок" / "📤 Загрузить"
   - Поле `use_transcription_as_lyrics` в `PipelineState`, новые FSM-состояния
   - Подробнее: [plans/iteration_42_plan.md](plans/iteration_42_plan.md)

- [x] **Итерация 43 — Рефакторинг VolumeSegment и универсальный merge_segments**
   - Новый класс `SegmentScore` для метрик суб-сегмента; `VolumeSegment.scores` теперь всегда `list[SegmentScore]`
   - Универсальный метод `merge_segments()` с предикатами и стратегиями (`COMBINE`, `ABSORB_SHORT`)
   - Замена `_merge_short_segments` и `group_volume_segments` на `merge_segments()`
   - Упрощение `TrackVisualizer` — убрать проверку `isinstance(scores, list)`
   - Сохранение обратной совместимости при загрузке старых JSON-файлов
   - Подробнее: [plans/iteration_43_plan.md](plans/iteration_43_plan.md)

- [x] **Итерация 44 — Команда /change для изменения типа сегмента**
   - Команда `/change <диапазон>` для изменения типа сегментов в `volume_segments_file`
   - Поддержка форматов: `1,2,3`, `5-10`, `1,3,5-7`
   - Inline-кнопки выбора типа: chorus, verse, instrumental
   - Автоматический пересчёт volume при смене типа
   - Кнопка "🔄 Пересчитать" для запуска пайплайна с шага MIX_AUDIO
   - Подробнее: [plans/iteration_44_plan.md](plans/iteration_44_plan.md)

- [x] **Итерация 45 — Countdown в instrumental-сегментах и превью следующей строки**
   - Обратный отсчет 3-2-1 в конце instrumental-сегментов (заменяет ActiveLine)
   - Превью следующей строки за 3 секунды до окончания instrumental (NextLine)
   - Адаптация для коротких instrumental (< 3 сек)
   - Параметры конфигурации: `ASS_COUNTDOWN_ENABLED`, `ASS_COUNTDOWN_SECONDS`
   - Подробнее: [plans/iteration_45_plan.md](plans/iteration_45_plan.md)

- [x] **Итерация 46 — Детальные метрики (1с) в слое визуализации TrackVisualizer**
     - `ChorusDetector`: вычисление метрик `vocal_energy`, `chroma_variance`, `hpss_score` с шагом 1 секунда
     - `FrameFeatures`: новый dataclass для хранения frame-level признаков (один проход извлечения)
     - Сохранение детальных метрик в отдельный файл `{stem}_metrics.json` через `save_detailed_metrics()`
     - `TrackVisualizer`: отрисовка детальных линий (тонкие, полупрозрачные) в слое метрик наряду с сегментными
     - `PipelineState`: новое поле `detailed_metrics_file` для пути к файлу метрик
     - Подробнее: [plans/iteration_46_plan.md](plans/iteration_46_plan.md)

- [x] **Итерация 47 — Исправление отрисовки подсегментов по фактическим границам**
       - Добавить поля `start`/`end` в `SegmentScore` для хранения фактических временных границ подсегментов
       - Обновить `build_volume_segments()` для сохранения границ подсегментов при создании `VolumeSegment`
       - Исправить `_draw_segments_layer()` в `TrackVisualizer`: отрисовка подсегментов по фактическим границам вместо усреднения
       - Обновить `_draw_metrics_layer()` для корректного отображения метрик по фактическим границам
       - Обратная совместимость: fallback на равномерное распределение при отсутствии `start`/`end` в старых файлах
       - Подробнее: [plans/iteration_47_plan.md](plans/iteration_47_plan.md)

- [x] **Итерация 48 — Оценка UV vs Conda (анализ документации)**
       - Документальный анализ UV vs Conda для ML-зависимостей
       - Сравнение по критериям: скорость, размер образа, GPU, воспроизводимость
       - Анализ GPU-поддержки для demucs (CPU достаточно)
       - Рекомендация: оставить UV
       - Артефакт: `docs/uv_vs_conda_analysis.md`
       - Подробнее: [plans/iteration_48_plan.md](plans/iteration_48_plan.md)

- [x] **Итерация 49 — Сравнение Debian 13 vs Ubuntu 24 (GPU)**
         - Сравнение базовых образов с акцентом на GPU-поддержку
         - Анализ: `debian:13-slim` vs `ubuntu:24.04` vs `nvidia/cuda:12-base-ubuntu24`
         - **Ключевой вывод: GPU не требуется для проекта с 1-2 пользователями**
         - Оценка необходимости GPU: CPU-only оптимальнее по стабильности (согласно ADR-001)
         - Сравнение производительности demucs: CPU 30-60 сек vs GPU 10-20 сек (некритично)
         - Рекомендация: Ubuntu 24.04 LTS с CPU-only конфигурацией
         - **ADR-002**: архитектурное решение об отказе от GPU
         - Артефакты: `Dockerfile.ubuntu24`, `Dockerfile.ubuntu24-rocm`, `docker-compose.ubuntu24.yaml`, `docs/docker-ubuntu24.md`, `docs/ADR-002. CPU_only_no_GPU.md`
         - Подробнее: [plans/iteration_49_plan.md](plans/iteration_49_plan.md)

- [x] **Итерация 53 — Сравнение ffmpeg 8.0 vs 6.1**
         - Анализ изменений: amix filter, ass filter, многодорожечный муксинг
         - Проверка производительности рендеринга (до 15% улучшение в 8.0)
         - Проверка отсутствия регрессий в качестве аудио/видео
         - **Рекомендация: остаться на FFmpeg 6.1.1 до Ubuntu 26.04 LTS**
         - Причина: стабильность LTS > производительность; 6.1 работает корректно
         - Артефакт: `docs/ffmpeg-comparison.md`
         - Подробнее: [plans/ffmpeg_comparison_plan.md](plans/ffmpeg_comparison_plan.md)

 - [x] **Итерация 50 — Документирование: Архитектура + Конфигурация**
       - `docs/architecture/index.md` — обзор архитектуры
       - `docs/architecture/components.md` — описание всех компонентов
       - `docs/architecture/data-flow.md` — потоки данных
       - `docs/architecture/message-flow.md` — потоки сообщений
       - `docs/architecture/pipeline.md` — детали пайплайна
       - `docs/architecture/models.md` — модели данных
       - `docs/configuration/index.md` — обзор конфигурации
       - `docs/configuration/env-reference.md` — справочник всех переменных
       - Подробнее: [plans/iteration_50_plan.md](plans/iteration_50_plan.md)

- [ ] **Итерация 51 — Документирование: Деплоймент**
       - `docs/deployment/index.md` — обзор вариантов деплоя
       - `docs/deployment/docker.md` — Docker деплой
       - `docs/deployment/local.md` — локальный запуск
       - Подробнее: [plans/iteration_51_plan.md](plans/iteration_51_plan.md)

- [ ] **Итерация 52 — Документирование: User-Guide**
       - `docs/user-guide/index.md` — введение
       - `docs/user-guide/workflow.md` — рабочий процесс
       - `docs/user-guide/commands.md` — команды бота
       - `docs/user-guide/tips.md` — советы и ограничения
       - Подробнее: [plans/iteration_52_plan.md](plans/iteration_52_plan.md)

- [ ] **Итерация 53 — Сравнение ffmpeg 8.0 vs 6.1**
        - Анализ изменений: amix filter, ass filter, многодорожечный муксинг
        - Тестирование производительности рендеринга
        - Проверка отсутствия регрессий в качестве аудио/видео
        - Рекомендация: обновление/сохранение текущей версии
        - Артефакты: `docs/ffmpeg-comparison.md`, обновление `Dockerfile.ubuntu24` (опционально)
        - Подробнее: [plans/ffmpeg_comparison_plan.md](plans/ffmpeg_comparison_plan.md)

- [x] **Итерация 54 — Команда /split для разделения сегмента**
          - Команда `/split <m:ss>` или `/split <seconds>` для ручного разделения сегмента
          - Разделение сегмента на два подсегмента с сохранением типа
          - Пересчёт метрик через `ChorusDetector._aggregate_segment_features()`
          - Формат ответа: "Было → Стало" с временными рамками и типами
          - Кнопки: `🔄 Пересчитать` (запуск с MIX_AUDIO) и `📊 Показать` (визуализация)
          - Интеграция в `SegmentChangeService` и `handlers_karaoke.py`
          - Подробнее: [plans/iteration_54_plan.md](plans/iteration_54_plan.md)

- [ ] **Итерация 55 — Стратегия Forced Alignment на основе MMS_FA**
          - Новая стратегия `ForcedAlignmentStrategy` в `AlignmentService` с использованием MMS_FA из torchaudio
          - Конвертация `vocal_file` в WAV 16kHz mono для подачи на вход модели
          - Использование `source_lyrics_file` как входной транскрипции
          - Сохранение результата в `aligned_lyrics_file` с форматом word-level и line-level таймкодов
          - Параметр конфигурации `ALIGN_ENABLE_FORCED` для включения стратегии
          - Подробнее: [plans/iteration_55_plan.md](plans/iteration_55_plan.md)
