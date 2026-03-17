import re


def normalize_filename(name: str) -> str:
    """
    Нормализует имя файла согласно правилам:
    - Содержит только русские или латинские буквы (оба регистра), цифры,
      знак дефиса, пробел и подчеркивание. Остальные символы удаляются.
    - Пробелы в начале и конце удаляются.
    - Повторяющиеся пробелы заменяются одиночными.
    - Если после очистки строка пуста, возвращается "track".
    """
    if not name:
        return "track"
    
    # Заменяем все пробельные символы (табуляция, перенос строки и т.д.) на обычный пробел
    # Используем регулярное выражение \s для нахождения любых пробельных символов
    name = re.sub(r'\s', ' ', name)
    
    # Оставляем только разрешённые символы: буквы (латиница и кириллица), цифры, дефис, пробел, подчёркивание
    # Используем Unicode-диапазоны для кириллицы и латиницы
    allowed = re.compile(r'[^a-zA-Zа-яА-ЯёЁ0-9\s\-_]')
    cleaned = allowed.sub('', name)
    
    # Удаляем пробелы в начале и конце
    cleaned = cleaned.strip()
    
    # Заменяем множественные пробелы на один
    cleaned = re.sub(r'\s+', ' ', cleaned)
    
    # Если после очистки строка пустая, возвращаем "track"
    if not cleaned:
        cleaned = "track"
    
    return cleaned


if __name__ == "__main__":
    # Простые тесты
    test_cases = [
        ("My Song.mp3", "My Song"),
        ("  Hello   World  ", "Hello World"),
        ("Привет-мир_2024!", "Привет-мир_2024"),
        ("@#$%^&*()", "track"),
        ("a   b   c", "a b c"),
        ("test -- double hyphen", "test  double hyphen"),  # дефис остаётся, двойной дефис не удаляется
        ("", "track"),
        ("   ", "track"),
        ("Track with spaces and	tab", "Track with spaces and tab"),
    ]
    for inp, expected in test_cases:
        out = normalize_filename(inp)
        print(f"'{inp}' -> '{out}' (expected '{expected}') {'OK' if out == expected else 'FAIL'}")