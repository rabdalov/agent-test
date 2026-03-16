"""VocalProcessor — обработка вокальной дорожки с разметкой громкости.

Объединённый метод для применения разметки громкости к вокальной дорожке
через ffmpeg с фильтром `volume` по временным сегментам и последующего
микширования с инструменталом в один проход (один ffmpeg вызов).

Предусмотрены заглушки/хуки для будущих фильтров:
реверберация, эхо, эквалайзер и т.д.
"""

import asyncio
import logging
from pathlib import Path

from .chorus_detector import VolumeSegment  # noqa: F401 — re-exported for convenience

logger = logging.getLogger(__name__)


class VocalProcessorError(Exception):
    """Raised when vocal processing fails."""


class VocalProcessor:
    """Обрабатывает вокальную дорожку с разметкой громкости по сегментам.

    Применяет разметку громкости через ffmpeg с фильтром ``volume`` по временным
    сегментам и микширует с инструменталом в один проход (один ffmpeg вызов).
    Это устраняет двойное перекодирование MP3 и улучшает качество звука.

    Поддерживает расширяемость через флаги конфигурации для будущих
    фильтров (реверберация, эхо).

    Parameters
    ----------
    reverb_enabled:
        Включить реверберацию вокала (по умолчанию ``False``).
    echo_enabled:
        Включить эхо вокала (по умолчанию ``False``).
    mix_voice_volume:
        Громкость вокала при микшировании с инструменталом (по умолчанию ``0.4``).
    """

    def __init__(
        self,
        *,
        reverb_enabled: bool = False,
        echo_enabled: bool = False,
        mix_voice_volume: float = 0.4,
    ) -> None:
        self._reverb_enabled = reverb_enabled
        self._echo_enabled = echo_enabled
        self._mix_voice_volume = mix_voice_volume

    async def process_and_mix(
        self,
        instrumental_file: str,
        vocal_file: str,
        volume_segments: list[VolumeSegment],
        output_file: str,
    ) -> str:
        """Применить разметку громкости к вокальной дорожке и микшировать с инструменталом.

        Выполняет обе операции в одном ffmpeg вызове:
        1. Применяет volume filter к вокальной дорожке по временным сегментам
        2. Микширует обработанный вокал с инструменталом

        Это устраняет двойное перекодирование MP3 и улучшает качество звука.

        Parameters
        ----------
        instrumental_file:
            Путь к файлу инструментала.
        vocal_file:
            Путь к исходному файлу вокальной дорожки.
        volume_segments:
            Список сегментов с заданной громкостью.
        output_file:
            Путь к выходному файлу обработанной вокальной дорожки.

        Returns
        -------
        str
            Путь к выходному файлу (совпадает с ``output_file``).

        Raises
        ------
        VocalProcessorError
            Если ffmpeg завершился с ошибкой или не удалось запустить.
        """
        instrumental_path = Path(instrumental_file)
        vocal_path = Path(vocal_file)
        output_path = Path(output_file)

        if not instrumental_path.exists():
            raise VocalProcessorError(
                f"Инструментал не найден: {instrumental_file}"
            )

        if not vocal_path.exists():
            raise VocalProcessorError(
                f"Вокальный файл не найден: {vocal_file}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build ffmpeg volume filter expression
        volume_filter = self._build_volume_filter(volume_segments)

        # Build full filter chain for vocal processing
        filter_chain = volume_filter

        # Hook: reverb (placeholder for future implementation)
        if self._reverb_enabled:
            logger.debug(
                "VocalProcessor: reverb_enabled=True (placeholder, not yet implemented)"
            )
            # Future: filter_chain += ",aecho=0.8:0.88:60:0.4"

        # Hook: echo (placeholder for future implementation)
        if self._echo_enabled:
            logger.debug(
                "VocalProcessor: echo_enabled=True (placeholder, not yet implemented)"
            )
            # Future: filter_chain += ",aecho=0.8:0.9:1000:0.3"

        # Build complex filter: apply volume to vocal, then mix with instrumental
        # [1:a] - vocal input (second -i)
        # volume filter applied to vocal
        # [0:a] - instrumental input (first -i)
        # amix - mix both with equal weights (1:1)
        filter_complex = f"[1:a]{filter_chain}[vocal];[0:a][vocal]amix=inputs=2:duration=longest:weights=1 1[aout]"

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-i", str(instrumental_path.resolve()),
            "-i", str(vocal_path.resolve()),
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            "-ar", "44100",
            str(output_path.resolve()),
        ]

        logger.info(
            "VocalProcessor: processing vocal '%s' + instrumental '%s' → '%s' with %d volume segments (single pass)",
            vocal_file,
            instrumental_file,
            output_file,
            len(volume_segments),
        )
        logger.debug("VocalProcessor: ffmpeg command: %s", " ".join(cmd))

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise VocalProcessorError(
                f"Не удалось запустить ffmpeg: {exc}"
            ) from exc

        stdout, stderr = await process.communicate()
        stderr_text = stderr.decode("utf-8", errors="replace")

        if stderr_text:
            logger.debug("VocalProcessor ffmpeg stderr:\n%s", stderr_text)

        if process.returncode != 0:
            logger.error(
                "VocalProcessor: ffmpeg failed (exit code %d): %s",
                process.returncode,
                stderr_text[-1000:],
            )
            raise VocalProcessorError(
                f"ffmpeg завершился с кодом {process.returncode}. "
                f"Детали: {stderr_text[-500:]}"
            )

        if not output_path.exists():
            raise VocalProcessorError(
                f"ffmpeg завершился успешно, но выходной файл не найден: {output_file}"
            )

        logger.info(
            "VocalProcessor: processing complete → '%s' (size=%d bytes)",
            output_file,
            output_path.stat().st_size,
        )
        return output_file

    def _build_volume_filter(self, volume_segments: list[VolumeSegment]) -> str:
        """Построить строку ffmpeg-фильтра ``volume`` с временными сегментами.

        Использует вложенное выражение ``if(between(t,start,end),vol,...)``
        для каждого сегмента. Параметр ``eval=frame`` обязателен — без него
        ffmpeg вычисляет выражение **один раз** (при ``t=0``) и применяет
        одну громкость ко всему файлу.

        Parameters
        ----------
        volume_segments:
            Список сегментов с заданной громкостью, отсортированных по времени.

        Returns
        -------
        str
            Строка ffmpeg-фильтра, например::

                volume=volume='if(between(t,10,30),1.0,if(between(t,60,90),1.0,0.4))':eval=frame
        """
        if not volume_segments:
            return f"volume={self._mix_voice_volume}"

        # Sort segments by start time
        sorted_segments = sorted(volume_segments, key=lambda s: s.start)

        # Determine default volume (volume outside all segments).
        # When segments cover the whole track (normal case from build_volume_segments),
        # this fallback is never reached. When only chorus segments are passed,
        # the fallback should be the non-chorus (lower) volume — use the last
        # segment's volume as a safe default (typically AUDIO_MIX_VOICE_VOLUME).
        default_volume = sorted_segments[-1].volume

        # Build nested if expression for ffmpeg volume filter.
        # Format: if(between(t,start,end),vol,if(between(t,...),vol,...,default))
        # We build from the inside out (last segment first).
        expr = str(default_volume)
        for seg in reversed(sorted_segments):
            expr = f"if(between(t,{seg.start:.3f},{seg.end:.3f}),{seg.volume},{expr})"

        # eval=frame is REQUIRED: without it ffmpeg evaluates the expression only
        # once (at t=0) and applies a single constant volume to the whole file.
        return f"volume=volume='{expr}':eval=frame"

    async def mix_instrumental_and_vocal_fixed_volume(
        self,
        instrumental_path: Path,
        vocal_path: Path,
        output_path: Path,
    ) -> None:
        """Mix instrumental and raw vocal tracks with fixed volume ratio.

        Creates a pre-rendered audio mix of instrumental + vocal at a fixed
        volume ratio (defined by self._mix_voice_volume). This is used for
        the "supressedvocal_mix" track in the final video.

        Uses ffmpeg amix filter with weights: instrumental=1, vocal=mix_voice_volume.

        Parameters
        ----------
        instrumental_path:
            Path to the instrumental (accompaniment) audio file.
        vocal_path:
            Path to the raw vocal audio file (not processed).
        output_path:
            Path to the output mixed audio file.

        Raises
        ------
        VocalProcessorError
            If ffmpeg fails to start or returns a non-zero exit code.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # amix with fixed weight ratio: instrumental=1, vocal=mix_voice_volume
        # This matches the filter_complex used in VideoRenderer.render()
        filter_complex = (
            f"[0:a][1:a]amix=inputs=2:duration=longest:weights=1 {self._mix_voice_volume}[aout]"
        )

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-i", str(instrumental_path.resolve()),
            "-i", str(vocal_path.resolve()),
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            "-ar", "44100",
            str(output_path.resolve()),
        ]

        logger.debug(
            "MIX_AUDIO (fixed volume): mixing instrumental + vocal → '%s'",
            output_path,
        )
        logger.info(
            "mix_instrumental_and_vocal_fixed_volume: using vocal volume=%.2f",
            self._mix_voice_volume,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise VocalProcessorError(
                f"Не удалось запустить ffmpeg для микширования: {exc}"
            ) from exc

        stdout, stderr = await process.communicate()
        stderr_text = stderr.decode("utf-8", errors="replace")

        if stderr_text:
            logger.debug("mix_instrumental_and_vocal_fixed_volume ffmpeg stderr:\n%s", stderr_text)

        if process.returncode != 0:
            raise VocalProcessorError(
                f"ffmpeg завершился с кодом {process.returncode} при микшировании. "
                f"Детали: {stderr_text[-500:]}"
            )

        if not output_path.exists():
            raise VocalProcessorError(
                f"ffmpeg завершился успешно, но файл микса не найден: {output_path}"
            )

        logger.info(
            "MIX_AUDIO (fixed volume): supressedvocal_mix created → '%s' (size=%d bytes)",
            output_path,
            output_path.stat().st_size,
        )
