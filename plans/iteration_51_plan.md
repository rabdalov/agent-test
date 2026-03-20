# Итерация 51: Документирование — Деплоймент

## Цель
Создать документацию по развёртыванию проекта: Docker, локальный запуск, обновление.

## Создаваемые документы

### `docs/deployment/index.md`
Обзор вариантов деплоя:
- Быстрый выбор (таблица: сценарий → рекомендуемый способ)
- Сравнение: Docker vs Локальный запуск
- Требования к системе

### `docs/deployment/docker.md`
Полное руководство по Docker-деплою:

#### Требования
- Docker 24.0+
- Docker Compose plugin
- 4 GB RAM минимум
- 10 GB свободного места

#### Быстрый старт
```bash
git clone <repo>
cd karaoke-telegram-bot
cp example.env .env
# Отредактировать .env

docker-compose up --build -d
```

#### Пошаговая настройка
1. Клонирование репозитория
2. Настройка `.env` (ссылка на конфигурацию)
3. Сборка образа
4. Первый запуск
5. Проверка работоспособности

#### Docker Compose конфигурация
- Описание всех сервисов
- Volume (треки, модели demucs)
- Environment variables
- Resource limits

#### Обновление
```bash
# Получить обновления
git pull

# Пересобрать и перезапусти
docker-compose down
docker-compose up --build -d
```

#### Устранение неполадок
- Логи: `docker-compose logs -f bot`
- Пересборка без кэша: `--no-cache`
- Очистка: `docker system prune`

### `docs/deployment/local.md`
Руководство по локальному запуску (без Docker):

#### Требования
- Python 3.12
- FFmpeg 5.x+
- UV (рекомендуется) или pip

#### Установка UV
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Настройка
```bash
uv sync
```

#### Запуск
```bash
uv run python -m app.main
```

#### Windows-специфика
- Установка FFmpeg через winget/chocolatey
- PowerShell команды

## Структура

```
docs/deployment/
├── index.md      # Обзор вариантов
├── docker.md     # Docker деплой
└── local.md      # Локальный запуск
```

## Definition of Done

- [ ] Все 3 документа созданы
- [ ] Команды copy-paste ready
- [ ] Описаны все Dockerfile из проекта
- [ ] Есть раздел troubleshooting
