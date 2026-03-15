# Итерация 31 — Детальное логирование сообщений пользователей и бота (DEBUG)

## Задача

Обеспечить точное логирование средствами встроенного класса `logger` для всех сообщений, отправляемых пользователем боту и ботом пользователю. Использовать уровень логирования `DEBUG`.

## Текущее состояние (проблемы)

- В `app/bot_app.py` класс `UpdateLoggingMiddleware` использует `print()` вместо `logger.debug()` — логи не попадают в стандартную систему логирования.
- В `app/config.py` класс `_AiogramUpdateHandler` и функция `_setup_aiogram_update_logging()` дублируют функциональность middleware и тоже используют `print()`.
- Исходящие сообщения от бота (ответы пользователю) нигде не логируются.
- Входящие сообщения логируются без ключевых полей: `user_id`, `chat_id`, `message_id`, тип контента (аудио, видео, текст, команда, callback).

## Требования к реализации

1. Все логи сообщений — уровень `DEBUG`.
2. Для входящих сообщений логировать: `user_id`, `username`, `chat_id`, `message_id`, тип контента, первые 200 символов текста/команды/callback_data.
3. Для исходящих сообщений от бота логировать: `chat_id`, `message_id` (если доступен), тип действия (`send`, `edit`, `reply`), первые 200 символов текста.
4. Использовать только стандартный `logging.getLogger()` — никаких `print()`.
5. Не дублировать механизмы логирования.
6. Не дублировать текст сообщения для логирования в коде при самой отправке и логировании.

## Архитектурное решение

### Перехват на уровне Bot Session (исходящие) + Middleware (входящие)

- **Для входящих сообщений:** `UpdateLoggingMiddleware` в `app/bot_app.py` — уже существует, нужно исправить.
- **Для исходящих сообщений:** создать `LoggingSession`, наследующий от `AiohttpSession`. aiogram использует `BaseSession` для всех HTTP-запросов к Telegram API — переопределив `make_request()`, можно перехватить ВСЕ исходящие вызовы централизованно, без изменения хендлеров.

### Схема потока данных

```
handlers_karaoke.py
  message.answer() / message.bot.edit_message_text()
        ↓
  Bot (aiogram)
        ↓
  LoggingSession.make_request()   ← перехват исходящих здесь
        ↓
  AiohttpSession (реальный HTTP-запрос к Telegram API)
```

## План реализации

### 1. `app/bot_app.py` — исправить `UpdateLoggingMiddleware` (входящие сообщения)

- Добавить `self._logger = logging.getLogger(__name__)` в `__init__`
- Заменить `print(log_msg)` на `self._logger.debug(log_msg)` в методе `__call__`
- Расширить логируемые поля: добавить `user_id`, `username`, `chat_id`, `message_id` из `event.message` или `event.callback_query`
- Добавить логирование типа контента: аудио, видео, документ, текст, команда

### 2. `app/config.py` — удалить дублирующий механизм

- Удалить класс `_AiogramUpdateHandler`
- Удалить функцию `_setup_aiogram_update_logging()` и её вызов из `setup_logging()`
- Удалить вспомогательные функции `_extract_event_type()` и `_extract_text_preview()`

### 3. `app/bot_app.py` — создать `LoggingSession` (исходящие сообщения)

- Создать класс `LoggingSession(AiohttpSession)` с `self._logger = logging.getLogger(__name__)`
- Переопределить метод `make_request()`: перед вызовом `super().make_request()` логировать на уровне `DEBUG` имя метода API, `chat_id` и первые 200 символов `text` из параметров запроса
- В `BotApp.__init__` передать `LoggingSession()` в конструктор `Bot(session=LoggingSession())`
- Это обеспечит перехват ВСЕХ исходящих вызовов: `sendMessage`, `editMessageText`, `sendVideo`, `sendDocument` и т.д. — без изменения хендлеров

## Формат лог-записей (примеры)

```
DEBUG app.bot_app: [IN] user_id=123456 username=john_doe chat_id=123456 message_id=42 type=audio file_name="Полина Гагарина - Shallow.mp3"
DEBUG app.bot_app: [IN] user_id=123456 username=john_doe chat_id=123456 message_id=43 type=text text="/search"
DEBUG app.bot_app: [IN] user_id=123456 username=john_doe chat_id=123456 message_id=44 type=callback_query data="lang_choice:ru"
DEBUG app.bot_app: [OUT] method=sendMessage chat_id=123456 text="⏳ Принято в обработку. Файл: Полина Гагарина - Shallow.mp3"
DEBUG app.bot_app: [OUT] method=editMessageText chat_id=123456 text="✅ Шаг DOWNLOAD завершён"
DEBUG app.bot_app: [OUT] method=editMessageText chat_id=123456 text="🎉 Обработка завершена успешно!"
DEBUG app.bot_app: [OUT] method=sendVideo chat_id=123456
```

## Проверка

При уровне логирования `DEBUG` в консоли отображаются все входящие сообщения от пользователей и все исходящие ответы бота с указанием `user_id`, `chat_id`, метода API и текста. Хендлеры (`handlers_karaoke.py`) не изменяются.
