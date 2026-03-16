from enum import Enum

from aiogram.fsm.state import State, StatesGroup
from pydantic import BaseModel


class User(BaseModel):
    user_id: int
    user_name: str | None = None


class UserRequest(BaseModel):
    user_id: int
    track_id: str
    source_type: str  # "file" | "url"
    source_url_or_file_path: str
    track_folder: str


class SourceType(str, Enum):
    """Тип источника трека для шага DOWNLOAD."""
    TELEGRAM_FILE = "telegram_file"   # Файл, загруженный через Telegram
    LOCAL_FILE = "local_file"         # Локальный файл (из поиска или файловой системы)
    HTTP_URL = "http_url"             # Произвольный HTTP(S) URL
    YANDEX_MUSIC = "yandex_music"     # Ссылка на Яндекс Музыку
    YOUTUBE = "youtube"               # Ссылка на YouTube


class PipelineStep(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    ASK_LANGUAGE = "ASK_LANGUAGE"
    GET_LYRICS = "GET_LYRICS"
    SEPARATE = "SEPARATE"
    TRANSCRIBE = "TRANSCRIBE"
    DETECT_CHORUS = "DETECT_CHORUS"
    CORRECT_TRANSCRIPT = "CORRECT_TRANSCRIPT"
    ALIGN = "ALIGN"
    MIX_AUDIO = "MIX_AUDIO"
    GENERATE_ASS = "GENERATE_ASS"
    RENDER_VIDEO = "RENDER_VIDEO"
    SEND_VIDEO = "SEND_VIDEO"


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"  # Ожидание ввода от пользователя (например, выбор языка)
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PipelineState(BaseModel):
    track_id: str
    user_id: int | None = None
    current_step: PipelineStep | None = None
    status: PipelineStatus = PipelineStatus.PENDING
    error_message: str | None = None
    # Тип источника трека (для унифицированного шага DOWNLOAD)
    source_type: SourceType | None = None
    # Исходный URL или путь к файлу (для шага DOWNLOAD)
    source_url: str | None = None
    # Telegram file_id (для source_type=TELEGRAM_FILE)
    telegram_file_id: str | None = None
    track_file_name: str | None = None
    track_source: str | None = None
    track_stem: str | None = None
    lang: str | None = None  # язык песни, выбранный пользователем ('ru' | 'en')
    vocal_file: str | None = None
    instrumental_file: str | None = None
    source_lyrics_file: str | None = None
    transcribe_json_file: str | None = None
    corrected_transcribe_json_file: str | None = None
    aligned_lyrics_file: str | None = None
    ass_file: str | None = None
    output_file: str | None = None
    download_url: str | None = None
    notification_chat_id: int | None = None  # ID чата для редактирования уведомлений
    notification_message_id: int | None = None  # ID сообщения для редактирования уведомлений
    # MIX_AUDIO step artifacts
    volume_segments_file: str | None = None   # JSON с разметкой громкости вокала по сегментам
    segment_groups_file: str | None = None    # JSON с группами сегментов (объединёнными по типу)
    processed_vocal_file: str | None = None   # Обработанная вокальная дорожка (с применённой громкостью)
    backvocal_mix_file: str | None = None     # MP3 микс: instrumental + processed_vocal
    supressedvocal_mix: str | None = None     # MP3 микс: instrumental + raw_vocal (с фиксированной громкостью)
    # Track visualization file (generated in GENERATE_ASS step when enabled)
    visualization_file: str | None = None


class PipelineResult(BaseModel):
    track_id: str
    status: PipelineStatus
    final_video_path: str | None = None
    error_message: str | None = None


class LyricsStates(StatesGroup):
    waiting_for_lyrics = State()


class TrackLangStates(StatesGroup):
    waiting_for_lang = State()


class SearchStates(StatesGroup):
    waiting_for_query = State()
    waiting_for_selection = State()
