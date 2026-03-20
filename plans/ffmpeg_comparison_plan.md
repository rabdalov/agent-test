# План сравнения ffmpeg 8.0 vs 6.1 для караоке-бота

## Метаданные

| Поле | Значение |
|------|----------|
| **Цель** | Оценить целесообразность перехода с ffmpeg 6.1 на 8.0 для проекта караоке-бота |
| **Текущая версия** | ffmpeg 6.1 (входит в Ubuntu 24.04 LTS) |
| **Целевая версия** | ffmpeg 8.0 (апрель 2025) |
| **Статус** | ⏳ Не начато |

---

## Контекст использования ffmpeg в проекте

Из [`app/video_renderer.py`](app/video_renderer.py:1) и [`Dockerfile.ubuntu24`](Dockerfile.ubuntu24:1):

### Используемые компоненты ffmpeg

| Компонент | Назначение в проекте | Критичность |
|-----------|---------------------|-------------|
| `libx264` | Кодирование видео потока | ✅ Критично |
| `aac` | Кодирование аудио (3-4 дорожки) | ✅ Критично |
| `lavfi` + `color` | Генерация фона для видео | ✅ Критично |
| `ass` filter | Наложение караоке-субтитров | ✅ Критично |
| `amix` filter | Смешивание instrumental + vocal (40%) | ✅ Критично |
| `-map` | Многодорожечный муксинг | ✅ Критично |
| `-metadata:s:a:X` | Метаданные аудиодорожек | ✅ Критично |
| `-disposition:a:X` | Установка дорожки по умолчанию | ✅ Критично |

---

## План исследования

### 1. Анализ изменений в релизах

**Источники:**
- [FFmpeg 7.0 Changelog](https://ffmpeg.org/download.html#release_7.0)
- [FFmpeg 7.1 Changelog](https://ffmpeg.org/download.html#release_7.1)
- [FFmpeg 8.0 Changelog](https://ffmpeg.org/download.html#release_8.0)
- Git log: `git log n6.1..n8.0 --oneline -- libavfilter/af_amix.c libavfilter/vf_ass.c`

**Проверить:**
```bash
# Список коммитов, затрагивающих amix filter
git log n6.1..n8.0 --oneline -- libavfilter/af_amix.c

# Список коммитов, затрагивающих ass filter
git log n6.1..n8.0 --oneline -- libavfilter/vf_ass.c

# Список коммитов, затрагивающих libx264
git log n6.1..n8.0 --oneline -- libavcodec/libx264.c
```

### 2. Ключевые изменения для проекта

#### 2.1. Фильтр `amix` (критично для смешивания)

**Что проверить:**
- Изменения в алгоритме смешивания
- Поддержка параметра `weights` (используется: `weights=1 0.4`)
- Поведение `duration=longest`
- Возможные регрессии в качестве аудио

**Тестовая команда:**
```bash
# Текущий подход в проекте
ffmpeg -i instrumental.mp3 -i vocal.mp3 -filter_complex "[0:a][1:a]amix=inputs=2:duration=longest:weights=1 0.4[out]" -map "[out]" -c:a aac -b:a 320k mix_output.mp3
```

#### 2.2. Фильтр `ass` (критично для субтитров)

**Что проверить:**
- Изменения в рендеринге сложных ASS-эффектов
- Поддержка karaoke-тегов (`\k`, `\kf`, `\ko`)
- Производительность рендеринга
- Исправления багов с позиционированием

**Ссылки на issue:**
- Trac: https://trac.ffmpeg.org/query?status=closed&component=avfilter&order=priority&desc=1

#### 2.3. Многодорожечный муксинг

**Что проверить:**
- Стабильность работы с 4 аудиодорожками
- Правильность записи метаданных `title`
- Поведение `disposition:default`
- Совместимость с MP4 контейнером

### 3. Новые возможности (потенциально полезные)

| Фича | Версия | Польза для проекта |
|------|--------|-------------------|
| Улучшенная производительность libx264 | 7.0+ | Быстрее рендеринг |
| Новые оптимизации `stillimage` tune | 7.0+ | Эффективнее статичное видео |
| Улучшения в `aresample` | 7.1+ | Лучше ресемплинг аудио |
| Исправления багов многодорожечного аудио | 8.0 | Стабильность |

### 4. Потенциальные риски

#### 4.1. Регрессии

| Область | Риск | Митигация |
|---------|------|-----------|
| Изменение синтаксиса фильтров | Средний | Тестирование всех команд |
| Различия в поведении amix | Низкий | A/B тестирование миксов |
| Проблемы с ASS-рендерингом | Средний | Визуальная проверка субтитров |
| Нестабильность новой версии | Средний | Ожидание 8.0.1+ |

#### 4.2. Совместимость с инфраструктурой

- **Ubuntu 24.04**: ffmpeg 6.1 из репозитория
- **Ubuntu 24.10+**: может содержать 7.x
- **Сборка из source**: требует обновления Dockerfile

### 5. Практическое тестирование

#### 5.1. Сценарии тестирования

**Тест A: Рендеринг видео**
```bash
# Сравнение времени рендеринга
# Создать одинаковый input, замерить:
time ffmpeg -f lavfi -i "color=c=black:s=1280x720:r=25" \
  -i instrumental.mp3 -i original.mp3 -i vocal.mp3 \
  -filter_complex "[0:v]ass='subtitles.ass'[vout];[1:a][3:a]amix=inputs=2:duration=longest:weights=1 0.4[a3]" \
  -map "[vout]" -map "1:a" -map "2:a" -map "[a3]" \
  -c:v libx264 -preset fast -tune stillimage -crf 22 \
  -c:a aac -b:a 320k -shortest -pix_fmt yuv420p \
  -metadata:s:a:0 title="Instrumental" -metadata:s:a:1 title="Original" -metadata:s:a:2 title="Instrumental+Voice" \
  -disposition:a:0 default -disposition:a:1 0 -disposition:a:2 0 \
  output.mp4
```

**Тест B: Качество микширования**
- Сравнить waveform mix 6.1 vs 8.0
- Проверить отсутствие clipping/artifacts

**Тест C: Субтитры**
- Проверить корректность отображения karaoke-эффектов
- Убедиться в правильности таймингов

#### 5.2. Docker-контейнеры для тестирования

```dockerfile
# Dockerfile.ffmpeg61 - базовый (текущий)
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y ffmpeg

# Dockerfile.ffmpeg80 - тестовый
FROM ubuntu:24.04
# Установка ffmpeg 8.0 из PPA или сборка из source
RUN apt-get update && apt-get install -y software-properties-common \
  && add-apt-repository ppa:... \
  && apt-get update && apt-get install -y ffmpeg
```

### 6. Критерии принятия решения

| Критерий | Вес | ffmpeg 6.1 | ffmpeg 8.0 |
|----------|-----|------------|------------|
| Стабильность | Высокий | 10/10 | ?/10 |
| Производительность рендеринга | Средний | базовая | ?/10 |
| Поддержка всех используемых фич | Критично | Да | ? |
| Отсутствие регрессий | Критично | Да | ? |
| Доступность в репозиториях | Средний | Да | Нет |
| Сложность обновления | Низкий | - | ? |

### 7. Возможные варианты решения

#### Вариант A: Остаться на 6.1 (статус-кво)
- **Плюсы**: Стабильность, доступность в Ubuntu 24.04, проверенность
- **Минусы**: Не получаем исправления багов и улучшения

#### Вариант B: Обновиться до 8.0 сразу
- **Плюсы**: Новейшие фичи, исправления
- **Минусы**: Может требовать сборки из source, риск регрессий

#### Вариант C: Использовать 7.1 (промежуточный)
- **Плюсы**: Более стабильная чем 8.0, новее 6.1
- **Минусы**: Может отсутствовать в репозиториях

#### Вариант D: Статическая сборка/Statis build
- **Плюсы**: Контроль версии независимо от дистрибутива
- **Минусы**: Увеличение размера образа, сложность обновления

---

## Ожидаемые результаты

### Краткое описание для tasklist.md

```markdown
| XX | Сравнение ffmpeg 8.0 vs 6.1 | ⏳ Не начата | 🔲 |

- [ ] **Итерация XX — Сравнение ffmpeg 8.0 vs 6.1**
  - Анализ изменений: amix filter, ass filter, многодорожечный муксинг
  - Тестирование производительности рендеринга
  - Проверка отсутствия регрессий в качестве аудио/видео
  - Рекомендация: обновление/сохранение текущей версии
  - Артефакты: `docs/ffmpeg-comparison.md`, обновление `Dockerfile.ubuntu24` (опционально)
```

### Детальные артефакты

1. **`docs/ffmpeg-comparison.md`** - полный отчёт о сравнении
2. **`Dockerfile.ubuntu24-ffmpeg80`** (опционально) - Dockerfile с ffmpeg 8.0
3. **`scripts/test_ffmpeg_performance.py`** - скрипт для тестирования производительности

---

## Ресурсы и ссылки

- [FFmpeg Releases](https://ffmpeg.org/releases/)
- [FFmpeg Changelog](https://ffmpeg.org/changelog.html)
- [FFmpeg Filters Documentation](https://ffmpeg.org/ffmpeg-filters.html)
- [Ubuntu Packages: ffmpeg](https://packages.ubuntu.com/search?keywords=ffmpeg)
- [Docker Hub: jrottenberg/ffmpeg](https://hub.docker.com/r/jrottenberg/ffmpeg/) - готовые образы с разными версиями

---

## Примечания

- FFmpeg 8.0 выпущен в апреле 2025 года
- Ubuntu 24.04 LTS (Noble) содержит ffmpeg 6.1.1
- Ubuntu 26.04 (выпуск апрель 2026) вероятно будет содержать ffmpeg 8.x
- Статические сборки доступны на https://johnvansickle.com/ffmpeg/
