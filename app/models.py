from enum import Enum

from pydantic import BaseModel


class UserRequest(BaseModel):
    user_id: int
    track_id: str
    source_type: str  # "file" | "url"
    source_url_or_file_path: str
    track_folder: str


class PipelineStep(str, Enum):
    DOWNLOAD = "DOWNLOAD"
    SEPARATE = "SEPARATE"
    TRANSCRIBE = "TRANSCRIBE"
    GET_LYRICS = "GET_LYRICS"
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
    current_step: PipelineStep | None = None
    status: PipelineStatus = PipelineStatus.PENDING
    error_message: str | None = None


class PipelineResult(BaseModel):
    track_id: str
    status: PipelineStatus
    final_video_path: str | None = None
    error_message: str | None = None
