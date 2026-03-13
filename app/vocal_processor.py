"""VocalProcessor — обработка вокальной дорожки с разметкой громкости.

Применяет разметку громкости к вокальной дорожке через ffmpeg с фильтром `volume`
по временным сегментам. Предусмотрены заглушки/хуки для будущих фильтров:
реверберация, эхо, эквалайзер и т.д.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class VolumeSegment:
    """Временной сегмент с заданной громкостью вокала.

    Attributes
    ----------
    start:
        Начало сегмента в секундах.
    end:
        Конец сегмента в секундах.
    volume:
        Громкость вокала в данном сегменте (0.0–1.0, где 1.0 = 100%).
    """
    start: float
    end: float
    volume: float


class VocalProcessorError(Exception):
    """Raised when vocal processing fails."""


class VocalProcessor:
    """Обрабатывает вокальную дорожку с разметкой громкости по сегментам.

    Применяет разметку громкости через ffmpeg с фильтром ``volume`` по временным
    сегментам. Поддерживает расширяемость через флаги конфигурации для будущих
    фильтров (реверберация, эхо).

    Parameters
    ----------
    reverb_enabled:
        Включить реверберацию вокала (по умолчанию ``False``).
    echo_enabled:
        Включить эхо вокала (по умолчанию ``False``).
    """

    def __init__(
        self,
        *,
        reverb_enabled: bool = False,
        echo_enabled: bool = False,
    ) -> None:
        self._reverb_enabled = reverb_enabled
        self._echo_enabled = echo_enabled

    async def process(
        self,
        vocal_file: str,
        volume_segments: list[VolumeSegment],
        output_file: str,
    ) -> str:
        """Применить разметку громкости к вокальной дорожке.

        Parameters
        ----------
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
        vocal_path = Path(vocal_file)
        output_path = Path(output_file)

        if not vocal_path.exists():
            raise VocalProcessorError(
                f"Вокальный файл не найден: {vocal_file}"
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build ffmpeg volume filter expression
        volume_filter = self._build_volume_filter(volume_segments)

        # Build full filter chain
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

        cmd: list[str] = [
            "ffmpeg",
            "-y",
            "-i", str(vocal_path.resolve()),
            "-af", filter_chain,
            "-c:a", "libmp3lame",
            "-b:a", "320k",
            "-ar", "44100",
            str(output_path.resolve()),
        ]

        logger.info(
            "VocalProcessor: processing vocal '%s' → '%s' with %d volume segments",
            vocal_file,
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
        """Построить строку ffmpeg-фильтра `volume` с временными сегментами.

        Использует выражение ``enable='between(t,start,end)'`` для каждого сегмента.
        Между сегментами применяется базовая громкость (последнее значение).

        Parameters
        ----------
        volume_segments:
            Список сегментов с заданной громкостью, отсортированных по времени.

        Returns
        -------
        str
            Строка ffmpeg-фильтра, например:
            ``volume=volume='if(between(t,10,30),0.3,if(between(t,60,90),0.3,0.4))'``
        """
        if not volume_segments:
            return "volume=1.0"

        # Sort segments by start time
        sorted_segments = sorted(volume_segments, key=lambda s: s.start)

        # Determine default volume (volume outside all segments)
        # Use the volume of the first segment as default (typically AUDIO_MIX_VOICE_VOLUME)
        # The last segment's volume is used as the "else" fallback
        default_volume = sorted_segments[0].volume

        # Build nested if expression for ffmpeg volume filter
        # Format: if(between(t,start,end),vol,if(between(t,...),vol,...,default))
        # We build from the inside out (last segment first)
        expr = str(default_volume)
        for seg in reversed(sorted_segments):
            expr = f"if(between(t,{seg.start:.3f},{seg.end:.3f}),{seg.volume},{expr})"

        return f"volume=volume='{expr}'"

    @staticmethod
    def build_volume_segments(
        chorus_segments: list[tuple[float, float]],
        audio_duration: float,
        chorus_volume: float,
        default_volume: float,
    ) -> list[VolumeSegment]:
        """Построить список сегментов громкости на основе найденных припевов.

        Parameters
        ----------
        chorus_segments:
            Список кортежей ``(start_sec, end_sec)`` для каждого припева.
        audio_duration:
            Общая длительность аудиофайла в секундах.
        chorus_volume:
            Громкость вокала в припевах (``CHORUS_BACKVOCAL_VOLUME``).
        default_volume:
            Громкость вокала вне припевов (``AUDIO_MIX_VOICE_VOLUME``).

        Returns
        -------
        list[VolumeSegment]
            Полный список сегментов, покрывающий весь трек.
        """
        if not chorus_segments:
            # No chorus detected — use default volume for the whole track
            return [VolumeSegment(start=0.0, end=audio_duration, volume=default_volume)]

        sorted_chorus = sorted(chorus_segments, key=lambda s: s[0])
        segments: list[VolumeSegment] = []

        current_pos = 0.0
        for start, end in sorted_chorus:
            # Non-chorus segment before this chorus
            if start > current_pos:
                segments.append(
                    VolumeSegment(start=current_pos, end=start, volume=default_volume)
                )
            # Chorus segment
            segments.append(VolumeSegment(start=start, end=end, volume=chorus_volume))
            current_pos = end

        # Non-chorus segment after the last chorus
        if current_pos < audio_duration:
            segments.append(
                VolumeSegment(start=current_pos, end=audio_duration, volume=default_volume)
            )

        return segments

    @staticmethod
    def save_volume_segments(
        segments: list[VolumeSegment],
        output_path: Path,
    ) -> None:
        """Сохранить разметку громкости в JSON-файл.

        Parameters
        ----------
        segments:
            Список сегментов громкости.
        output_path:
            Путь к выходному JSON-файлу.
        """
        data = [
            {"start": seg.start, "end": seg.end, "volume": seg.volume}
            for seg in segments
        ]
        output_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug(
            "VocalProcessor: saved %d volume segments to '%s'",
            len(segments),
            output_path,
        )
