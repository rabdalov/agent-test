# Поток сообщений

Документация диаграмм взаимодействия пользователя с ботом через Telegram.

---

## 1. Базовый сценарий обработки трека

```mermaid
sequenceDiagram
    autonumber
    actor U as 👤 Пользователь
    participant B as 🤖 Бот
    participant FSM as 📋 FSM
    participant P as ⚙️ Pipeline

    U->>B: /start
    B->>U: Привет! Отправьте аудио или ссылку...

    U->>B: [аудио файл]
    B->>FSM: set_state(TrackLangStates)
    B->>U: ⏳ Принято в обработку<br/>track_id: abc123...

    alt Язык не определён
        B->>U: Выберите язык:
        Note over B,U: [RU] [EN] [AUTO]
        U->>B: RU
        B->>FSM: clear_state()
    end

    B->>P: DOWNLOAD
    P-->>B: ✅
    B->>U: ✅ DOWNLOAD завершён

    alt Текст песни не найден
        B->>U: Текст не найден. Выберите:
        Note over B,U: [📝 Транскрипция] [📤 Загрузить]
        
        alt Выбрана Транскрипция
            U->>B: 📝 Транскрипция
            B->>FSM: set_state(LyricsConfirmStates)
            B->>P: GENERATE_LYRICS
            B->>U: Предпросмотр текста...<br/>[✅ Ок] [📤 Загрузить]
            U->>B: ✅ Ок
            B->>FSM: clear_state()
        end
    end

    loop Pipeline Steps
        P->>P: SEPARATE → TRANSCRIBE → ALIGN...
        B->>U: ✅ Шаг завершён (редактирование)
    end

    P->>B: COMPLETED
    B->>U: 🎉 Готово!<br/>📥 Скачать: [ссылка]
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

    U->>B: /search
    B->>FSM: set_state(SearchStates)
    B->>U: Введите название трека или исполнителя:

    U->>B: Beatles Yesterday
    
    par Параллельный поиск
        B->>Store: Поиск локально
        Store-->>B: Результаты (топ-5)
        B->>YM: Поиск через API
        YM-->>B: Результаты (топ-5)
    end

    B->>U: Найдено:<br/>1. [Локально] Beatles - Yesterday<br/>2. [Яндекс] Beatles - Yesterday<br/>...
    Note over B,U: Inline-кнопки выбора

    U->>B: [Выбрать: 1. Локально]
    B->>FSM: clear_state()
    
    alt Локальный файл
        B->>U: Запуск пайплайна с пропуском DOWNLOAD...
        Note over B: Начинается с SEPARATE
    else Яндекс Музыка
        B->>U: Загрузка и запуск пайплайна...
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

    U->>B: /change 5-10
    B->>SCS: Парсинг диапазона
    
    alt Некорректный диапазон
        B->>U: ❌ Неверный формат. Используйте: 1,2,3 или 5-10
    else Корректный диапазон
        B->>FSM: set_state(SegmentChangeStates)
        B->>U: Выберите тип для сегментов 5-10:
        Note over B,U: [🎵 Chorus] [🎤 Verse] [🎹 Instrumental]
        
        U->>B: 🎵 Chorus
        B->>SCS: Изменить тип
        SCS-->>B: ✅ Тип изменён, volume пересчитан
        
        B->>U: ✅ Сегменты 5-10 изменены на Chorus<br/>[🔄 Пересчитать]
        
        U->>B: 🔄 Пересчитать
        B->>FSM: clear_state()
        B->>U: Перезапуск с шага MIX_AUDIO...
    end
```

---

## 4. Сценарий управления доступом (Admin Flow)

### Алгоритм проверки доступа

Доступ разрешён, если выполняется **любое** из условий:
1. Пользователь является администратором (`user_id == ADMIN_ID`)
2. Пользователь явно добавлен в список разрешённых (`allowed_users`)
3. Пользователь есть в списке `TLG_ALLOWED_ID`

Доступ **отклонён** (без уведомления администратора), если:
- Пользователь в списке `denied_users`

Новые пользователи **не имеют доступа по умолчанию** — если списки пусты, доступ запрещён.

```mermaid
sequenceDiagram
    autonumber
    actor NU as 👤 Новый пользователь
    actor A as 🔴 Администратор
    participant B as 🤖 Бот
    participant S as ⚙️ Settings

    NU->>B: Любое сообщение
    B->>B: _is_user_allowed(NU.id)
    Note over B: Проверка: admin? → denied? → allowed?
    B->>S: is_user_denied(NU.id)?
    S-->>B: false
    B->>S: is_user_allowed(NU.id)?
    S-->>B: false
    B->>S: user_id in tlg_allowed_id?
    S-->>B: false
    
    B->>NU: ⛔ У вас нет доступа к этому боту
    B->>A: ⚠️ Запрос доступа
    Note over B,A: ID: 123456789<br/>Имя: @newuser<br/>[✅ Добавить] [❌ Отклонить]

    alt Администратор разрешает
        A->>B: ✅ Добавить
        B->>S: add_allowed_user(NU.id)
        B->>NU: ✅ Вам предоставлен доступ к боту!<br/>Отправьте /start
        B->>A: ✅ Пользователь добавлен
    else Администратор отклоняет
        A->>B: ❌ Отклонить
        B->>S: add_denied_user(NU.id)
        B->>NU: ❌ Ваш запрос на доступ к боту отклонён
        B->>A: ❌ Пользователь отклонён
        
        Note over NU,B: Последующие сообщения игнорируются
    end
```

### Реализация в коде

**Проверка доступа для сообщений:**
```python
# app/handlers_karaoke.py

def _is_user_allowed(self, message: types.Message) -> bool:
    """Return True if the sender's user_id is in the allowed list."""
    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return False
    # Admin always has access
    if user_id == self._settings.admin_id:
        return True
    if self._settings.is_user_denied(user_id):
        return False
    if self._settings.is_user_allowed(user_id):
        return True
    allowed = self._settings.tlg_allowed_id
    return user_id in allowed
```

**Проверка доступа для callback-запросов:**
```python
def _is_user_id_allowed(self, user_id: int | None) -> bool:
    """Return True if the user_id is allowed (for callback handlers)."""
    if user_id is None:
        return False
    # Admin always has access
    if user_id == self._settings.admin_id:
        return True
    if self._settings.is_user_denied(user_id):
        return False
    if self._settings.is_user_allowed(user_id):
        return True
    allowed = self._settings.tlg_allowed_id
    return user_id in allowed
```

**Обработка решения администратора:**
```python
async def _handle_admin_decision(
    self,
    callback: types.CallbackQuery,
    decision: str,
    user_id: int,
    user_name: str | None
) -> None:
    """Handle admin's decision to allow or deny a user."""
    if decision == "allow":
        self._settings.add_allowed_user(user_id, user_name)
        await callback.answer(f"✅ Пользователь {user_id} добавлен")
        # Уведомляем пользователя
        await callback.bot.send_message(
            chat_id=user_id,
            text="✅ Вам предоставлен доступ к боту!\n\n"
                 "Отправьте /start для начала работы..."
        )
    else:
        self._settings.add_denied_user(user_id, user_name)
        await callback.answer(f"❌ Пользователь {user_id} отклонён")
        # Уведомляем пользователя
        await callback.bot.send_message(
            chat_id=user_id,
            text="❌ Ваш запрос на доступ к боту отклонён."
        )
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
    
    U->>B: /continue
    B->>S: Загрузка последнего состояния
    S-->>B: status=FAILED, current_step=TRANSCRIBE
    
    alt Артефакты на месте
        B->>P: from_state(state)
        B->>P: run(start_from_step=TRANSCRIBE)
        B->>U: 🔄 Возобновление с шага TRANSCRIBE...
        
        loop Продолжение пайплайна
            P->>P: TRANSCRIBE → ALIGN → ...
            B->>U: ✅ Шаг завершён
        end
        
        B->>U: 🎉 Готово!
    else Артефакты потеряны
        B->>U: ❌ Невозможно продолжить. Начните заново.
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
