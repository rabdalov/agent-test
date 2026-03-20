# Поток сообщений

Документация диаграмм взаимодействия пользователя с ботом через Telegram.

## Цветовое кодирование

- **Синий** — команды пользователя
- **Зелёный** — ответы бота
- **Оранжевый** — FSM переходы
- **Красный** — Admin действия
- **Серый** — Pipeline шаги

---

## 1. Базовый сценарий обработки трека

```mermaid
sequenceDiagram
    autonumber
    actor U as 👤 Пользователь
    participant B as 🤖 Бот
    participant FSM as 📋 FSM
    participant P as ⚙️ Pipeline

    U->>B: <span style="color:blue">/start</span>
    B->>U: <span style="color:green">Привет! Отправьте аудио или ссылку...</span>

    U->>B: <span style="color:blue">[аудио файл]</span>
    B->>FSM: <span style="color:orange">set_state(TrackLangStates)</span>
    B->>U: <span style="color:green">⏳ Принято в обработку<br/>track_id: abc123...</span>

    alt Язык не определён
        B->>U: <span style="color:green">Выберите язык:</span>
        Note over B,U: [RU] [EN] [AUTO]
        U->>B: <span style="color:blue">RU</span>
        B->>FSM: <span style="color:orange">clear_state()</span>
    end

    B->>P: <span style="color:gray">DOWNLOAD</span>
    P-->>B: ✅
    B->>U: <span style="color:green">✅ DOWNLOAD завершён</span>

    alt Текст песни не найден
        B->>U: <span style="color:green">Текст не найден. Выберите:</span>
        Note over B,U: [📝 Транскрипция] [📤 Загрузить]
        
        alt Выбрана Транскрипция
            U->>B: <span style="color:blue">📝 Транскрипция</span>
            B->>FSM: <span style="color:orange">set_state(LyricsConfirmStates)</span>
            B->>P: <span style="color:gray">GENERATE_LYRICS</span>
            B->>U: <span style="color:green">Предпросмотр текста...<br/>[✅ Ок] [📤 Загрузить]</span>
            U->>B: <span style="color:blue">✅ Ок</span>
            B->>FSM: <span style="color:orange">clear_state()</span>
        end
    end

    loop Pipeline Steps
        P->>P: <span style="color:gray">SEPARATE → TRANSCRIBE → ALIGN...</span>
        B->>U: <span style="color:green">✅ Шаг завершён (редактирование)</span>
    end

    P->>B: COMPLETED
    B->>U: <span style="color:green">🎉 Готово!<br/>📥 Скачать: [ссылка]</span>
```

---

## 2. Сценарий поиска трека (/search)

```mermaid
sequenceDiagram
    autonumber
    actor U as 👤 Пользователь
    participant B as 🤖 Бот
    participant FSM as 📋 FSM
    participant Store as 💾 Хранилище
    participant YM as 🎵 Яндекс Музыка

    U->>B: <span style="color:blue">/search</span>
    B->>FSM: <span style="color:orange">set_state(SearchStates)</span>
    B->>U: <span style="color:green">Введите название трека или исполнителя:</span>

    U->>B: <span style="color:blue"> Beatles Yesterday</span>
    
    par Параллельный поиск
        B->>Store: Поиск локально
        Store-->>B: Результаты (топ-5)
        B->>YM: Поиск через API
        YM-->>B: Результаты (топ-5)
    end

    B->>U: <span style="color:green">Найдено:<br/>1. [Локально] Beatles - Yesterday<br/>2. [Яндекс] Beatles - Yesterday<br/>...</span>
    Note over B,U: Inline-кнопки выбора

    U->>B: <span style="color:blue">[Выбрать: 1. Локально]</span>
    B->>FSM: <span style="color:orange">clear_state()</span>
    
    alt Локальный файл
        B->>U: <span style="color:green">Запуск пайплайна с пропуском DOWNLOAD...</span>
        Note over B: Начинается с SEPARATE
    else Яндекс Музыка
        B->>U: <span style="color:green">Загрузка и запуск пайплайна...</span>
        Note over B: Начинается с DOWNLOAD
    end
```

---

## 3. Сценарий изменения сегментов (/change)

```mermaid
sequenceDiagram
    autonumber
    actor U as 👤 Пользователь
    participant B as 🤖 Бот
    participant FSM as 📋 FSM
    participant SCS as 🔄 SegmentChangeService

    U->>B: <span style="color:blue">/change 5-10</span>
    B->>SCS: Парсинг диапазона
    
    alt Некорректный диапазон
        B->>U: <span style="color:green">❌ Неверный формат. Используйте: 1,2,3 или 5-10</span>
    else Корректный диапазон
        B->>FSM: <span style="color:orange">set_state(SegmentChangeStates)</span>
        B->>U: <span style="color:green">Выберите тип для сегментов 5-10:</span>
        Note over B,U: [🎵 Chorus] [🎤 Verse] [🎹 Instrumental]
        
        U->>B: <span style="color:blue">🎵 Chorus</span>
        B->>SCS: Изменить тип
        SCS-->>B: ✅ Тип изменён, volume пересчитан
        
        B->>U: <span style="color:green">✅ Сегменты 5-10 изменены на Chorus<br/>[🔄 Пересчитать]</span>
        
        U->>B: <span style="color:blue">🔄 Пересчитать</span>
        B->>FSM: <span style="color:orange">clear_state()</span>
        B->>U: <span style="color:green">Перезапуск с шага MIX_AUDIO...</span>
    end
```

---

## 4. Сценарий управления доступом (Admin Flow)

```mermaid
sequenceDiagram
    autonumber
    actor NU as 👤 Новый пользователь
    actor A as 🔴 Администратор
    participant B as 🤖 Бот
    participant S as ⚙️ Settings

    NU->>B: <span style="color:blue">Любое сообщение</span>
    B->>S: is_user_allowed(NU.id)?
    S-->>B: false
    B->>S: is_user_denied(NU.id)?
    S-->>B: false
    
    B->>A: <span style="color:red">⚠️ Запрос доступа</span>
    Note over B,A: ID: 123456789<br/>Имя: @newuser<br/>[✅ Добавить] [❌ Отклонить]

    alt Администратор разрешает
        A->>B: <span style="color:red">✅ Добавить</span>
        B->>S: add_allowed_user(NU.id)
        B->>NU: <span style="color:green">✅ Доступ разрешён!<br/>Отправьте /start</span>
        B->>A: <span style="color:green">Пользователь добавлен</span>
    else Администратор отклоняет
        A->>B: <span style="color:red">❌ Отклонить</span>
        B->>S: add_denied_user(NU.id)
        B->>NU: <span style="color:green">⛔ Доступ отклонён</span>
        B->>A: <span style="color:green">Пользователь отклонён</span>
        
        Note over NU,B: Последующие сообщения игнорируются
    end
```

---

## 5. Сценарий продолжения после ошибки (/continue)

```mermaid
sequenceDiagram
    autonumber
    actor U as 👤 Пользователь
    participant B as 🤖 Бот
    participant P as ⚙️ Pipeline
    participant S as 💾 State.json

    Note over U,B: Произошла ошибка на шаге TRANSCRIBE
    
    U->>B: <span style="color:blue">/continue</span>
    B->>S: Загрузка последнего состояния
    S-->>B: status=FAILED, current_step=TRANSCRIBE
    
    alt Артефакты на месте
        B->>P: from_state(state)
        B->>P: run(start_from_step=TRANSCRIBE)
        B->>U: <span style="color:green">🔄 Возобновление с шага TRANSCRIBE...</span>
        
        loop Продолжение пайплайна
            P->>P: TRANSCRIBE → ALIGN → ...
            B->>U: <span style="color:green">✅ Шаг завершён</span>
        end
        
        B->>U: <span style="color:green">🎉 Готово!</span>
    else Артефакты потеряны
        B->>U: <span style="color:green">❌ Невозможно продолжить. Начните заново.</span>
    end
```

---

## 6. FSM-состояния и переходы

### Таблица FSM States

| StatesGroup | Состояние | Триггер перехода | Следующее состояние |
|-------------|-----------|------------------|---------------------|
| `TrackLangStates` | `waiting_for_lang` | Отправка файла/ссылки без языка | clear_state() |
| `LyricsStates` | `waiting_for_lyrics` | Текст не найден, запрос ручного ввода | clear_state() |
| `LyricsChoiceStates` | `waiting_for_choice` | Текст не найден, выбор источника | → LyricsConfirmStates или clear_state() |
| `LyricsConfirmStates` | `waiting_for_confirmation` | Генерация текста из транскрипции | clear_state() |
| `SearchStates` | `waiting_for_query` | Команда /search | → waiting_for_selection |
| `SearchStates` | `waiting_for_selection` | Получены результаты поиска | clear_state() |
| `SegmentChangeStates` | `waiting_for_type_selection` | Команда /change | clear_state() |

### Диаграмма переходов FSM

```mermaid
stateDiagram-v2
    [*] --> Idle: /start
    
    Idle --> TrackLangStates: Отправка файла
    TrackLangStates --> Idle: Выбор языка
    
    Idle --> LyricsChoiceStates: Текст не найден
    LyricsChoiceStates --> LyricsConfirmStates: Выбор "Транскрипция"
    LyricsChoiceStates --> LyricsStates: Выбор "Загрузить"
    LyricsStates --> Idle: Получен текст
    LyricsConfirmStates --> Idle: Подтверждение
    
    Idle --> SearchStates: /search
    SearchStates --> SearchStates: Ввод запроса
    SearchStates --> Idle: Выбор результата
    
    Idle --> SegmentChangeStates: /change
    SegmentChangeStates --> Idle: Выбор типа + Пересчитать
    
    Idle --> Pipeline: Запуск обработки
    Pipeline --> WaitingForInput: Нужен ввод
    WaitingForInput --> Idle: Получен ввод
    Pipeline --> Error: Ошибка
    Error --> Pipeline: /continue
    Pipeline --> [*]: Завершено
```

---

## Реализация в коде

### Инициализация FSM

```python
# app/bot_app.py
from aiogram.fsm.storage.memory import MemoryStorage

dp = Dispatcher(storage=MemoryStorage())
```

### Определение состояний

```python
# app/models.py
from aiogram.fsm.state import State, StatesGroup

class TrackLangStates(StatesGroup):
    waiting_for_lang = State()

class LyricsStates(StatesGroup):
    waiting_for_lyrics = State()

class LyricsChoiceStates(StatesGroup):
    waiting_for_choice = State()

class LyricsConfirmStates(StatesGroup):
    waiting_for_confirmation = State()

class SearchStates(StatesGroup):
    waiting_for_query = State()
    waiting_for_selection = State()

class SegmentChangeStates(StatesGroup):
    waiting_for_type_selection = State()
```

### Использование в хендлерах

```python
# app/handlers_karaoke.py
from aiogram.fsm.context import FSMContext

@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext):
    await state.set_state(SearchStates.waiting_for_query)
    await message.answer("Введите название трека:")

@router.message(SearchStates.waiting_for_query)
async def process_search_query(message: Message, state: FSMContext):
    # Обработка запроса
    await state.set_state(SearchStates.waiting_for_selection)
    # Показ результатов с inline-кнопками

@router.callback_query(SearchStates.waiting_for_selection, F.data.startswith("search:"))
async def process_search_selection(callback: CallbackQuery, state: FSMContext):
    # Обработка выбора
    await state.clear()
    # Запуск пайплайна
```

---

## Cross-references

- Реализация FSM: [`app/handlers_karaoke.py`](app/handlers_karaoke.py)
- Определение состояний: [`app/models.py`](app/models.py)
- Обработка команд: [`KaraokeHandlers`](app/handlers_karaoke.py)
- Список всех команд: [`docs/bot_commands.md`](docs/bot_commands.md)
