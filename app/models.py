from pydantic import BaseModel


class UserRequest(BaseModel):
    user_id: int
    track_id: str
    source_type: str  # "url" | "file"
    source_url_or_file_path: str
