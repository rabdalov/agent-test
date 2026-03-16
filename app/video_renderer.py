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
        backvocal_mix_path: Path | None = None,
        supressedvocal_mix_path: Path | None = None,
    ) -> Path:
        """Render the karaoke video with three (or four) audio tracks.

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
        backvocal_mix_path:
            Optional path to the back-vocal mix file (Instrumental + BackVocal).
            If provided, a fourth audio track is added to the output MP4.
        supressedvocal_mix_path:
            Optional path to the supressedvocal mix file (Instrumental + Vocal at fixed volume).
            If provided, this pre-rendered file is used for the third audio track instead of
            generating the mix on-the-fly.

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

        # Determine if we have a 4th audio track (BackVocal mix)
        has_backvocal = backvocal_mix_path is not None and backvocal_mix_path.exists()

        # Determine if we have pre-rendered supressedvocal mix
        has_supressedvocal_mix = (
            supressedvocal_mix_path is not None and supressedvocal_mix_path.exists()
        )

        # Audio inputs: [0:v] = lavfi color, [1:a] = instrumental, [2:a] = original, [3:a] = vocal
        # Optional [4:a] = backvocal_mix (if provided)
        # Third track: use pre-rendered supressedvocal_mix OR generate on-the-fly
        if has_supressedvocal_mix:
            # Use pre-rendered supressedvocal_mix file as third audio track
            logger.info(
                "VideoRenderer: using pre-rendered supressedvocal_mix='%s'",
                supressedvocal_mix_path,
            )
            # filter_complex only burns ASS subtitles
            filter_complex = f"[0:v]ass='{ass_for_filter}'[vout]"
            # Audio inputs: 1=instrumental, 2=original, 3=supressedvocal_mix
            audio_input_count = 3
        else:
            # Generate mix on-the-fly using amix filter
            # Mix instrumental + vocal (at reduced volume) for third track
            # amix weights: instrumental=1, vocal=mix_voice_volume
            logger.info(
                "VideoRenderer: generating supressedvocal mix on-the-fly (volume=%.2f)",
                self._mix_voice_volume,
            )
            filter_complex = (
                f"[0:v]ass='{ass_for_filter}'[vout];"
                f"[1:a][3:a]amix=inputs=2:duration=longest:weights=1 {self._mix_voice_volume}[a3]"
            )
            # Audio inputs: 1=instrumental, 2=original, 3=vocal (for on-the-fly mixing)
            audio_input_count = 4 if has_backvocal else 3

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
        ]

        # Add third audio input (either pre-rendered or vocal for on-the-fly mixing)
        if has_supressedvocal_mix:
            # Use pre-rendered supressedvocal_mix as third audio track
            cmd += ["-i", str(supressedvocal_mix_path.resolve())]  # type: ignore[union-attr]
        else:
            # Use raw vocal for on-the-fly mixing
            cmd += ["-i", str(vocal_path.resolve())]

        # Optional 4th audio input: BackVocal mix → [4:a]
        if has_backvocal:
            cmd += ["-i", str(backvocal_mix_path.resolve())]  # type: ignore[union-attr]

        cmd += [
            # Filter: burn ASS subtitles
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "1:a",                    # First audio track: Instrumental
            "-map", "2:a",                    # Second audio track: Original
        ]

        # Third audio track: use pre-rendered or on-the-fly mix
        if has_supressedvocal_mix:
            cmd += ["-map", "3:a"]            # Third audio track: pre-rendered supressedvocal_mix
        else:
            cmd += ["-map", "[a3]"]           # Third audio track: on-the-fly mix

        if has_backvocal:
            # Fourth audio track index depends on whether we have pre-rendered mix
            fourth_audio_idx = "4:a" if has_supressedvocal_mix else "5:a"
            cmd += ["-map", fourth_audio_idx]

        cmd += [
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
        ]

        if has_backvocal:
            cmd += ["-metadata:s:a:3", "title=Instrumental+BackVocal"]

        cmd += [
            # Disposition: first track is default, others are not
            "-disposition:a:0", "default",
            "-disposition:a:1", "0",
            "-disposition:a:2", "0",
        ]

        if has_backvocal:
            cmd += ["-disposition:a:3", "0"]

        cmd += [str(output_path.resolve())]

        logger.info(
            "VideoRenderer: starting ffmpeg render\n  instrumental='%s'\n  original='%s'\n  vocal='%s'\n  ass='%s'\n  backvocal_mix='%s'\n  supressedvocal_mix='%s'\n  output='%s'",
            instrumental_path,
            original_path,
            vocal_path,
            ass_path,
            backvocal_mix_path,
            supressedvocal_mix_path,
            output_path,
        )
        
        # Build command string for logging and debug file
        cmd_string = " ".join(cmd)
        logger.info("ffmpeg command: %s", cmd_string)
        
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
