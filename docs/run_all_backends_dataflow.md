# Поток данных метода run_all_backends

```mermaid
flowchart TD
    Start([Начало: run_all_backends]) --> Init1[Инициализация all_results]
    Init1 --> Init2["all_results: dict[str, dict[str, list[tuple[float, float]]]]"]
    
    Init2 --> LoopBackend{Цикл по BACKENDS}
    LoopBackend --> BackendItem["backend: str"]
    
    BackendItem --> PrintBackend["Печать: БЭКЕНД: {backend}"]
    PrintBackend --> CreateDetector["Создание ChorusDetector с backend, min_duration_sec=15.0, max_duration_sec=60.0"]
    
    CreateDetector --> LoopFiles{Цикл по test_files}
    LoopFiles --> FileItem["label: str, path: Path"]
    
    FileItem --> PrintAnalyze["Печать: Анализирую {label} {path.name} [backend={backend}]"]
    
    PrintAnalyze --> CallDetect["detector.detect_with_info(str(path))"]
    CallDetect --> SegmentInfos["segment_infos: list[SegmentInfo]"]
    
    SegmentInfos --> PrintSegInfo["print_segments_with_info(f'{label} [{backend}]', segment_infos)"]
    
    PrintSegInfo --> FilterChorus["Фильтрация chorus-сегментов<br/>[s.start, s.end] for s in segment_infos<br/>where s.segment_type == 'chorus'"]
    FilterChorus --> ChorusSegs["chorus_segs: list[tuple[float, float]]"]
    
    ChorusSegs --> StoreResults["Сохранение в all_results[label][backend]"]
    StoreResults --> CheckException{Произошло исключение?}
    
    CheckException --> |Да| PrintError["Печать ошибки, all_results[label][backend] = []"]
    CheckException --> |Нет| NextFile{Следующий файл?}
    
    PrintError --> NextFile
    NextFile --> |Да| FileItem
    NextFile --> |Нет| NextBackend{Следующий бэкенд?}
    
    NextBackend --> |Да| BackendItem
    NextBackend --> |Нет| CompareResults["Цикл сравнения бэкендов для каждого файла"]
    
    CompareResults --> LoopLabels{Цикл по test_files}
    LoopLabels --> LabelItem["label из test_files"]
    
    LabelItem --> CallCompare["compare_backends(all_results[label], label)"]
    CallCompare --> NextLabel{Следующая метка?}
    
    NextLabel --> |Да| LabelItem
    NextLabel --> |Нет| PrintSummary["Печать итоговой сводки"]
    
    PrintSummary --> PrintHeader["Печать заголовка таблицы"]
    PrintHeader --> LoopSummary{Цикл по test_files для сводки}
    
    LoopSummary --> SummaryItem["label из test_files"]
    SummaryItem --> GetCounts["Получение количества сегментов для каждого бэкенда"]
    GetCounts --> PrintRow["Печать строки таблицы"]
    
    PrintRow --> NextSummary{Следующая строка?}
    NextSummary --> |Да| SummaryItem
    NextSummary --> |Нет| End([Конец])
    
    %% Data flow annotations
    subgraph "Входные данные"
        A["test_files: dict[str, Path]"]
        B["BACKENDS: list[str]"]
    end
    
    subgraph "Промежуточные данные"
        C["detector: ChorusDetector"]
        D["segment_infos: list[SegmentInfo]"]
        E["chorus_segs: list[tuple[float, float]]"]
        F["all_results: dict[str, dict[str, list[tuple[float, float]]]]"]
    end
    
    subgraph "Выходные данные"
        G["Сравнительные результаты"]
        H["Итоговая сводка"]
    end
    
    A -.-> Init2
    B -.-> LoopBackend
    C -.-> CallDetect
    D -.-> FilterChorus
    E -.-> StoreResults
    F -.-> CompareResults
    G <-.-> CallCompare
    H <-.-> PrintSummary

    style Start fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style End fill:#ffcdd2,stroke:#c62828,stroke-width:2px
    style LoopBackend fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style LoopFiles fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style CompareResults fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px