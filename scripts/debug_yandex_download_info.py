"""Диагностический скрипт для исследования DownloadInfo Яндекс Музыки.

Зависимости: только yandex-music (уже в проекте).
Токен читается из .env в корне проекта (YANDEX_MUSIC_TOKEN).
Запуск: uv run python scripts/debug_yandex_download_info.py
"""
import os
import sys
from pathlib import Path

# Принудительно UTF-8 для stdout (Windows cmd может использовать cp1251)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Минимальная загрузка .env без сторонних библиотек
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.is_file():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _stripped = _line.strip()
        if _stripped and not _stripped.startswith("#") and "=" in _stripped:
            _key, _value = _stripped.split("=", 1)
            _key = _key.strip()
            if _key and _key not in os.environ:
                os.environ[_key] = _value.strip().strip('"').strip("'")

from yandex_music import Client  # noqa: E402

TRACK_ID = 77362003  # Полина Гагарина — Shallow (Live)


def safe_print(text: str) -> None:
    """Print text, replacing unencodable characters."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def main() -> None:
    token = os.getenv("YANDEX_MUSIC_TOKEN")
    if not token:
        safe_print("WARN: YANDEX_MUSIC_TOKEN not set — using anonymous access (limited)")

    client = Client(token)
    client.init()

    track = client.tracks(TRACK_ID)[0]
    safe_print(f"Track: {track.title!r}")
    artists_names = [a.name for a in (track.artists or [])]
    safe_print(f"Artists: {artists_names!r}")
    safe_print("")

    download_info = track.get_download_info()
    safe_print(f"Total download_info entries: {len(download_info)}")
    safe_print("")

    for i, fmt in enumerate(download_info):
        safe_print(f"--- Format #{i} ---")
        for attr in dir(fmt):
            if attr.startswith("_") or callable(getattr(fmt, attr, None)):
                continue
            try:
                val = getattr(fmt, attr)
                safe_print(f"  {attr}: {val!r}")
            except Exception as exc:
                safe_print(f"  {attr}: ERROR({exc})")
        safe_print("")


if __name__ == "__main__":
    main()
