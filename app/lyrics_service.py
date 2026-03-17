import asyncio
import logging
import re
from functools import partial
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_SEPARATORS = re.compile(r" — | - |_-_")


def _parse_artist_title(track_stem: str, track_file_name: str | None) -> tuple[str | None, str | None]:
    """Extract artist and title from track stem or file name.

    Supported separators: ' - ', ' — ', '_-_'.
    Returns (artist, title) or (None, None) if parsing fails.
    """
    for source in filter(None, [track_stem, track_file_name]):
        # Strip extension if it's the file name
        name = re.sub(r"\.[^.]+$", "", source)
        parts = _SEPARATORS.split(name, maxsplit=1)
        if len(parts) == 2:
            artist = parts[0].strip().replace("_", " ")
            title = parts[1].strip().replace("_", " ")
            if artist and title:
                return artist, title
    return None, None


class LyricsService:
    """Service for fetching song lyrics automatically via Genius API and LyricaV2."""

    def __init__(
        self,
        genius_token: str | None = None,
        enable_genius: bool = False,
        enable_lyrica: bool = False,
        enable_lyricslib: bool = False,
        lyrica_base_url: str = "http://localhost:5000",
    ) -> None:
        self._genius_token = genius_token
        self.enable_genius = enable_genius
        self.enable_lyrica = enable_lyrica
        self.enable_lyricslib = enable_lyricslib
        self._lyrica_base_url = lyrica_base_url.rstrip("/")

    async def find_lyrics(
        self,
        track_stem: str,
        track_file_name: str | None = None,
    ) -> str | None:
        """Attempt to find song lyrics automatically.

        Args:
            track_stem: Base filename without extension (e.g. "artist_-_song_title").
            track_file_name: Original filename with extension (optional).

        Returns:
            Lyrics text or None if not found.
        """
        any_enabled = self.enable_lyrica or (self.enable_genius and self._genius_token)
        if not any_enabled:
            logger.info("No lyrics providers enabled/configured — skipping auto lyrics search")
            return None

        artist, title = _parse_artist_title(track_stem, track_file_name)
        if not artist or not title:
            logger.info(
                "Could not parse artist/title from track_stem=%r, track_file_name=%r — "
                "skipping lyrics lookup",
                track_stem,
                track_file_name,
            )
            return None

        # Try Lyrica first (if enabled)
        if self.enable_lyrica:
            result = await self._search_lyrica(artist, title)
            if result:
                logger.info(f"Found lyrics via Lyrica for '{artist} - {title}'")
                return result

        # Try Genius API (if enabled)
        if self.enable_genius and self._genius_token:
            result = await self._search_genius_async(artist, title)
            if result:
                logger.info(f"Found lyrics via Genius for '{artist} - {title}'")
                return result

        return None

    async def _search_lyrica(self, artist: str, title: str) -> str | None:
        """Search lyrics using LyricaV2 HTTP API (https://github.com/Wilooper/LyricaV2)."""
        url = f"{self._lyrica_base_url}/lyrics/"
        params = {"artist": artist, "song": title, "timestamps": True}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                follow_redirects=True,
            ) as client:
                print(f"url:{url}")
                print(f"params:{params}")
                response = await client.get(url, params=params)
                print(f"response:{response}")

                response.raise_for_status()
                data: dict = response.json()
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"LyricaV2 returned HTTP {e.response.status_code} for '{artist} - {title}'"
            )
            return None
        except Exception as e:
            logger.warning(f"LyricaV2 search failed for '{artist} - {title}': {e}")
            return None

        if data.get("status") != "success":
            error_msg = data.get("error", {}).get("message", "unknown error")
            logger.debug(f"LyricaV2 no results for '{artist} - {title}': {error_msg}")
            return None

        lyrics: str = data.get("data", {}).get("lyrics", "") or ""
        lyrics = lyrics.strip()
        if len(lyrics) > 50:
            return lyrics
        return None

    async def _search_genius_async(self, artist: str, title: str) -> str | None:
        """Async wrapper around synchronous Genius API search."""
        try:
            return await asyncio.get_event_loop().run_in_executor(
                None,
                partial(self._search_genius, artist, title),
            )
        except Exception as exc:
            logger.warning("Genius search failed: %s", exc)
            return None

    def _search_genius(self, artist: str, title: str) -> str | None:
        """Synchronous Genius API search (intended to be called in executor)."""
        try:
            import lyricsgenius  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("lyricsgenius package is not installed")
            return None

        try:
            genius = lyricsgenius.Genius(
                self._genius_token,
                verbose=False,
                remove_section_headers=True,
                skip_non_songs=True,
                excluded_terms=["(Remix)", "(Live)"],
            )
            song = genius.search_song(title, artist)
        except Exception as exc:
            logger.warning("lyricsgenius raised an exception: %s", exc)
            return None

        if song is None:
            return None

        lyrics: str = song.lyrics or ""
        # lyricsgenius sometimes prepends e.g. "Artist Name\n" — strip it
        lines = lyrics.splitlines()
        if lines and lines[0].strip().lower().startswith(title.lower()[:10]):
            lines = lines[1:]
        return "\n".join(lines).strip() or None

    @staticmethod
    def generate_lyrics_from_transcription(transcription_json_path: Path) -> str:
        """Генерирует текст песни из segments транскрипции.

        Формат segments (после шага TRANSCRIBE и _cleanup_transcription):
        {
            "segments": [
                {"id": 0, "start": 0.0, "end": 2.5, "text": "текст строки"},
                ...
            ]
        }

        Args:
            transcription_json_path: Путь к JSON-файлу с транскрипцией.

        Returns:
            Строка с текстом песни, объединённые сегменты через перенос строки.
        """
        import json

        try:
            with open(transcription_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(f"Failed to read transcription file {transcription_json_path}: {exc}")
            return ""

        segments = data.get("segments", [])
        if not segments:
            logger.warning(f"No segments found in transcription file {transcription_json_path}")
            return ""

        lines = []
        for segment in segments:
            text = segment.get("text", "").strip()
            if text:
                lines.append(text)

        return "\n".join(lines)
