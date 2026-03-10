import json
import logging
import os
from pathlib import Path
from typing import List

from pydantic import BaseModel, Field, ValidationError

from app.models import User


_SENSITIVE_NAME_PARTS = ("TOKEN", "KEY", "SECRET", "JWC")
_BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    telegram_bot_token: str
    admin_id: int
    tlg_allowed_id: List[int]
    allowed_users: List[User] = Field(default_factory=list)
    denied_users: List[User] = Field(default_factory=list)
    log_level: str = "INFO"
    tracks_root_dir: Path
    demucs_model: str = "htdemucs"
    demucs_output_format: str = "mp3"
    # speeches.ai transcription
    speeches_base_url: str = "http://localhost:8000"
    transcription_model_id: str = "whisper-1"
    lang_default: str = "ru"
    prompt_speeches: str = ""
    speeches_timeout: int = 300
    # Genius API token for automatic lyrics search (optional)
    genius_token: str | None = None
    # Yandex Music token for downloading tracks and fetching lyrics (optional)
    yandex_music_token: str | None = None
    # Lyrics providers
    lyrics_enable_genius: bool = True   # Enable Genius API lyrics search
    lyrics_enable_lyrica: bool = False  # Enable LyricaV2 HTTP service lyrics search
    lyrics_enable_lyricslib: bool = False  # Enable lyrics-lib lyrics search
    # LyricaV2 service base URL (used when lyrics_enable_lyrica=true)
    lyrica_base_url: str = "http://localhost:5000"
    # ASS subtitle font size (pixels, used in GENERATE_ASS step)
    ass_font_size: int = 60
    # Video render settings (RENDER_VIDEO step)
    video_width: int = 1280
    video_height: int = 720
    video_background_color: str = "black"
    video_ffmpeg_preset: str = "fast"
    video_ffmpeg_crf: int = 22
    # Align timing correction settings (ALIGN step)
    # Maximum allowed duration (seconds) for the first word of a line;
    # if exceeded, a "(проигрыш)" gap marker is inserted before it.
    max_word_time: float = 5.0
    # Normal/expected duration (seconds) for the first word of a line;
    # used to compute the corrected start_time of the first real word.
    normal_word_time: float = 1.5
    # Send video to user after processing (RENDER_VIDEO step)
    # If true, video is sent to user via Telegram; if false, video is not sent but remains available locally
    send_video_to_user: bool = True
    # Audio mix voice volume for Instrumental+Voice track (RENDER_VIDEO step)
    # Volume level for voice track when mixing instrumental + voice (as decimal: 0.4 = 40%)
    audio_mix_voice_volume: float = 0.4
    # Content external URL for download links (RENDER_VIDEO step)
    # Base URL of the content server (e.g., content.homeserver.top)
    content_external_url: str | None = None
    # OpenRouter LLM settings (for CORRECT_TRANSCRIPT step)
    openrouter_api_key: str | None = None
    openrouter_model: str = "qwen/qwen3-next-80b-a3b-instruct:free"
    openrouter_api_url: str = "https://api.openrouter.ai/v1"
    # Enable/disable CORRECT_TRANSCRIPT step (uses LLM to correct transcription)
    correct_transcript_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        raw_tlg_allowed = os.getenv("TLG_ALLOWED_ID", "[]")
        try:
            tlg_allowed_parsed = json.loads(raw_tlg_allowed)
        except json.JSONDecodeError:
            tlg_allowed_parsed = []

        genius_token_raw = os.getenv("GENIUS_TOKEN", "") or None

        data = {
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "admin_id": int(os.getenv("ADMIN_ID", "0")),
            "tlg_allowed_id": tlg_allowed_parsed,
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "tracks_root_dir": os.getenv(
                "TRACKS_ROOT_DIR", str(_BASE_DIR / "tracks")
            ),
            "speeches_base_url": os.getenv("SPEECHES_BASE_URL", "http://localhost:8000"),
            "transcription_model_id": os.getenv("TRANSCRIPTION_MODEL_ID", "whisper-1"),
            "lang_default": os.getenv("LANG_DEFAULT", "ru"),
            "prompt_speeches": os.getenv("PROMPT_SPEECHES", ""),
            "speeches_timeout": int(os.getenv("SPEECHES_TIMEOUT", "300")),
            "genius_token": genius_token_raw,
            "yandex_music_token": os.getenv("YANDEX_MUSIC_TOKEN"),
            "lyrics_enable_genius": os.getenv("LYRICS_ENABLE_GENIUS", "true").lower() in ("true", "1", "yes"),
            "lyrics_enable_lyrica": os.getenv("LYRICS_ENABLE_LYRICA", "false").lower() in ("true", "1", "yes"),
            "lyrics_enable_lyricslib": os.getenv("LYRICS_ENABLE_LYRICSLIB", "false").lower() in ("true", "1", "yes"),
            "lyrica_base_url": os.getenv("LYRICA_BASE_URL", "http://localhost:5000"),
            "ass_font_size": int(os.getenv("ASS_FONT_SIZE", "60")),
            "video_width": int(os.getenv("VIDEO_WIDTH", "1280")),
            "video_height": int(os.getenv("VIDEO_HEIGHT", "720")),
            "video_background_color": os.getenv("VIDEO_BACKGROUND_COLOR", "black"),
            "video_ffmpeg_preset": os.getenv("VIDEO_FFMPEG_PRESET", "fast"),
            "video_ffmpeg_crf": int(os.getenv("VIDEO_FFMPEG_CRF", "22")),
            "max_word_time": float(os.getenv("MAX_WORD_TIME", "5.0")),
            "normal_word_time": float(os.getenv("NORMAL_WORD_TIME", "1.5")),
            "send_video_to_user": os.getenv("SEND_VIDEO_TO_USER", "true").lower() in ("true", "1", "yes"),
            "audio_mix_voice_volume": float(os.getenv("AUDIO_MIX_VOICE_VOLUME", "0.4")),
            "content_external_url": os.getenv("CONTENT_EXTERNAL_URL") or None,
            "openrouter_api_key": os.getenv("OPENROUTER_API_KEY") or None,
            "openrouter_model": os.getenv("OPENROUTER_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free"),
            "openrouter_api_url": os.getenv("OPENROUTER_API", "https://api.openrouter.ai/v1"),
            "correct_transcript_enabled": os.getenv("CORRECT_TRANSCRIPT_ENABLED", "true").lower() in ("true", "1", "yes"),
        }

        settings = cls(**data)
        settings._load_users()
        return settings

    def _get_users_file_path(self) -> Path:
        """Путь к файлу с пользователями."""
        return _BASE_DIR / "users.json"

    def _load_users(self) -> None:
        """Загрузка пользователей из JSON-файла."""
        users_file = self._get_users_file_path()
        if not users_file.is_file():
            return
        try:
            data = json.loads(users_file.read_text(encoding="utf-8"))
            allowed = data.get("allowed_users", [])
            denied = data.get("denied_users", [])
            self.allowed_users = [User(**u) for u in allowed]
            self.denied_users = [User(**u) for u in denied]
        except (json.JSONDecodeError, ValueError) as e:
            logging.warning(f"Failed to load users from {users_file}: {e}")

    def save_users(self) -> None:
        """Сохранение пользователей в JSON-файл."""
        users_file = self._get_users_file_path()
        data = {
            "allowed_users": [u.model_dump() for u in self.allowed_users],
            "denied_users": [u.model_dump() for u in self.denied_users],
        }
        users_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_allowed_user(self, user_id: int, user_name: str | None = None) -> None:
        """Добавить пользователя в список разрешённых."""
        self.denied_users = [u for u in self.denied_users if u.user_id != user_id]
        if not any(u.user_id == user_id for u in self.allowed_users):
            self.allowed_users.append(User(user_id=user_id, user_name=user_name))
        self.save_users()

    def add_denied_user(self, user_id: int, user_name: str | None = None) -> None:
        """Добавить пользователя в список отклонённых."""
        self.allowed_users = [u for u in self.allowed_users if u.user_id != user_id]
        if not any(u.user_id == user_id for u in self.denied_users):
            self.denied_users.append(User(user_id=user_id, user_name=user_name))
        self.save_users()

    def is_user_allowed(self, user_id: int) -> bool:
        """Проверить, разрешён ли пользователь."""
        return any(u.user_id == user_id for u in self.allowed_users)

    def is_user_denied(self, user_id: int) -> bool:
        """Проверить, отклонён ли пользователь."""
        return any(u.user_id == user_id for u in self.denied_users)


def setup_logging(log_level: str) -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _mask_value(name: str, value: object) -> object:
    upper_name = name.upper()
    if any(part in upper_name for part in _SENSITIVE_NAME_PARTS):
        text = str(value)
        if not text:
            return ""
        return text[:4] + "****"
    return value


def settings_for_logging(settings: Settings) -> dict[str, object]:
    data: dict[str, object] = {}
    for field_name in settings.model_fields:
        value = getattr(settings, field_name)
        data[field_name] = _mask_value(field_name, value)
    return data


def _load_dotenv_if_present() -> None:
    """
    Простая загрузка .env из корня проекта, если uv не подхватил файл сам.
    Не переопределяет уже существующие переменные окружения.
    """
    env_path = _BASE_DIR / ".env"
    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        # Убираем обрамляющие кавычки, если они есть
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def load_settings() -> Settings:
    _load_dotenv_if_present()
    try:
        return Settings.from_env()
    except (ValidationError, ValueError) as exc:
        raise RuntimeError(f"Failed to load settings from environment: {exc}") from exc


