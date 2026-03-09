import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoRenderError(Exception):
    """Raised when ffmpeg video rendering fails."""


class VideoRenderer:
    """Renders a karaoke MP4 video with multiple audio tracks and an ASS subtitle file.

    The renderer generates a static-colour background video stream, burns the ASS
    subtitles into it via the ``ass`` filter, and muxes the result with multiple
    audio tracks (Instrumental, Original, etc.).

    Example equivalent shell command (2 audio tracks)::

        ffmpeg -f lavfi -i "color=c=black:s=1280x720:r=25" -i instrumental.mp3 -i original.mp3 \\
            -filter_complex "[0:v]ass='subtitles.ass'[vout]" \\
            -map "[vout]" -map "1:a" -map "2:a" \\
            -c:v libx264 -preset fast -tune stillimage -crf 22 \\
            -c:a aac -b:a 320k -shortest -pix_fmt yuv420p \\
            -metadata:s:a:0 title="Instrumental" -metadata:s:a:1 title="Original" \\
            -disposition:a:0 default -disposition:a:1 0 \\
            output.mp4

    Instead of a background image we use ``lavfi`` colour source so no external
    image file is needed.
    """

    def __init__(
        self,
        *,
        width: int = 1280,
        height: int = 720,
        background_color: str = "black",
        ffmpeg_preset: str = "fast",
        ffmpeg_crf: int = 22,
        audio_bitrate: str = "320k",
    ) -> None:
        self._width = width
        self._height = height
        self._background_color = background_color
        self._ffmpeg_preset = ffmpeg_preset
        self._ffmpeg_crf = ffmpeg_crf
        self._audio_bitrate = audio_bitrate

    async def render(
        self,
        *,
        instrumental_path: Path,
        original_path: Path,
        ass_path: Path,
        output_path: Path,
    ) -> Path:
        """Render the karaoke video with multiple audio tracks.

        Parameters
        ----------
        instrumental_path:
            Path to the instrumental audio file (vocals removed).
        original_path:
            Path to the original audio file (full mix with vocals).
        ass_path:
            Path to the ``.ass`` subtitle file with karaoke highlights.
        output_path:
            Destination path for the resulting ``.mp4`` file.

        Returns
        -------
        Path
            The path to the rendered output file (same as *output_path*).

        Raises
        ------
        VideoRenderError
            If ``ffmpeg`` exits with a non-zero return code or cannot be started.
        """
        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build ASS path string safe for ffmpeg filter graph.
        # ffmpeg `ass` filter requires forward slashes and escaped colons.
        # On Windows: "C:\path\file.ass" → "C\\:/path/file.ass"
        ass_path_resolved = str(ass_path.resolve())
        # 1. Normalise to forward slashes
        ass_for_filter = ass_path_resolved.replace("\\", "/")
        # 2. Escape colon in drive letter (Windows: "C:/..." → "C\\:/...")
        if len(ass_for_filter) >= 2 and ass_for_filter[1] == ":":
            ass_for_filter = ass_for_filter[0] + "\\:" + ass_for_filter[2:]

        # The lavfi input is [0:v]; instrumental audio is [1:a]; original audio is [2:a].
        # We apply the `ass` filter directly to [0:v].
        filter_complex = f"[0:v]ass='{ass_for_filter}'[vout]"

        cmd: list[str] = [
            "ffmpeg",
            "-y",                           # overwrite output without asking
            # Video: synthetic lavfi colour background → input [0:v]
            "-f", "lavfi",
            "-i", f"color=c={self._background_color}:s={self._width}x{self._height}:r=25",
            # Audio input 1: Instrumental → [1:a]
            "-i", str(instrumental_path.resolve()),
            # Audio input 2: Original → [2:a]
            "-i", str(original_path.resolve()),
            # Filter: burn ASS subtitles into the colour background
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "1:a",                    # First audio track: Instrumental
            "-map", "2:a",                    # Second audio track: Original
            # Video codec settings
            "-c:v", "libx264",
            "-preset", self._ffmpeg_preset,
            "-tune", "stillimage",
            "-crf", str(self._ffmpeg_crf),
            "-pix_fmt", "yuv420p",
            # Audio codec settings
            "-c:a", "aac",
            "-b:a", self._audio_bitrate,
            # Stop when the audio ends
            "-shortest",
            # Metadata: name the audio tracks
            "-metadata:s:a:0", "title=Instrumental",
            "-metadata:s:a:1", "title=Original",
            # Disposition: first track is default, second is not
            "-disposition:a:0", "default",
            "-disposition:a:1", "0",
            str(output_path.resolve()),
        ]

        logger.info(
            "VideoRenderer: starting ffmpeg render\n  instrumental='%s'\n  original='%s'\n  ass='%s'\n  output='%s'",
            instrumental_path,
            original_path,
            ass_path,
            output_path,
        )
        logger.info("ffmpeg command: %s", " ".join(cmd))

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.error("VideoRenderer: failed to start ffmpeg: %s", exc)
            raise VideoRenderError(
                f"Не удалось запустить ffmpeg: {exc}"
            ) from exc

        stdout, stderr = await process.communicate()
        stderr_text = stderr.decode("utf-8", errors="replace")

        logger.info(
            "VideoRenderer: ffmpeg exited with code %d",
            process.returncode,
        )
        if stderr_text:
            # Always log full ffmpeg stderr at DEBUG level; last 3000 chars at INFO
            logger.debug("ffmpeg stderr (full):\n%s", stderr_text)
            logger.info("ffmpeg stderr (tail):\n%s", stderr_text[-3000:])

        if process.returncode != 0:
            logger.error(
                "ffmpeg render FAILED (exit code %d)",
                process.returncode,
            )
            raise VideoRenderError(
                f"ffmpeg завершился с кодом {process.returncode}. "
                f"Детали: {stderr_text[-1000:]}"
            )

        if not output_path.exists():
            logger.error(
                "VideoRenderer: ffmpeg exited 0 but output file not found: '%s'",
                output_path,
            )
            raise VideoRenderError(
                f"ffmpeg завершился успешно (код 0), но выходной файл не найден: {output_path}"
            )

        output_size = output_path.stat().st_size
        logger.info(
            "VideoRenderer: render complete → '%s' (size=%d bytes)",
            output_path,
            output_size,
        )
        return output_path
