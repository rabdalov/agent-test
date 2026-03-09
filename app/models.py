from enum import Enum

from aiogram.fsm.state import State, StatesGroup
from pydantic import BaseModel


class UserRequest(BaseModel):
    user_id: int
    track_id: str
    source_type: str  # "file" | "url"
    source_url_or_file_path: str
    track_folder: str


class PipelineStep(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    GET_LYRICS = "GET_LYRICS"
    SEPARATE = "SEPARATE"
    TRANSCRIBE = "TRANSCRIBE"
    CORRECT_TRANSCRIPT = "CORRECT_TRANSCRIPT"
    ALIGN = "ALIGN"
    GENERATE_ASS = "GENERATE_ASS"
    RENDER_VIDEO = "RENDER_VIDEO"


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PipelineState(BaseModel):
    track_id: str
    user_id: int | None = None
    current_step: PipelineStep | None = None
    status: PipelineStatus = PipelineStatus.PENDING
    error_message: str | None = None
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


class PipelineResult(BaseModel):
    track_id: str
    status: PipelineStatus
    final_video_path: str | None = None
    error_message: str | None = None


class LyricsStates(StatesGroup):
    waiting_for_lyrics = State()


class TrackLangStates(StatesGroup):
    waiting_for_lang = State()
