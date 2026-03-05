import asyncio
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class DemucsService:
    def __init__(self, model: str, output_format: str, output_dir: str) -> None:
        """
        :param model: Название модели demucs (например "htdemucs")
        :param output_format: Формат выходных файлов ("mp3")
        :param output_dir: Базовая директория для результатов demucs
        """
        self._model = model
        self._output_format = output_format
        self._output_dir = Path(output_dir)

    async def separate(self, audio_path: str, track_dir: str) -> tuple[str, str]:
        """Запускает demucs асинхронно и возвращает пути к файлам (vocals, accompaniment).

        Demucs сохраняет файлы в:
            {output_dir}/{model}/{track_stem}/vocals.mp3
            {output_dir}/{model}/{track_stem}/no_vocals.mp3

        После завершения файлы копируются в:
            {track_dir}/vocals.mp3
            {track_dir}/accompaniment.mp3

        :param audio_path: Путь к входному аудиофайлу
        :param track_dir: Целевая директория для результирующих файлов
        :returns: Кортеж (vocals_path, accompaniment_path)
        :raises RuntimeError: При ненулевом коде возврата demucs
        """
        input_source = Path(audio_path)
        dest_dir = Path(track_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        #python3 -m demucs -d cpu "$INPUT_SOURCE" --mp3 --two-stems=vocals -o "${DIR_NAME}" -n ${DEMUCS_MODEL}

        cmd: list[str] = [
            sys.executable,
            "-m",
            "demucs",
            "-d", "cpu", 
            "--two-stems=vocals",
            "--mp3",
            "--mp3-bitrate", "320",
            "-n", self._model,
            "-o", str(self._output_dir),
            str(input_source),
        ]

        logger.info(
            "DemucsService: starting separation for '%s' with model '%s'",
            audio_path,
            self._model,
        )

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()

        if process.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")
            logger.error(
                "DemucsService: demucs failed (exit code %d) for '%s': %s",
                process.returncode,
                audio_path,
                stderr_text,
            )
            raise RuntimeError(
                f"demucs завершился с ошибкой (код {process.returncode}): {stderr_text}"
            )

        logger.info(
            "DemucsService: demucs finished successfully for '%s'",
            audio_path,
        )

        # Demucs сохраняет файлы по пути {output_dir}/{model}/{track_stem}/
        track_stem = input_source.stem
        demucs_track_dir = self._output_dir / self._model / track_stem
        #print(f"DemucsService: demucs track directory: '{demucs_track_dir}'")

        src_vocals = demucs_track_dir / "vocals.mp3"
        src_no_vocals = demucs_track_dir / "no_vocals.mp3"
        #print(f"DemucsService: source vocals path: '{src_vocals}', source accompaniment path: '{src_no_vocals}'")
        dest_vocals = dest_dir / f"{track_stem}_(Vocals).mp3"
        dest_accompaniment = dest_dir / f"{track_stem}_(Instrumental).mp3"
        print(f"DemucsService: Dest vocals path: '{dest_vocals}', source accompaniment path: '{dest_accompaniment}'")

        if not src_vocals.exists():
            raise RuntimeError(
                f"Файл голоса не найден по ожидаемому пути: {src_vocals}"
            )
        if not src_no_vocals.exists():
            raise RuntimeError(
                f"Файл инструментала не найден по ожидаемому пути: {src_no_vocals}"
            )

        shutil.copy2(src_vocals, dest_vocals)
        shutil.copy2(src_no_vocals, dest_accompaniment)

        logger.info(
            "DemucsService: saved vocals to '%s', accompaniment to '%s'",
            dest_vocals,
            dest_accompaniment,
        )

        return str(dest_vocals), str(dest_accompaniment)
