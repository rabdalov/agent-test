"""YouTube downloader service for fetching audio tracks from YouTube."""

import asyncio
import logging
import re
from pathlib import Path
from typing import NamedTuple

import yt_dlp

from .utils import normalize_filename

logger = logging.getLogger(__name__)


class YouTubeTrackInfo(NamedTuple):
    """Information about a downloaded YouTube track."""

    local_path: Path
    track_stem: str
    video_id: str


class YouTubeTrackMeta(NamedTuple):
    """Metadata about a YouTube video (without downloading)."""

    video_id: str
    title: str
    artist: str | None
    duration: int  # seconds


class YouTubeDownloader:
    """Downloader for YouTube audio tracks.

    Supports downloading audio via URL and extracting metadata.
    """

    def __init__(self, quality: str = "best") -> None:
        """Initialize the downloader.

        :param quality: Audio quality preference (e.g., "best", "worst", "192").
        """
        self.quality = quality

    @staticmethod
    def parse_video_id(url: str) -> str | None:
        """Parse video ID from YouTube URL.

        Supports formats:
        - https://www.youtube.com/watch?v=VIDEO_ID
        - https://youtu.be/VIDEO_ID
        - https://www.youtube.com/embed/VIDEO_ID
        - https://www.youtube.com/v/VIDEO_ID
        - https://www.youtube.com/shorts/VIDEO_ID

        :param url: YouTube video URL
        :return: Video ID if found, None otherwise
        """
        patterns = [
            r"(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/|youtube\.com\/v\/|youtube\.com\/shorts\/)([a-zA-Z0-9_-]{11})",
            r"youtube\.com\/watch\?.*v=([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    async def get_track_info(self, url: str) -> YouTubeTrackMeta:
        """Get video metadata from YouTube URL without downloading.

        :param url: YouTube video URL
        :returns: YouTubeTrackMeta with video ID, title, artist, duration
        :raises RuntimeError: If video cannot be found or metadata extraction fails
        """
        video_id = self.parse_video_id(url)
        if video_id is None:
            raise RuntimeError(f"Не удалось извлечь ID видео из URL: {url}")

        logger.info(
            "YouTubeDownloader: fetching video metadata for ID %s from URL: %s",
            video_id,
            url,
        )

        # Конфигурация yt-dlp для извлечения только метаданных
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,  # получаем полную информацию
            "socket_timeout": 30,
            "connect_timeout": 10,
        }

        try:
            # Запускаем синхронный вызов yt-dlp в отдельном потоке
            def extract_info() -> dict:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)

            info = await asyncio.to_thread(extract_info)
        except yt_dlp.utils.DownloadError as e:
            logger.error(
                "YouTubeDownloader: failed to extract metadata for URL %s: %s",
                url,
                str(e),
            )
            raise RuntimeError(f"Не удалось получить метаданные видео: {e}")
        except Exception as e:
            logger.error(
                "YouTubeDownloader: unexpected error while extracting metadata: %s",
                str(e),
            )
            raise RuntimeError(f"Ошибка при извлечении метаданных: {e}")

        if info is None:
            raise RuntimeError("Не удалось получить информацию о видео (ответ пуст)")

        # Извлекаем необходимые поля
        title = info.get("title") or "Без названия"
        # Автор (канал)
        artist = info.get("uploader") or info.get("channel") or None
        duration = int(info.get("duration") or 0)
        # Проверяем, что видео доступно
        availability = info.get("availability")
        if availability == "private" or availability == "subscriber_only":
            raise RuntimeError("Видео приватное или доступно только подписчикам")
        if duration <= 0:
            raise RuntimeError("Длительность видео не определена или равна нулю")
        # Убедимся, что video_id соответствует извлечённому
        extracted_id = info.get("id")
        if extracted_id and extracted_id != video_id:
            logger.warning(
                "YouTubeDownloader: parsed video ID %s differs from extracted %s",
                video_id,
                extracted_id,
            )
            video_id = extracted_id

        logger.info(
            "YouTubeDownloader: retrieved metadata for '%s' (artist: %s, duration: %d s)",
            title,
            artist,
            duration,
        )

        return YouTubeTrackMeta(
            video_id=video_id,
            title=title,
            artist=artist,
            duration=duration,
        )

    async def download(self, url: str, output_dir: Path) -> YouTubeTrackInfo:
        """Download audio from YouTube video by URL.

        :param url: YouTube video URL
        :param output_dir: Directory to save the downloaded audio
        :returns: YouTubeTrackInfo with local path and track stem
        :raises RuntimeError: If video cannot be found or download fails
        """
        video_id = self.parse_video_id(url)
        if video_id is None:
            raise RuntimeError(f"Не удалось извлечь ID видео из URL: {url}")

        logger.info(
            "YouTubeDownloader: downloading audio for video ID %s from URL: %s",
            video_id,
            url,
        )

        # Создаём директорию, если не существует
        output_dir.mkdir(parents=True, exist_ok=True)

        # Определяем формат аудио на основе качества
        # quality может быть "best", "worst" или числовым значением битрейта (например "192")
        if self.quality == "best":
            format_spec = "bestaudio"
        elif self.quality == "worst":
            format_spec = "worstaudio"
        else:
            # Пытаемся интерпретировать как битрейт для mp3
            # yt-dlp поддерживает фильтры по битрейту, но проще скачать bestaudio и потом конвертировать
            # Для простоты используем bestaudio, а битрейт зададим в постпроцессоре
            format_spec = "bestaudio"

        # Конфигурация yt-dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": format_spec,
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": self.quality if self.quality.isdigit() else "192",
                }
            ],
            "progress_hooks": [self._progress_hook],
            "socket_timeout": 60,
            "connect_timeout": 30,
        }

        try:
            def download_audio() -> tuple[Path, str, dict]:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # extract_info скачивает аудио и возвращает метаданные
                    info = ydl.extract_info(url, download=True)
                    # После скачивания файл будет находиться в output_dir с расширением .mp3
                    # (постпроцессор меняет расширение)
                    title = info.get("title", "video")
                    # Нормализуем имя файла
                    safe_title = normalize_filename(title)
                    # Ищем файл с таким именем в output_dir
                    # yt-dlp может добавить суффиксы, но outtmpl использует оригинальное название
                    # Поищем файл с расширением .mp3, который содержит safe_title
                    for f in output_dir.iterdir():
                        if f.suffix.lower() == ".mp3" and safe_title in f.stem:
                            local_path = f
                            break
                    else:
                        # Если не нашли, используем первый .mp3 файл
                        mp3_files = list(output_dir.glob("*.mp3"))
                        if not mp3_files:
                            raise RuntimeError("Скачанный аудиофайл не найден")
                        local_path = mp3_files[0]
                    return local_path, safe_title, info

            local_path, safe_title, info = await asyncio.to_thread(download_audio)
        except yt_dlp.utils.DownloadError as e:
            logger.error(
                "YouTubeDownloader: failed to download audio for URL %s: %s",
                url,
                str(e),
            )
            raise RuntimeError(f"Не удалось скачать аудио: {e}")
        except Exception as e:
            logger.error(
                "YouTubeDownloader: unexpected error while downloading: %s",
                str(e),
            )
            raise RuntimeError(f"Ошибка при скачивании аудио: {e}")

        # Use normalized title as track_stem for consistency with directory naming
        track_stem = safe_title
        # Убедимся, что video_id соответствует извлечённому
        extracted_id = info.get("id")
        if extracted_id and extracted_id != video_id:
            logger.warning(
                "YouTubeDownloader: parsed video ID %s differs from extracted %s",
                video_id,
                extracted_id,
            )
            video_id = extracted_id

        logger.info(
            "YouTubeDownloader: successfully downloaded audio to '%s'",
            local_path,
        )

        return YouTubeTrackInfo(
            local_path=local_path,
            track_stem=track_stem,
            video_id=video_id,
        )

    def _progress_hook(self, d: dict) -> None:
        """Хук для отслеживания прогресса скачивания (можно расширить)."""
        if d.get("status") == "downloading":
            percent = d.get("_percent_str", "?")
            speed = d.get("_speed_str", "?")
            logger.debug(
                "YouTubeDownloader: downloading %s at %s",
                percent,
                speed,
            )
        elif d.get("status") == "finished":
            logger.debug("YouTubeDownloader: download finished, post-processing...")