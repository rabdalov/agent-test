import asyncio
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from aiogram import F, Router, types
from aiogram.filters import CommandStart

from .config import Settings
from .models import UserRequest


class KaraokeHandlers:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tracks_root_dir: Path = settings.tracks_root_dir
        self._logger = logging.getLogger(__name__)
        self.router = Router()
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def handle_start(message: types.Message) -> None:  # type: ignore[unused-ignore]
            await message.answer(
                "Привет! Я бот для подготовки караоке-видео.\n"
                "Отправьте мне аудиофайл (музыкальную композицию длительностью более 1 минуты), "
                "и я подготовлю данные для караоке-пайплайна."
            )

        @self.router.message(F.audio)
        async def handle_audio(message: types.Message) -> None:  # type: ignore[unused-ignore]
            audio = message.audio
            if audio is None:
                return

            self._ensure_tracks_root()

            tmp_dir = self._tracks_root_dir / "_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)

            original_name = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
            tmp_path = tmp_dir / original_name

            await message.bot.download(audio, destination=tmp_path)
            #artist=""
            #title=""
            #duration=120
            duration, artist, title = await self._probe_audio(tmp_path)

            #track_name = self._build_track_name(original_name, artist, title)
            track_name = self._build_track_name(original_name or "track", None, None)

            if duration is None or duration < 60:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        self._logger.warning("Failed to remove temporary file %s", tmp_path)

                await message.answer(
                    f'Полученный файл "{track_name}" не является музыкальной композицией '
                    "(длительность менее 1 минуты или не удалось определить длительность)."
                )
                return

            track_id = uuid.uuid4().hex
            track_dir = self._tracks_root_dir / track_name
            track_dir.mkdir(parents=True, exist_ok=True)

            final_path = track_dir / f"{track_name}.mp3"

            try:
                shutil.move(str(tmp_path), final_path)
            except OSError as exc:
                self._logger.error("Failed to move file %s to %s: %s", tmp_path, final_path, exc)
                await message.answer(
                    "Не удалось сохранить аудиофайл. Пожалуйста, попробуйте отправить его ещё раз позже."
                )
                return

            await message.answer(
                "Аудиофайл принят.\n"
                f"track_id: <code>{track_id}</code>\n"
                f'track_name: <code>{track_name}</code>\n'
                f'Путь к файлу: <code>{final_path}</code>'
            )

        @self.router.message(F.text)
        async def handle_text(message: types.Message) -> None:  # type: ignore[unused-ignore]
            text = (message.text or "").strip()
            url = self._extract_url(text)
            if url is None:
                await message.answer(
                    "Полученное сообщение не является музыкальной композицией. "
                    "Пожалуйста, отправьте аудиофайл длительностью более 1 минуты "
                    "или ссылку на трек."
                )
                return

            if self._is_blocked_url(url):
                await message.answer(
                    "Ссылки на Яндекс Музыку и YouTube пока не поддерживаются. "
                    "Поддержка этих источников появится в будущих версиях бота."
                )
                return

            self._ensure_tracks_root()

            track_id = uuid.uuid4().hex
            parsed_url_for_name = urlparse(url)
            url_basename = unquote(parsed_url_for_name.path.rstrip("/").split("/")[-1]) if parsed_url_for_name.path.rstrip("/") else ""
            track_name = self._build_track_name(url_basename or "track", None, None)
            track_dir = self._tracks_root_dir / track_name
            track_dir.mkdir(parents=True, exist_ok=True)

            user_id: int = message.from_user.id if message.from_user else 0
            request = UserRequest(
                user_id=user_id,
                track_id=track_id,
                source_type="url",
                source_url_or_file_path=url,
            )

            # Fix 1: save state.json at <tracks_root_dir> / <track_name> / state.json
            state_path = track_dir / "state.json"
            try:
                state_path.write_text(
                    request.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self._logger.error(
                    "Failed to write state file for track %s: %s", track_id, exc
                )
                await message.answer(
                    "Не удалось сохранить информацию о треке. "
                    "Пожалуйста, попробуйте ещё раз позже."
                )
                return

            # Fix 3: download the file by HTTP URL and save locally
            filename = url_basename if url_basename else "source_file"
            local_path = track_dir / filename

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with local_path.open("wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
            except httpx.HTTPError as exc:
                self._logger.error(
                    "Failed to download file for track %s from %s: %s", track_id, url, exc
                )
                await message.answer(
                    "Не удалось скачать файл по указанной ссылке. "
                    "Пожалуйста, проверьте ссылку и попробуйте ещё раз."
                )
                return
            except OSError as exc:
                self._logger.error(
                    "Failed to save downloaded file for track %s: %s", track_id, exc
                )
                await message.answer(
                    "Не удалось сохранить скачанный файл. "
                    "Пожалуйста, попробуйте ещё раз позже."
                )
                return

            # Update request with local file path and re-save state.json
            request = UserRequest(
                user_id=user_id,
                track_id=track_id,
                source_type="url",
                source_url_or_file_path=str(local_path),
            )
            try:
                state_path.write_text(
                    request.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self._logger.error(
                    "Failed to update state file for track %s: %s", track_id, exc
                )

            await message.answer(
                "Файл скачан и принят.\n"
                f"track_id: <code>{track_id}</code>\n"
                f"track_name: <code>{track_name}</code>\n"
                f"Путь к файлу: <code>{local_path}</code>",
                parse_mode="HTML",
            )

        @self.router.message()
        async def handle_non_audio(message: types.Message) -> None:  # type: ignore[unused-ignore]
            await message.answer(
                "Полученное сообщение не является музыкальной композицией. "
                "Пожалуйста, отправьте аудиофайл длительностью более 1 минуты."
            )

    def _ensure_tracks_root(self) -> None:
        self._tracks_root_dir.mkdir(parents=True, exist_ok=True)

    async def _probe_audio(self, path: Path) -> tuple[float | None, str | None, str | None]:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            self._logger.error("Failed to start ffprobe for %s: %s", path, exc)
            return None, None, None

        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            self._logger.warning(
                "ffprobe returned non-zero exit code for %s: %s", path, stderr.decode("utf-8", "ignore")
            )
            return None, None, None

        try:
            payload: dict[str, Any] = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._logger.warning("Failed to parse ffprobe output for %s: %s", path, exc)
            return None, None, None

        fmt = payload.get("format") or {}
        duration_raw = fmt.get("duration")
        duration: float | None
        if duration_raw is None:
            duration = None
        else:
            try:
                duration = float(duration_raw)
            except (TypeError, ValueError):
                duration = None

        tags = fmt.get("tags") or {}
        artist = tags.get("artist") or tags.get("ARTIST")
        title = tags.get("title") or tags.get("TITLE")

        return duration, artist, title

    def _build_track_name(
        self,
        original_filename: str,
        artist: str | None,
        title: str | None,
    ) -> str:
        if artist or title:
            parts = [part for part in [artist, title] if part]
            base = "-".join(parts)
        else:
            base = Path(original_filename).stem

        base = base.strip()
        if not base:
            base = "track"

        #normalized = re.sub(r"[^\w\-]+", "_", base)
        #normalized = normalized.strip("_")
        normalized = re.sub(r"[^\w\s\-]+", "", base)  # Удаляем спецсимволы, кроме букв/цифр/пробелов/дефиса
        normalized = re.sub(r"\s+", " ", normalized)  # Сжимаем множественные пробелы в один
        if not normalized:
            normalized = base

        return normalized

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    _URL_PATTERN: re.Pattern[str] = re.compile(
        r"https?://[^\s]+",
        re.IGNORECASE,
    )

    _BLOCKED_HOSTS: tuple[str, ...] = (
        "music.yandex.ru",
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "m.youtube.com",
    )

    def _extract_url(self, text: str) -> str | None:
        """Return the first HTTP(S) URL found in *text*, or None.

        Fix 2: if the message starts with http(s)://, treat the entire stripped
        text as the URL (replacing spaces with %20 so filenames with spaces are
        preserved).  Otherwise, find the first occurrence of http and take
        everything from that position to the end of the string (again encoding
        spaces as %20).
        """
        stripped = text.strip()
        if re.match(r"https?://", stripped, re.IGNORECASE):
            # Entire message is the URL candidate
            return stripped.replace(" ", "%20")
        match = re.search(r"https?://", stripped, re.IGNORECASE)
        if match:
            return stripped[match.start():].replace(" ", "%20")
        return None

    def _is_blocked_url(self, url: str) -> bool:
        """Return True if *url* points to Yandex Music or YouTube."""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except ValueError:
            return False
        return any(host == blocked or host.endswith("." + blocked) for blocked in self._BLOCKED_HOSTS)


