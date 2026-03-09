"""Yandex Music downloader service for fetching tracks and lyrics."""

import logging
import re
from pathlib import Path
from typing import NamedTuple

import yandex_music
from yandex_music import Client

logger = logging.getLogger(__name__)


class YandexMusicTrackInfo(NamedTuple):
    """Information about a downloaded Yandex Music track."""

    local_path: Path
    track_stem: str
    track_id: int


class YandexMusicTrackMeta(NamedTuple):
    """Metadata about a Yandex Music track (without downloading)."""

    track_id: int
    title: str
    artist: str | None


class YandexMusicLyricsResult(NamedTuple):
    """Result of fetching lyrics from Yandex Music."""

    plain_text: str | None
    lrc_text: str | None


class YandexMusicDownloader:
    """Downloader for Yandex Music tracks and lyrics.

    Supports downloading tracks via URL and fetching lyrics/LRC data.
    """

    def __init__(self, token: str | None = None) -> None:
        """Initialize the downloader.

        :param token: Yandex Music authentication token (optional but recommended).
                     Can be obtained from yandex music web interface.
        """
        self._token = token
        self._client: Client | None = None

    def _get_client(self) -> Client:
        """Get or create Yandex Music client."""
        if self._client is None:
            if self._token:
                self._client = Client(self._token)
            else:
                # Try without token (limited access)
                self._client = Client()
        return self._client

    @staticmethod
    def parse_track_url(url: str) -> int | None:
        """Parse track ID from Yandex Music URL.

        Supports formats:
        - https://music.yandex.ru/album/12345/track/67890
        - https://music.yandex.ru/track/67890
        - //music.yandex.ru/album/12345/track/67890

        :param url: Yandex Music track URL
        :return: Track ID if found, None otherwise
        """
        # Pattern for /album/{album_id}/track/{track_id}
        album_track_pattern = r"/album/(\d+)/track/(\d+)"
        match = re.search(album_track_pattern, url)
        if match:
            return int(match.group(2))

        # Pattern for just /track/{track_id}
        track_pattern = r"/track/(\d+)"
        match = re.search(track_pattern, url)
        if match:
            return int(match.group(1))

        return None

    async def get_track_info(
        self,
        track_url: str,
    ) -> YandexMusicTrackMeta:
        """Get track metadata from Yandex Music URL without downloading.

        :param track_url: Yandex Music track URL
        :returns: YandexMusicTrackMeta with track ID, title, and artist
        :raises RuntimeError: If track cannot be found
        """
        track_id = self.parse_track_url(track_url)
        if track_id is None:
            raise RuntimeError(f"Не удалось извлечь ID трека из URL: {track_url}")

        logger.info(
            "YandexMusicDownloader: fetching track metadata for ID %d from URL: %s",
            track_id,
            track_url,
        )

        client = self._get_client()

        try:
            track = client.tracks(track_id)[0]
        except Exception as exc:
            logger.error(
                "YandexMusicDownloader: failed to fetch track metadata for %d: %s",
                track_id,
                exc,
            )
            raise RuntimeError(f"Не удалось получить информацию о треке с ID {track_id}: {exc}") from exc

        title = track.title
        artist = ", ".join(artist.name for artist in track.artists) if track.artists else None

        return YandexMusicTrackMeta(
            track_id=track_id,
            title=title,
            artist=artist,
        )

    async def download(
        self,
        track_url: str,
        output_dir: Path,
    ) -> YandexMusicTrackInfo:
        """Download a track from Yandex Music by URL.

        :param track_url: Yandex Music track URL
        :param output_dir: Directory to save the downloaded track
        :returns: YandexMusicTrackInfo with local path and track stem
        :raises RuntimeError: If track cannot be found or downloaded
        """
        track_id = self.parse_track_url(track_url)
        if track_id is None:
            raise RuntimeError(f"Не удалось извлечь ID трека из URL: {track_url}")

        logger.info(
            "YandexMusicDownloader: fetching track with ID %d from URL: %s",
            track_id,
            track_url,
        )

        client = self._get_client()

        # Fetch track by ID
        try:
            track = client.tracks(track_id)[0]
        except Exception as exc:
            logger.error(
                "YandexMusicDownloader: failed to fetch track %d: %s",
                track_id,
                exc,
            )
            raise RuntimeError(f"Не удалось получить трек с ID {track_id}: {exc}") from exc

        # Get track metadata
        title = track.title
        artists = ", ".join(artist.name for artist in track.artists) if track.artists else "Unknown"
        track_stem = f"{artists} - {title}".replace("/", "-").replace("\\", "-")
        # Remove invalid filesystem characters
        track_stem = re.sub(r'[<>:"*?|]', "", track_stem).strip()
        if not track_stem:
            track_stem = f"track_{track_id}"

        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{track_stem}.mp3"

        logger.info(
            "YandexMusicDownloader: downloading track '%s' (ID: %d) to '%s'",
            track_stem,
            track_id,
            output_path,
        )

        # Download track
        try:
            # Get download info
            download_info = track.get_download_info()
            if not download_info:
                raise RuntimeError(f"No download info available for track {track_id}")

            # Get the first available format (prefer mp3)
            best_format = None
            for fmt in download_info:
                if fmt.codec == "mp3":
                    best_format = fmt
                    break
            if best_format is None:
                best_format = download_info[0]

            # Download the file
            track.download(output_path, bitrate_in_kbps=best_format.bitrate_in_kbps)
            logger.info(
                "YandexMusicDownloader: track downloaded successfully to '%s'",
                output_path,
            )

        except Exception as exc:
            logger.error(
                "YandexMusicDownloader: failed to download track %d: %s",
                track_id,
                exc,
            )
            raise RuntimeError(f"Не удалось скачать трек: {exc}") from exc

        return YandexMusicTrackInfo(
            local_path=output_path,
            track_stem=track_stem,
            track_id=track_id,
        )

    async def fetch_lyrics(self, track_id: int) -> YandexMusicLyricsResult:
        """Fetch lyrics for a track from Yandex Music.

        :param track_id: Yandex Music track ID
        :returns: YandexMusicLyricsResult with plain text and/or LRC lyrics
        """
        logger.info(
            "YandexMusicDownloader: fetching lyrics for track ID %d",
            track_id,
        )

        client = self._get_client()

        try:
            # Fetch track first to check lyrics availability
            track = client.tracks(track_id)[0]

            # Check if lyrics are available via lyrics_info
            lyrics_info = getattr(track, 'lyrics_info', None)
            if not lyrics_info:
                logger.info(
                    "YandexMusicDownloader: no lyrics info for track ID %d",
                    track_id,
                )
                return YandexMusicLyricsResult(plain_text=None, lrc_text=None)

            # Use client.tracks_lyrics to get the TrackLyrics object with download URL
            track_lyrics = client.tracks_lyrics(track_id)

            if not track_lyrics:
                logger.info(
                    "YandexMusicDownloader: no lyrics available for track ID %d",
                    track_id,
                )
                return YandexMusicLyricsResult(plain_text=None, lrc_text=None)

            # TrackLyrics is not a list, it's a single object
            lyrics_obj = track_lyrics
            
            # Get the download URL from the lyrics object
            download_url = getattr(lyrics_obj, 'download_url', None)
            if not download_url:
                logger.info(
                    "YandexMusicDownloader: no download URL for track ID %d",
                    track_id,
                )
                return YandexMusicLyricsResult(plain_text=None, lrc_text=None)

            # Download the actual lyrics content
            import httpx
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(download_url)
                response.raise_for_status()
                # The content is UTF-8 encoded bytes
                lyrics_content = response.content.decode("utf-8")

            if not lyrics_content:
                logger.info(
                    "YandexMusicDownloader: empty lyrics content for track ID %d",
                    track_id,
                )
                return YandexMusicLyricsResult(plain_text=None, lrc_text=None)

            plain_text: str | None = None
            lrc_text: str | None = None

            # Check if lyrics contain timestamp markers like [00:00]
            if re.search(r"\[\d{2}:\d{2}\]", lyrics_content):
                lrc_text = lyrics_content
            else:
                plain_text = lyrics_content

            logger.info(
                "YandexMusicDownloader: fetched lyrics for track ID %d (plain: %s, lrc: %s)",
                track_id,
                "yes" if plain_text else "no",
                "yes" if lrc_text else "no",
            )

            return YandexMusicLyricsResult(plain_text=plain_text, lrc_text=lrc_text)

        except Exception as exc:
            logger.error(
                "YandexMusicDownloader: failed to fetch lyrics for track %d: %s",
                track_id,
                exc,
            )
            raise RuntimeError(f"Не удалось получить текст песни: {exc}") from exc
