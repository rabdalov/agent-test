# json_to_seg_word_srt.py
import json
import re
import sys
import os
from datetime import datetime, timedelta

def format_time(seconds):
    """Конвертирует секунды в формат ASS времени: H:MM:SS.XX"""
    td = timedelta(seconds=seconds)
    hours = td.seconds // 3600
    minutes = (td.seconds // 60) % 60
    seconds = td.seconds % 60 + td.microseconds / 1000000
    return f"{hours}:{minutes:02d}:{seconds:05.2f}"

def clean_text(text):
    """Удаляет знаки препинания и лишние пробелы для сравнения"""
    # Удаляем все знаки препинания и приводим к нижнему регистру
    return re.sub(r'[^\w\s]', '', text).lower().strip()

def find_word_position(segment_text, word_text, start_idx=0):
    """Находит позицию слова в тексте сегмента, игнорируя знаки препинания"""
    clean_segment = clean_text(segment_text)
    clean_word = clean_text(word_text)
    
    pos = clean_segment.find(clean_word, start_idx)
    if pos == -1:
        # Если не нашли, попробуем найти как часть слова
        words = clean_segment.split()
        for i, w in enumerate(words):
            if w.startswith(clean_word) or clean_word.startswith(w):
                # Восстанавливаем позицию в оригинальном тексте
                # Это приблизительная позиция
                return segment_text.lower().find(word_text.lower(), start_idx)
        return -1
    
    # Возвращаем позицию в оригинальном тексте
    # Это приблизительная позиция
    return segment_text.lower().find(word_text.lower(), start_idx)



def create_ass_subtitles_by_segments(json_file_path, output_ass_path, font_size=60):
    """
    Создает ASS субтитры с караоке-эффектом
    
    Args:
        json_file_path: путь к JSON файлу с транскрипцией
        output_ass_path: путь для сохранения ASS файла
        font_size: размер шрифта (по умолчанию 60)
    """
    # Загружаем JSON файл
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Получаем название трека из имени файла или из данных
    file_name = os.path.basename(json_file_path)
    track_name = os.path.splitext(file_name)[0].replace('_', ' ')
    
    # ASS заголовок с настраиваемым размером шрифта
    ass_content = f"""[Script Info]
Title: {track_name} (Karaoke)
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,  Arial,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,5,30,30,50,1
Style: Highlight,Arial,{font_size},&H00FF00FF,&H00FF00FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,5,30,30,50,1
Style: TextLine, Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,5,30,30,50,1
Style: Title,    Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,8,30,30,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    duration=data.get('duration',0)
    ass_content += f"Dialogue: 0,{format_time(0)},{format_time(duration)},Title,,0,0,0,,{track_name}\n"
     
    # Получаем сегменты и слова
    segments = data.get('segments', [])
    words = data.get('words', [])
    
    if not segments or not words:
        print("Не найдены сегменты или слова в JSON файле")
        return False
    
    # Группируем слова по сегментам
    word_idx = 0
    segment_words = []
    
    for segment in segments:
        segment_text = segment.get('text', '').strip()
        segment_start = segment.get('start', 0)
        segment_end = segment.get('end', 0)
        
        # Собираем слова для этого сегмента
        seg_words = []
        while word_idx < len(words) and words[word_idx]['start'] < segment_end:
            seg_words.append(words[word_idx])
            word_idx += 1
        
        if seg_words:
            segment_words.append({
                'start': segment_start,
                'end': segment_end,
                'text': segment_text,
                'words': seg_words
            })
    
    # Создаем события для каждого слова
    for seg in segment_words:
        seg_start = seg['start']
        seg_end = seg['end']
        seg_text = seg['text']
        seg_words = seg['words']
        
        # Добавляем всю строку текста (постоянно видна)
        ass_content += f"Dialogue: 0,{format_time(seg_start)},{format_time(seg_end)},TextLine,,0,0,0,,{seg_text}\n"
        
        # Добавляем подсветку для каждого слова
        for i, word in enumerate(seg_words):
            start_time = word['start']
            end_time = word['end']
            word_text = word['word'].strip()

            # Ищем слово в тексте сегмента
            highlighted_text = seg_text
            start_pos = 0
            
            # Для каждого слова ищем его позицию в сегменте
            for j, w in enumerate(seg_words):
                w_text = w['word'].strip()
                pos = find_word_position(seg_text, w_text, start_pos)
                
                if pos != -1:
                    # Если это текущее слово для подсветки
                    if j == i:
                        # Вставляем теги подсветки
                        before = highlighted_text[:pos]
                        after = highlighted_text[pos + len(w_text):]
                        highlighted_text = f"{before}{{\\rHighlight}}{w_text}{{\\rDefault}}{after}"
                    
                    # Обновляем стартовую позицию для поиска следующего слова
                    start_pos = pos + len(w_text)
                else:
                    # Если не нашли, просто пропускаем
                    pass
            
            # Добавляем событие с подсветкой
            ass_content += f"Dialogue: 1,{format_time(start_time)},{format_time(end_time)},Default,,0,0,0,,{highlighted_text}\n"

    # Сохраняем ASS файл
    with open(output_ass_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)
    
    print(f"ASS файл создан: {output_ass_path}")
    print(f"Размер шрифта: {font_size}")
    print(f"Сегментов: {len(segment_words)}")
    print(f"Всего слов: {len(words)}")
    print(f"Длительность: {data.get('duration', 0):.2f} секунд")
    return True

def main():
    # Проверяем аргументы командной строки
    if len(sys.argv) < 2:
        print("Использование: python3 json_to_seg_word_srt.py <json_file> [font_size]")
        print("Пример: python3 json_to_seg_word_srt.py \"1_Godsmack - Nothing Else Matters_(Vocals).json\"")
        print("Пример с размером шрифта: python3 json_to_seg_word_srt.py \"file.json\" 60")
        sys.exit(1)
    
    # Получаем путь к JSON файлу из аргументов
    json_file_path = sys.argv[1]
    
    # Проверяем существование файла
    if not os.path.exists(json_file_path):
        print(f"Ошибка: Файл '{json_file_path}' не найден")
        sys.exit(1)
    
    # Проверяем наличие аргумента для размера шрифта
    font_size = 48  # Увеличено с 36 до 48 по умолчанию
    if len(sys.argv) >= 3:
        try:
            font_size = int(sys.argv[2])
            if font_size < 10 or font_size > 100:
                print(f"Предупреждение: Размер шрифта {font_size} может быть слишком маленьким или большим")
        except ValueError:
            print(f"Предупреждение: '{sys.argv[2]}' не является числом, используется размер по умолчанию: {font_size}")
    
    # Создаем имя для ASS файла (то же имя, но с расширением .ass)
    base_name = os.path.splitext(json_file_path)[0]
    ass_file_path = base_name + ".ass"
    
    # Создаем ASS субтитры
    try:
        success = create_ass_subtitles_by_segments(json_file_path, ass_file_path, font_size)
        if success:
            print(f"\nГотово! ASS файл сохранен как: {ass_file_path}")
    except Exception as e:
        print(f"Ошибка при создании ASS файла: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
