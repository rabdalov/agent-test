# Зависимости метода run_all_backends

```mermaid
graph TD
    A[run_all_backends] --> B[ChorusDetector]
    A --> C[test_files]
    A --> D[BACKENDS]
    
    B --> E[app.chorus_detector.SegmentInfo]
    B --> F[backend parameter]
    B --> G[min_duration_sec]
    B --> H[max_duration_sec]
    
    C --> I["dict[str, Path]"]
    C --> J[audio file paths]
    
    D --> K["['msaf', 'librosa', 'hybrid']"]
    
    A --> M[detect_with_info]
    A --> N[print_segments_with_info]
    A --> O[compare_backends]
    A --> P[format_time]
    
    M --> B
    N --> Q[SegmentInfo objects]
    O --> R[backend_results]
    R --> S["dict[str, dict[str, list[tuple[float, float]]]]"]
    
    A --> T[all_results]
    T --> U["dict[str, dict[str, list[tuple[float, float]]]]"]
    
    A --> V[chorus_segs extraction]
    V --> W[isinstance checks]
    V --> X[SegmentInfo filtering]
    
    style A fill:#e1f5fe,stroke:#01579b,stroke-width:3px
    style B fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    style M fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px
    style N fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px
    style O fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px
```

## Описание зависимостей

### Внешние зависимости:
- `ChorusDetector` из `app.chorus_detector` - основной класс для детектирования припевов
- `SegmentInfo` из `app.chorus_detector` - класс для хранения информации о сегментах
- `BACKENDS` - глобальная константа, определяющая доступные бэкенды

### Входные параметры:
- `test_files: dict[str, Path]` - словарь с метками и путями к аудиофайлам

### Внутренние зависимости:
- `detect_with_info()` - метод детектора для получения информации о сегментах
- `print_segments_with_info()` - функция для вывода информации о сегментах
- `compare_backends()` - функция для сравнения результатов разных бэкендов
- `format_time()` - вспомогательная функция для форматирования времени
- `all_results` - внутренняя структура данных для хранения результатов
- `chorus_segs` - логика фильтрации сегментов по типу (chorus/non-chorus)

### Обработка:
- Для каждого бэкенда создается экземпляр `ChorusDetector`
- Для каждого файла вызывается `detect_with_info()` 
- Результаты фильтруются для получения только chorus-сегментов
- Результаты сохраняются во внутреннюю структуру `all_results`
- Вызывается `compare_backends()` для сравнения результатов