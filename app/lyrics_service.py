import asyncio
import logging
import re
from functools import partial

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
    """Service for fetching song lyrics automatically via Genius API."""

    def __init__(
        self,
        genius_token: str | None = None,
        enable_genius: bool = True,
        enable_lyrica: bool = False,
        enable_lyricslib: bool = False,
    ) -> None:
        self._genius_token = genius_token
        self.enable_genius = enable_genius
        self.enable_lyrica = enable_lyrica
        self.enable_lyricslib = enable_lyricslib

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
        """Search lyrics using Lyrica library."""

        def _sync_search() -> str | None:
            try:
                from lyrica import Song  # type: ignore[import-untyped]
                song = Song(artist, title)
                lyrics = song.lyrics
                if lyrics and len(lyrics.strip()) > 50:
                    return lyrics.strip()
                return None
            except Exception as e:
                logger.warning(f"Lyrica search failed for '{artist} - {title}': {e}")
                return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_search)

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
