import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _make_batch_command(cmd: list[str]) -> str:
    """Convert ffmpeg command list to Windows batch-compatible string.
    
    Quotes arguments that contain spaces or special characters.
    Does not over-escape parentheses in filenames.
    """
    result = []
    for arg in cmd:
        # Only quote arguments that contain spaces
        if " " in arg:
            result.append(f'"{arg}"')
        else:
            result.append(arg)
    return " ".join(result)


class VideoRenderError(Exception):
    """Raised when ffmpeg video rendering fails."""


class VideoRenderer:
    """Renders a karaoke MP4 video with multiple audio tracks and an ASS subtitle file.

    The renderer generates a static-colour background video stream, burns the ASS
    subtitles into it via the ``ass`` filter, and muxes the result with multiple
    audio tracks (Instrumental, Original, Instrumental+Voice mix).

    Example equivalent shell command (3 audio tracks)::

        ffmpeg -f lavfi -i "color=c=black:s=1280x720:r=25" -i instrumental.mp3 -i original.mp3 -i vocal.mp3 \\
            -filter_complex "[0:v]ass='subtitles.ass'[vout];[1:a][3:a]amix=inputs=2:duration=longest:weights=1 0.4[a3]" \\
            -map "[vout]" -map "1:a" -map "2:a" -map "[a3]" \\
            -c:v libx264 -preset fast -tune stillimage -crf 22 \\
            -c:a aac -b:a 320k -shortest -pix_fmt yuv420p \\
            -metadata:s:a:0 title="Instrumental" -metadata:s:a:1 title="Original" -metadata:s:a:2 title="Instrumental+Voice" \\
            -disposition:a:0 default -disposition:a:1 0 -disposition:a:2 0 \\
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
        mix_voice_volume: float = 0.4,
    ) -> None:
        self._width = width
        self._height = height
        self._background_color = background_color
        self._ffmpeg_preset = ffmpeg_preset
        self._ffmpeg_crf = ffmpeg_crf
        self._audio_bitrate = audio_bitrate
        self._mix_voice_volume = mix_voice_volume

    async def render(
        self,
        *,
        instrumental_path: Path,
        original_path: Path,
        vocal_path: Path,
        ass_path: Path,
        output_path: Path,
    ) -> Path:
        """Render the karaoke video with three audio tracks.

        Parameters
        ----------
        instrumental_path:
            Path to the instrumental audio file (vocals removed).
        original_path:
            Path to the original audio file (full mix with vocals).
        vocal_path:
            Path to the vocal-only audio file (for mixing with instrumental).
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

        # Audio inputs: [0:v] = lavfi color, [1:a] = instrumental, [2:a] = original, [3:a] = vocal
        # Mix instrumental + vocal (at reduced volume) for third track
        # amix weights: instrumental=1, vocal=mix_voice_volume
        filter_complex = (
            f"[0:v]ass='{ass_for_filter}'[vout];"
            f"[1:a][2:a]amix=inputs=2:duration=longest:weights=1 {self._mix_voice_volume}[a3]"
        )

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
            # Audio input 3: Vocal → [3:a]
            "-i", str(vocal_path.resolve()),
            # Filter: burn ASS subtitles + mix instrumental+voice
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "1:a",                    # First audio track: Instrumental
            "-map", "2:a",                    # Second audio track: Original
            "-map", "[a3]",                   # Third audio track: Instrumental+Voice mix
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
            "-metadata:s:a:2", "title=Instrumental+Voice",
            # Disposition: first track is default, others are not
            "-disposition:a:0", "default",
            "-disposition:a:1", "0",
            "-disposition:a:2", "0",
            str(output_path.resolve()),
        ]

        logger.info(
            "VideoRenderer: starting ffmpeg render\n  instrumental='%s'\n  original='%s'\n  vocal='%s'\n  ass='%s'\n  output='%s'",
            instrumental_path,
            original_path,
            vocal_path,
            ass_path,
            output_path,
        )
        
        # Build command string for logging and debug file
        cmd_string = " ".join(cmd)
        logger.info("ffmpeg command: %s", cmd_string)
        
        # Save command to videorender.cmd for debugging
        cmd_file_path = output_path.parent / "videorender.cmd"
        batch_command = _make_batch_command(cmd)
        try:
            cmd_file_path.write_text(
                f"@echo off\nREM ffmpeg command for debugging\nREM Generated by VideoRenderer\n{batch_command}\n",
                encoding="utf-8"
            )
            logger.info("ffmpeg command saved to '%s'", cmd_file_path)
        except OSError as save_exc:
            logger.warning("Failed to save videorender.cmd: %s", save_exc)

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
