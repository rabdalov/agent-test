import json
import logging
import os
from pathlib import Path
from typing import List

from pydantic import BaseModel, ValidationError


_SENSITIVE_NAME_PARTS = ("TOKEN", "KEY", "SECRET", "JWC")
_BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    telegram_bot_token: str
    admin_id: int
    tlg_allowed_id: List[int]
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        raw_tlg_allowed = os.getenv("TLG_ALLOWED_ID", "[]")
        try:
            tlg_allowed_parsed = json.loads(raw_tlg_allowed)
        except json.JSONDecodeError:
            tlg_allowed_parsed = []

        data = {
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "admin_id": int(os.getenv("ADMIN_ID", "0")),
            "tlg_allowed_id": tlg_allowed_parsed,
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
        }

        return cls(**data)


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


