import asyncio
import json
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import quote

import httpx
from aiogram import Bot

from .alignment_service import AlignmentService, save_aligned_result
from .ass_generator import AssGenerator
from .chorus_detector import (
    ChorusDetector,
    SegmentScore,
    VolumeSegment,
    build_volume_segments,
    load_volume_segments,
    save_volume_segments,
    should_merge_same_type,
)
from .config import Settings
from .correct_transcript_service import CorrectTranscriptService
from .demucs_service import DemucsService
from .llm_client import LLMClient
from .lyrics_service import LyricsService
from .speeches_client import SpeechesClient
from .video_renderer import VideoRenderer
from .vocal_processor import VocalProcessor
from .models import (
    PipelineResult,
    PipelineState,
    PipelineStatus,
    PipelineStep,
    SourceType,
    UserRequest,
)
from .utils import normalize_filename


class LyricsNotFoundError(Exception):
    """Raised when lyrics cannot be found automatically."""
    pass


class WaitingForInputError(Exception):
    """Raised when pipeline needs to pause and wait for user input (e.g., language selection)."""
    pass


logger = logging.getLogger(__name__)

_STEP_LABELS: dict[PipelineStep, str] = {
    PipelineStep.DOWNLOAD: "скачивание",
    PipelineStep.ASK_LANGUAGE: "выбор языка",
    PipelineStep.GET_LYRICS: "получение текста",
    PipelineStep.SEPARATE: "разделение дорожек",
    PipelineStep.TRANSCRIBE: "транскрипция",
    PipelineStep.GENERATE_LYRICS: "генерация текста песни из транскрипции",
    PipelineStep.DETECT_CHORUS: "определение припевов",
    PipelineStep.CORRECT_TRANSCRIPT: "корректировка транскрипции",
    PipelineStep.ALIGN: "выравнивание",
    PipelineStep.MIX_AUDIO: "обработка вокала (бэк-вокал)",
    PipelineStep.GENERATE_ASS: "генерация субтитров",
    PipelineStep.RENDER_VIDEO: "рендеринг видео",
    PipelineStep.SEND_VIDEO: "отправка результата",
}

_ORDERED_STEPS: list[PipelineStep] = [
    PipelineStep.DOWNLOAD,
    PipelineStep.ASK_LANGUAGE,
    PipelineStep.GET_LYRICS,
    PipelineStep.SEPARATE,
    PipelineStep.TRANSCRIBE,
    PipelineStep.GENERATE_LYRICS,
    PipelineStep.DETECT_CHORUS,
    PipelineStep.CORRECT_TRANSCRIPT,
    PipelineStep.ALIGN,
    PipelineStep.MIX_AUDIO,
    PipelineStep.GENERATE_ASS,
    PipelineStep.RENDER_VIDEO,
    PipelineStep.SEND_VIDEO,
]

# Required artifact fields that must be set before a given step can run.
# If a step is absent, no prerequisite artifacts are needed.
_STEP_REQUIRED_ARTIFACTS: dict[PipelineStep, list[str]] = {
    PipelineStep.ASK_LANGUAGE: ["track_source", "track_stem"],
    PipelineStep.GET_LYRICS: ["track_file_name", "track_stem"],
    PipelineStep.SEPARATE: ["track_source"],
    PipelineStep.TRANSCRIBE: ["vocal_file"],
    PipelineStep.GENERATE_LYRICS: ["transcribe_json_file"],
    PipelineStep.DETECT_CHORUS: ["vocal_file", "instrumental_file"],
    PipelineStep.CORRECT_TRANSCRIPT: ["transcribe_json_file", "source_lyrics_file"],
    PipelineStep.ALIGN: ["source_lyrics_file", "transcribe_json_file"],
    PipelineStep.MIX_AUDIO: ["vocal_file", "instrumental_file", "volume_segments_file"],
    PipelineStep.GENERATE_ASS: ["aligned_lyrics_file", "segment_groups_file"],
    PipelineStep.RENDER_VIDEO: ["ass_file", "vocal_file", "instrumental_file"],
    PipelineStep.SEND_VIDEO: ["output_file"],
}


class KaraokePipeline:
    def __init__(
        self,
        settings: Settings,
        state: PipelineState,
        track_folder: str,
        bot: Bot | None = None,
    ) -> None:
        self._settings = settings
        self._state = state
        self._track_folder = Path(track_folder)
        self._bot = bot
        demucs_output_dir = str(self._track_folder.parent)
        self._demucs_service = DemucsService(
            model=settings.demucs_model,
            output_format=settings.demucs_output_format,
            output_dir=demucs_output_dir,
        )
        self._speeches_client = SpeechesClient(settings=settings)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def create_new(
        cls,
        settings: Settings,
        user_id: int,
        source_type: SourceType,
        source_url: str,
        track_folder: str,
        bot: Bot | None = None,
        telegram_file_id: str | None = None,
    ) -> "KaraokePipeline":
        """Create a new pipeline with a fresh PipelineState."""
        track_id = uuid.uuid4().hex
        state = PipelineState(
            track_id=track_id,
            user_id=user_id,
            status=PipelineStatus.PENDING,
            source_type=source_type,
            source_url=source_url,
            telegram_file_id=telegram_file_id,
        )
        pipeline = cls(settings=settings, state=state, track_folder=track_folder, bot=bot)
        pipeline._save_state()
        return pipeline

    @classmethod
    def from_state(
        cls,
        settings: Settings,
        state: PipelineState,
        track_folder: str,
        bot: Bot | None = None,
    ) -> "KaraokePipeline":
        """Create a pipeline from an existing PipelineState."""
        return cls(settings=settings, state=state, track_folder=track_folder, bot=bot)

    async def run(
        self,
        progress_callback: Callable[[str], Awaitable[None]],
        start_from_step: PipelineStep | None = None,
    ) -> PipelineResult:
        """Run the pipeline.

        Resolution order for the starting step:
        1. If *start_from_step* is explicitly provided — use it (after
           validating prerequisite artifacts).
        2. Else if state has ``status == FAILED`` — resume from ``current_step``.
        3. Otherwise — run from the very beginning (DOWNLOAD).

        Pipeline can pause at ASK_LANGUAGE step (raises WaitingForInputError).
        After user provides input, call resume() to continue.
        """
        first_step: PipelineStep

        logger.info(
            "Pipeline.run called for track_id=%s with start_from_step=%s",
            self._state.track_id,
            start_from_step,
        )

        if start_from_step is not None:
            # Mode 3: explicit start_from_step
            logger.info(
                "Pipeline mode 3: explicit start_from_step=%s",
                start_from_step,
            )
            validation_error = self._validate_artifacts_for_step(start_from_step)
            if validation_error:
                logger.error(
                    "Cannot start from step %s for track_id=%s: %s",
                    start_from_step.value,
                    self._state.track_id,
                    validation_error,
                )
                self._state.status = PipelineStatus.FAILED
                self._state.error_message = validation_error
                self._save_state()
                return PipelineResult(
                    track_id=self._state.track_id,
                    status=PipelineStatus.FAILED,
                    error_message=validation_error,
                )
            first_step = start_from_step
            logger.info(
                "Pipeline starting from explicit step %s for track_id=%s",
                first_step.value,
                self._state.track_id,
            )
        elif self._state.status == PipelineStatus.FAILED and self._state.current_step is not None:
            # Mode 2: resume from last failed step
            first_step = self._state.current_step
            logger.info(
                "Pipeline resuming from step %s for track_id=%s",
                first_step.value,
                self._state.track_id,
            )
            await progress_callback(
                f"🔄 Возобновление с шага {first_step.value}: {_STEP_LABELS[first_step]}..."
            )
        elif self._state.status == PipelineStatus.WAITING_FOR_INPUT and self._state.current_step is not None:
            # Resume after waiting for input — continue from NEXT step after ASK_LANGUAGE
            current_index = _ORDERED_STEPS.index(self._state.current_step)
            if current_index + 1 < len(_ORDERED_STEPS):
                first_step = _ORDERED_STEPS[current_index + 1]
            else:
                first_step = _ORDERED_STEPS[0]
            logger.info(
                "Pipeline resuming after WAITING_FOR_INPUT from step %s for track_id=%s",
                first_step.value,
                self._state.track_id,
            )
        else:
            # Mode 1: fresh start
            first_step = PipelineStep.DOWNLOAD
            logger.info(
                "Pipeline starting fresh for track_id=%s (lang=%s)",
                self._state.track_id,
                self._state.lang,
            )

        return await self._execute_from(first_step, progress_callback)

    async def resume(
        self,
        progress_callback: Callable[[str], Awaitable[None]],
    ) -> PipelineResult:
        """Resume pipeline after WAITING_FOR_INPUT (e.g., after user selected language)."""
        if self._state.status != PipelineStatus.WAITING_FOR_INPUT:
            logger.warning(
                "Pipeline.resume called but status is %s (expected WAITING_FOR_INPUT) for track_id=%s",
                self._state.status,
                self._state.track_id,
            )

        # Continue from next step after the waiting step
        if self._state.current_step is not None:
            current_index = _ORDERED_STEPS.index(self._state.current_step)
            if current_index + 1 < len(_ORDERED_STEPS):
                next_step = _ORDERED_STEPS[current_index + 1]
            else:
                next_step = _ORDERED_STEPS[0]
        else:
            next_step = PipelineStep.GET_LYRICS

        logger.info(
            "Pipeline.resume: continuing from step %s for track_id=%s",
            next_step.value,
            self._state.track_id,
        )
        return await self._execute_from(next_step, progress_callback)

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    async def _execute_from(
        self,
        first_step: PipelineStep,
        progress_callback: Callable[[str], Awaitable[None]],
    ) -> PipelineResult:
        self._state.status = PipelineStatus.IN_PROGRESS
        self._save_state()

        # Store callback reference for potential edit-in-place
        self._progress_callback = progress_callback

        step_methods: dict[PipelineStep, Callable[[], Awaitable[None]]] = {
            PipelineStep.DOWNLOAD: self._step_download,
            PipelineStep.ASK_LANGUAGE: self._step_ask_language,
            PipelineStep.GET_LYRICS: self._step_get_lyrics,
            PipelineStep.SEPARATE: self._step_separate,
            PipelineStep.TRANSCRIBE: self._step_transcribe,
            PipelineStep.GENERATE_LYRICS: self._step_generate_lyrics,
            PipelineStep.DETECT_CHORUS: self._step_detect_chorus,
            PipelineStep.CORRECT_TRANSCRIPT: self._step_correct_transcribe,
            PipelineStep.ALIGN: self._step_align,
            PipelineStep.MIX_AUDIO: self._step_mix_audio,
            PipelineStep.GENERATE_ASS: self._step_generate_ass,
            PipelineStep.RENDER_VIDEO: self._step_render_video,
            PipelineStep.SEND_VIDEO: self._step_send_video,
        }

        start_index = _ORDERED_STEPS.index(first_step)
        steps_to_run = _ORDERED_STEPS[start_index:]

        for step in steps_to_run:
            label = _STEP_LABELS[step]
            self._state.current_step = step
            self._state.status = PipelineStatus.IN_PROGRESS
            self._save_state()

            logger.info("Step %s started for track_id=%s", step.value, self._state.track_id)
            await progress_callback(f"⏳ Шаг {step.value}: {label}...")

            # Remember track folder before step (may change during DOWNLOAD)
            folder_before_step = self._track_folder

            try:
                await step_methods[step]()
            except WaitingForInputError:
                # Pipeline paused waiting for user input
                self._state.status = PipelineStatus.WAITING_FOR_INPUT
                self._save_state()
                # Clean up temporary folder if track folder changed during DOWNLOAD
                if step == PipelineStep.DOWNLOAD and self._track_folder != folder_before_step:
                    self._cleanup_tmp_folder(folder_before_step)
                raise
            except LyricsNotFoundError:
                # Let this propagate — the handler will request lyrics from the user.
                self._state.status = PipelineStatus.FAILED
                self._state.error_message = "Требуется ручной ввод текста песни"
                self._save_state()
                # Clean up temporary folder if track folder changed during DOWNLOAD
                if step == PipelineStep.DOWNLOAD and self._track_folder != folder_before_step:
                    self._cleanup_tmp_folder(folder_before_step)
                raise
            except Exception as exc:
                # Check if this is CORRECT_TRANSCRIPT step - continue to next step on error
                if step == PipelineStep.CORRECT_TRANSCRIPT:
                    error_msg = f"Ошибка на шаге {step.value}: {exc}"
                    logger.warning(
                        "Step %s failed for track_id=%s: %s. Continuing to next step.",
                        step.value,
                        self._state.track_id,
                        exc,
                    )
                    await progress_callback(f"⚠️ Шаг {step.value} завершился с ошибкой: {exc}. Продолжаю...")
                    # Continue to next step instead of returning error
                    continue

                error_msg = f"Ошибка на шаге {step.value}: {exc}"
                logger.error(
                    "Step %s failed for track_id=%s: %s",
                    step.value,
                    self._state.track_id,
                    exc,
                )
                self._state.status = PipelineStatus.FAILED
                self._state.error_message = error_msg
                self._save_state()
                # Clean up temporary folder if track folder changed during DOWNLOAD
                if step == PipelineStep.DOWNLOAD and self._track_folder != folder_before_step:
                    self._cleanup_tmp_folder(folder_before_step)
                await progress_callback(f"❌ Шаг {step.value} завершился с ошибкой: {exc}")
                return PipelineResult(
                    track_id=self._state.track_id,
                    status=PipelineStatus.FAILED,
                    error_message=error_msg,
                )

            # Clean up temporary folder if track folder changed during DOWNLOAD
            if step == PipelineStep.DOWNLOAD and self._track_folder != folder_before_step:
                self._cleanup_tmp_folder(folder_before_step)

            logger.info("Step %s completed for track_id=%s", step.value, self._state.track_id)
            await progress_callback(f"✅ Шаг {step.value}: {_STEP_LABELS[step]}...завершён")

        self._state.status = PipelineStatus.COMPLETED
        self._state.error_message = None
        self._save_state()
        logger.info("Pipeline completed for track_id=%s", self._state.track_id)

        return PipelineResult(
            track_id=self._state.track_id,
            status=PipelineStatus.COMPLETED,
            final_video_path=self._state.output_file,
        )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        state_path = self._track_folder / "state.json"
        try:
            state_path.write_text(self._state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to save state.json for track_id=%s: %s",
                self._state.track_id,
                exc,
            )

    @staticmethod
    def load_state(track_folder: Path) -> PipelineState | None:
        """Load PipelineState from state.json in the given folder."""
        state_path = track_folder / "state.json"
        if not state_path.exists():
            return None
        try:
            return PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "Failed to load state.json from %s: %s",
                track_folder,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Artifact prerequisite validation
    # ------------------------------------------------------------------

    def _validate_artifacts_for_step(self, step: PipelineStep) -> str | None:
        """Return an error message if required artifacts for *step* are absent,
        or None if all prerequisites are satisfied."""
        required_fields = _STEP_REQUIRED_ARTIFACTS.get(step)
        if not required_fields:
            return None

        missing: list[str] = []
        for field in required_fields:
            value = getattr(self._state, field, None)
            if not value:
                missing.append(field)

        if missing:
            return (
                f"Невозможно начать с шага {step.value}: "
                f"отсутствуют артефакты предыдущих шагов: {', '.join(missing)}"
            )
        return None

    # ------------------------------------------------------------------
    # Step: DOWNLOAD — унифицированная загрузка из любого источника
    # ------------------------------------------------------------------

    async def _step_download(self) -> None:
        """Unified download step supporting all source types."""
        source_type = self._state.source_type
        source_url = self._state.source_url or ""

        if source_type == SourceType.TELEGRAM_FILE:
            await self._download_telegram_file()
        elif source_type == SourceType.YANDEX_MUSIC:
            await self._download_yandex_music(source_url)
        elif source_type == SourceType.YOUTUBE:
            await self._download_youtube(source_url)
        elif source_type == SourceType.HTTP_URL:
            await self._download_http_url(source_url)
        elif source_type == SourceType.LOCAL_FILE:
            await self._use_local_file(source_url)
        else:
            # Fallback: treat source_url as local file path
            await self._use_local_file(source_url)

    async def _download_telegram_file(self) -> None:
        """Download file from Telegram using bot API."""
        file_id = self._state.telegram_file_id
        if not file_id:
            raise RuntimeError("telegram_file_id не задан для source_type=TELEGRAM_FILE")
        if not self._bot:
            raise RuntimeError("Bot instance not provided to pipeline for Telegram file download")

        # Get file info from Telegram
        file_info = await self._bot.get_file(file_id)
        file_path = file_info.file_path
        if not file_path:
            raise RuntimeError(f"Не удалось получить путь к файлу для file_id={file_id}")

        # Determine filename from source_url (original filename stored there)
        original_name = self._state.source_url or f"audio_{file_id}.mp3"
        original_name = Path(original_name).name

        # Normalize track name
        track_stem = normalize_filename(Path(original_name).stem)
        suffix = Path(original_name).suffix or ".mp3"

        # Ensure track folder matches track_stem
        target_dir = self._track_folder.parent / track_stem
        target_dir.mkdir(parents=True, exist_ok=True)
        self._track_folder = target_dir

        final_path = target_dir / f"{track_stem}{suffix}"

        # Download file with extended timeout for large audio files (default 30s is not enough)
        logger.info(
            "DOWNLOAD (telegram_file): downloading file_id=%s to '%s'",
            file_id,
            final_path,
        )
        try:
            await self._bot.download_file(file_path, destination=final_path, timeout=300)
        except Exception as exc:
            raise RuntimeError(
                f"Ошибка при скачивании файла из Telegram (file_id={file_id}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        self._state.track_file_name = final_path.name
        self._state.track_source = str(final_path)
        self._state.track_stem = track_stem
        self._save_state()

        logger.info(
            "DOWNLOAD (telegram_file) completed for track_id=%s: file='%s'",
            self._state.track_id,
            final_path,
        )

    async def _download_yandex_music(self, url: str) -> None:
        """Download track from Yandex Music."""
        from .yandex_music_downloader import YandexMusicDownloader

        downloader = YandexMusicDownloader(token=self._settings.yandex_music_token)

        # Get track info first to build directory name
        track_info = await downloader.get_track_info(url)

        # Build track name
        track_stem = normalize_filename(
            f"{track_info.artist} - {track_info.title}" if track_info.artist and track_info.title
            else track_info.title or "track"
        )

        # Ensure track folder matches track_stem
        target_dir = self._track_folder.parent / track_stem
        target_dir.mkdir(parents=True, exist_ok=True)
        self._track_folder = target_dir

        # Download track
        track_info = await downloader.download(url, target_dir)

        # Validate duration
        duration = await self._probe_audio_duration(track_info.local_path)
        if duration is None or duration < 60:
            raise RuntimeError(
                "Полученный трек не является музыкальной композицией "
                "(длительность менее 1 минуты или не удалось определить длительность)."
            )

        # Try to fetch lyrics/LRC from Yandex Music
        try:
            lyrics_result = await downloader.fetch_lyrics(track_info.track_id)
            stem = track_info.track_stem
            if lyrics_result.lrc_text:
                lrc_file = target_dir / f"{stem}_lyrics.txt"
                lrc_file.write_text(lyrics_result.lrc_text, encoding="utf-8")
                self._state.source_lyrics_file = str(lrc_file)
                logger.info("Saved LRC lyrics for track_id=%s to %s", self._state.track_id, lrc_file)
            elif lyrics_result.plain_text:
                txt_file = target_dir / f"{stem}_lyrics.txt"
                txt_file.write_text(lyrics_result.plain_text, encoding="utf-8")
                self._state.source_lyrics_file = str(txt_file)
                logger.info("Saved plain text lyrics for track_id=%s to %s", self._state.track_id, txt_file)
        except Exception as exc:
            logger.warning(
                "Failed to fetch lyrics from Yandex Music for track_id=%s: %s",
                self._state.track_id,
                exc,
            )

        self._state.track_file_name = track_info.local_path.name
        self._state.track_source = str(track_info.local_path)
        self._state.track_stem = track_info.track_stem
        self._save_state()

        logger.info(
            "DOWNLOAD (yandex_music) completed for track_id=%s: file='%s'",
            self._state.track_id,
            track_info.local_path,
        )

    async def _download_youtube(self, url: str) -> None:
        """Download audio from YouTube."""
        from .youtube_downloader import YouTubeDownloader

        downloader = YouTubeDownloader(quality=self._settings.youtube_download_quality)

        # Get video metadata first
        meta = await downloader.get_track_info(url)

        # Validate duration
        if meta.duration < 60:
            raise RuntimeError(
                "Полученное видео не является музыкальной композицией "
                "(длительность менее 1 минуты)."
            )

        # Build track name
        track_stem = normalize_filename(
            f"{meta.artist} - {meta.title}" if meta.artist and meta.title
            else meta.title or "video"
        )

        # Ensure track folder matches track_stem
        target_dir = self._track_folder.parent / track_stem
        target_dir.mkdir(parents=True, exist_ok=True)
        self._track_folder = target_dir

        # Download audio
        track_info = await downloader.download(url, target_dir)

        # Verify duration using ffprobe
        duration = await self._probe_audio_duration(track_info.local_path)
        if duration is None or duration < 60:
            raise RuntimeError(
                "Полученный аудиофайл не является музыкальной композицией "
                "(длительность менее 1 минуты или не удалось определить длительность)."
            )

        self._state.track_file_name = track_info.local_path.name
        self._state.track_source = str(track_info.local_path)
        self._state.track_stem = track_info.track_stem
        self._save_state()

        logger.info(
            "DOWNLOAD (youtube) completed for track_id=%s: file='%s'",
            self._state.track_id,
            track_info.local_path,
        )

    async def _download_http_url(self, url: str) -> None:
        """Download file from arbitrary HTTP(S) URL."""
        from urllib.parse import unquote, urlparse

        parsed = urlparse(url)
        url_basename = unquote(parsed.path.rstrip("/").split("/")[-1]) if parsed.path.rstrip("/") else ""
        filename = normalize_filename(url_basename) if url_basename else "source_file"
        track_stem = normalize_filename(Path(url_basename).stem) if url_basename else "source_file"

        # Ensure track folder matches track_stem
        target_dir = self._track_folder.parent / track_stem
        target_dir.mkdir(parents=True, exist_ok=True)
        self._track_folder = target_dir

        local_path = target_dir / filename

        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with local_path.open("wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)

        # Validate duration
        duration = await self._probe_audio_duration(local_path)
        if duration is None or duration < 60:
            raise RuntimeError(
                f'Полученный файл "{filename}" не является музыкальной композицией '
                "(длительность менее 1 минуты или не удалось определить длительность)."
            )

        self._state.track_file_name = local_path.name
        self._state.track_source = str(local_path)
        self._state.track_stem = track_stem
        self._save_state()

        logger.info(
            "DOWNLOAD (http_url) completed for track_id=%s: file='%s'",
            self._state.track_id,
            local_path,
        )

    async def _use_local_file(self, file_path_str: str) -> None:
        """Use an existing local file as track source."""
        file_path = Path(file_path_str)

        if not file_path.exists():
            raise RuntimeError(f"Локальный файл не найден: {file_path}")

        track_stem = normalize_filename(file_path.stem)

        # Ensure track folder matches track_stem
        target_dir = self._track_folder.parent / track_stem
        target_dir.mkdir(parents=True, exist_ok=True)

        # Copy file to target dir if not already there
        target_file = target_dir / file_path.name
        if file_path != target_file and not target_file.exists():
            shutil.copy2(file_path, target_file)
            logger.info("Copied local file from %s to %s", file_path, target_file)
        elif target_file.exists():
            pass  # Already in place
        else:
            target_file = file_path  # Use original path

        self._track_folder = target_dir

        self._state.track_file_name = target_file.name
        self._state.track_source = str(target_file)
        self._state.track_stem = track_stem
        self._save_state()

        logger.info(
            "DOWNLOAD (local_file) completed for track_id=%s: file='%s'",
            self._state.track_id,
            target_file,
        )

    async def _probe_audio_duration(self, path: Path) -> float | None:
        """Probe audio file duration using ffprobe."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.error("Failed to start ffprobe for %s: %s", path, exc)
            return None

        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None

        try:
            payload = json.loads(stdout.decode("utf-8"))
            fmt = payload.get("format") or {}
            duration_raw = fmt.get("duration")
            if duration_raw is not None:
                return float(duration_raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return None

    # ------------------------------------------------------------------
    # Step: ASK_LANGUAGE — запрос языка у пользователя
    # ------------------------------------------------------------------

    async def _step_ask_language(self) -> None:
        """Ask user to select song language. Pauses pipeline until user responds."""
        # Skip if language already set
        if self._state.lang:
            logger.info(
                "ASK_LANGUAGE step skipped for track_id=%s: lang already set to '%s'",
                self._state.track_id,
                self._state.lang,
            )
            return

        # Pause pipeline — handler will resume after user selects language
        logger.info(
            "ASK_LANGUAGE step: pausing pipeline for track_id=%s to wait for language selection",
            self._state.track_id,
        )
        raise WaitingForInputError("Ожидание выбора языка от пользователя")

    # ------------------------------------------------------------------
    # Step: GET_LYRICS
    # ------------------------------------------------------------------

    async def _step_get_lyrics(self) -> None:
        # Проверяем, есть ли уже файл с текстом песни в state
        existing_lyrics_file = self._state.source_lyrics_file
        if existing_lyrics_file:
            lyrics_path = Path(existing_lyrics_file)
            if lyrics_path.exists() and lyrics_path.stat().st_size > 0:
                logger.info(
                    "GET_LYRICS step skipped for track_id=%s: lyrics loaded from existing file '%s'",
                    self._state.track_id,
                    lyrics_path,
                )
                self._state.source_lyrics_file = str(lyrics_path)
                self._save_state()
                return

        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder

        lyrics_service = LyricsService(
            genius_token=self._settings.genius_token,
            enable_genius=self._settings.lyrics_enable_genius,
            enable_lyrica=self._settings.lyrics_enable_lyrica,
            enable_lyricslib=self._settings.lyrics_enable_lyricslib,
            lyrica_base_url=self._settings.lyrica_base_url,
        )
        lyrics = await lyrics_service.find_lyrics(
            track_stem=stem,
            track_file_name=self._state.track_file_name,
        )

        if lyrics is None:
            raise LyricsNotFoundError(
                f"Не удалось автоматически найти текст для трека '{stem}'"
            )

        lyrics_file = track_dir / f"{stem}_lyrics.txt"
        lyrics_file.write_text(lyrics, encoding="utf-8")

        self._state.source_lyrics_file = str(lyrics_file)
        self._save_state()
        logger.info(
            "GET_LYRICS step completed for track_id=%s: lyrics saved to '%s'",
            self._state.track_id,
            lyrics_file,
        )

    # ------------------------------------------------------------------
    # Step: SEPARATE
    # ------------------------------------------------------------------

    async def _step_separate(self) -> None:
        audio_path = self._state.track_source
        if not audio_path:
            raise RuntimeError("track_source не задан — шаг DOWNLOAD не был выполнен")

        track_dir = str(self._track_folder)

        vocals_path, accompaniment_path = await self._demucs_service.separate(
            audio_path=audio_path,
            track_dir=track_dir,
        )

        self._state.vocal_file = vocals_path
        self._state.instrumental_file = accompaniment_path

        logger.info(
            "SEPARATE step completed for track_id=%s: vocals='%s', accompaniment='%s'",
            self._state.track_id,
            vocals_path,
            accompaniment_path,
        )
        self._save_state()

    # ------------------------------------------------------------------
    # Step: DETECT_CHORUS — определение припевов и формирование volume_segments
    # ------------------------------------------------------------------

    async def _step_detect_chorus(self) -> None:
        """Detect chorus segments and build volume_segments_file."""
        # Check if step is enabled in config
        if not self._settings.detect_chorus_enabled:
            logger.info(
                "DETECT_CHORUS step skipped (DETECT_CHORUS_ENABLED=false in config) for track_id=%s",
                self._state.track_id,
            )
            return

        full_file_str = self._state.track_source
        if not full_file_str:
            raise RuntimeError("track_source не задан — шаг DOWNLOAD не был выполнен")

        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder

        # Step 1: Detect segments via ChorusDetector (dual_file or single_file mode).
        vocal_file_str = self._state.vocal_file
        mode = "dual_file" if vocal_file_str else "single_file"
        logger.info(
            "DETECT_CHORUS step: detecting segments for track_id=%s (mode=%s)",
            self._state.track_id,
            mode,
        )
        detector = ChorusDetector(
            min_duration_sec=self._settings.chorus_min_duration_sec,
            vocal_silence_threshold=self._settings.chorus_vocal_silence_threshold,
            boundary_merge_tolerance_sec=self._settings.chorus_boundary_merge_tolerance_sec,
            chorus_volume=self._settings.chorus_backvocal_volume,
            default_volume=self._settings.audio_mix_voice_volume,
        )
        segment_infos = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: detector.detect(
                full_file_str,
                vocal_file=vocal_file_str,
            ),
        )
        
        # Step 2: Probe audio duration
        audio_duration = await self._probe_audio_duration(Path(full_file_str))
        if audio_duration is None:
            audio_duration = 0.0

        # Step 3: Build volume segments
        volume_segments = build_volume_segments(
            chorus_segments=[],
            audio_duration=audio_duration,
            chorus_volume=self._settings.chorus_backvocal_volume,
            default_volume=self._settings.audio_mix_voice_volume,
            segment_infos=segment_infos,
        )
        
        # Step 4: Merge short segments (используем метод detector)
        volume_segments = detector.merge_segments(
            segments=volume_segments,
            should_merge=detector.should_merge_short,
        )

        # Save to JSON
        volume_segments_file = track_dir / f"{stem}_volume_segments.json"
        save_volume_segments(volume_segments, volume_segments_file)
        self._state.volume_segments_file = str(volume_segments_file)

        self._save_state()

    # ------------------------------------------------------------------
    # Step: MIX_AUDIO — обработка вокала с эффектом бэк-вокала
    # ------------------------------------------------------------------

    async def _step_mix_audio(self) -> None:
        """Apply back-vocal effect using volume_segments_file.

        Steps:
        1. Load volume_segments from volume_segments_file (built in DETECT_CHORUS step).
        2. Create segment_groups by grouping volume_segments by type.
        3. Apply grouped segments to vocal AND mix with instrumental in ONE ffmpeg pass.
        4. Create supressedvocal_mix_file: instrumental + raw vocal (with fixed volume).
        """
        # Check if step is enabled in config
        if not self._settings.mix_audio_enabled:
            logger.info(
                "MIX_AUDIO step skipped (disabled in config) for track_id=%s",
                self._state.track_id,
            )
            return

        vocal_file_str = self._state.vocal_file
        instrumental_file_str = self._state.instrumental_file
        volume_segments_file_str = self._state.volume_segments_file
        if not vocal_file_str:
            raise RuntimeError("vocal_file не задан — шаг SEPARATE не был выполнен")
        if not instrumental_file_str:
            raise RuntimeError("instrumental_file не задан — шаг SEPARATE не был выполнен")
        if not volume_segments_file_str:
            raise RuntimeError(
                "volume_segments_file не задан — шаг DETECT_CHORUS не был выполнен"
            )

        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder

        # Step 1: Load volume segments from file
        volume_segments = load_volume_segments(Path(volume_segments_file_str))
        logger.info(
            "MIX_AUDIO step: loaded %d volume segment(s) from '%s' for track_id=%s",
            len(volume_segments),
            volume_segments_file_str,
            self._state.track_id,
        )

        # Step 2: Create segment groups by grouping volume_segments by type
        # This creates segment_groups_file for use in GENERATE_ASS step
        detector = ChorusDetector(
            chorus_volume=self._settings.chorus_backvocal_volume,
            default_volume=self._settings.audio_mix_voice_volume,
        )
        segment_groups = detector.merge_segments(
            segments=volume_segments,
            should_merge=should_merge_same_type,
        )
        segment_groups_path = track_dir / f"{stem}_segment_groups.json"
        save_volume_segments(segment_groups, segment_groups_path)
        self._state.segment_groups_file = str(segment_groups_path)
        self._save_state()
        logger.info(
            "MIX_AUDIO step: created %d segment group(s) in '%s' for track_id=%s",
            len(segment_groups),
            segment_groups_path,
            self._state.track_id,
        )

        # Step 3: Apply grouped segments to vocal AND mix with instrumental in one pass
        # Use segment_groups (grouped by type) for correct volume levels per segment type
        backvocal_mix_file = track_dir / f"{stem}_backvocal_mix.mp3"
        processor = VocalProcessor(
            reverb_enabled=self._settings.vocal_reverb_enabled,
            echo_enabled=self._settings.vocal_echo_enabled,
            mix_voice_volume=self._settings.audio_mix_voice_volume,
        )
        await processor.process_and_mix(
            instrumental_file=instrumental_file_str,
            vocal_file=vocal_file_str,
            volume_segments=segment_groups,  # Use grouped segments with correct volume levels
            output_file=str(backvocal_mix_file),
        )
        self._state.backvocal_mix_file = str(backvocal_mix_file)

        # Step 3: Create supressedvocal_mix_file: instrumental + raw vocal (with fixed volume)
        # This is used for the 3rd audio track in the video (Instrumental+Voice mix)
        supressedvocal_mix_file = track_dir / f"{stem}_supressedvocal_mix.mp3"
        await processor.mix_instrumental_and_vocal_fixed_volume(
            instrumental_path=Path(instrumental_file_str),
            vocal_path=Path(vocal_file_str),  # Use raw vocal, not processed
            output_path=supressedvocal_mix_file,
        )
        self._state.supressedvocal_mix = str(supressedvocal_mix_file)

        self._save_state()
        logger.info(
            "MIX_AUDIO step completed for track_id=%s: "
            "backvocal_mix='%s', supressedvocal_mix='%s'",
            self._state.track_id,
            backvocal_mix_file,
            supressedvocal_mix_file,
        )

    # ------------------------------------------------------------------
    # Step: TRANSCRIBE
    # ------------------------------------------------------------------

    async def _step_transcribe(self) -> None:
        vocal_file_str = self._state.vocal_file
        if not vocal_file_str:
            raise RuntimeError("vocal_file не задан — шаг SEPARATE не был выполнен")

        vocal_file = Path(vocal_file_str)
        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder
        output_json = track_dir / f"{stem}_transcription.json"

        # Передаём язык из состояния (выбранный пользователем) или None (SpeechesClient использует lang_default)
        await self._speeches_client.transcribe(
            vocal_file=vocal_file,
            output_json=output_json,
            language=self._state.lang,
        )

        self._state.transcribe_json_file = str(output_json)

        # Clean up the transcription file to remove unnecessary information
        self._cleanup_transcription(output_json)

        self._save_state()

    # ------------------------------------------------------------------
    # Step: GENERATE_LYRICS
    # ------------------------------------------------------------------

    async def _step_generate_lyrics(self) -> None:
        """Генерирует текст из транскрипции и ждёт подтверждения пользователя.

        Шаг выполняется только если:
        - use_transcription_as_lyrics=True
        - source_lyrics_file ещё не установлен или файл пустой/не существует
        """
        # Пропуск если флаг не установлен
        if not self._state.use_transcription_as_lyrics:
            logger.info("GENERATE_LYRICS skipped: flag not set")
            return

        # Пропуск если текст уже есть (проверяем реальное наличие файла > 100 байт)
        if self._state.source_lyrics_file:
            lyrics_path = Path(self._state.source_lyrics_file)
            if lyrics_path.exists() and lyrics_path.stat().st_size > 100:
                logger.info("GENERATE_LYRICS skipped: lyrics already exists (%s, %d bytes)",
                           lyrics_path, lyrics_path.stat().st_size)
                return
            else:
                logger.info("GENERATE_LYRICS: source_lyrics_file set but file missing or too small, proceeding")

        # Проверка наличия транскрипции
        if not self._state.transcribe_json_file:
            raise RuntimeError("transcribe_json_file не задан")

        # Генерация текста
        lyrics = LyricsService.generate_lyrics_from_transcription(
            Path(self._state.transcribe_json_file)
        )

        if not lyrics:
            raise RuntimeError("Не удалось сгенерировать текст из транскрипции")

        # Сохранение во временный файл
        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder
        temp_lyrics_file = track_dir / f"{stem}_lyrics_temp.txt"
        temp_lyrics_file.write_text(lyrics, encoding="utf-8")

        # Сохраняем путь к временному файлу в состоянии
        self._state.temp_lyrics_file = str(temp_lyrics_file)
        self._save_state()

        # Ожидание подтверждения пользователя
        raise WaitingForInputError("Ожидание подтверждения текста из транскрипции")

    # ------------------------------------------------------------------
    # Step: CORRECT_TRANSCRIPT
    # ------------------------------------------------------------------

    async def _step_correct_transcribe(self) -> None:
        # Check if step is enabled in config
        if not self._settings.correct_transcript_enabled:
            logger.info(
                "CORRECT_TRANSCRIPT step skipped (disabled in config) for track_id=%s",
                self._state.track_id,
            )
            return

        # Check if we have API key
        if not self._settings.openrouter_api_key:
            logger.warning(
                "CORRECT_TRANSCRIPT step skipped (no API key) for track_id=%s",
                self._state.track_id,
            )
            return

        transcribe_path = self._state.transcribe_json_file
        lyrics_path = self._state.source_lyrics_file

        if not transcribe_path:
            raise RuntimeError(
                "transcribe_json_file не задан — шаг TRANSCRIBE не был выполнен"
            )
        if not lyrics_path:
            raise RuntimeError(
                "source_lyrics_file не задан — шаг GET_LYRICS не был выполнен"
            )

        # Create LLM client
        llm_client = LLMClient(
            api_key=self._settings.openrouter_api_key,
            model=self._settings.openrouter_model,
            api_url=self._settings.openrouter_api_url,
        )

        try:
            # Create correction service
            correct_service = CorrectTranscriptService(llm_client=llm_client)

            # Perform correction
            corrected_data = await correct_service.correct_transcript(
                transcription_json_path=Path(transcribe_path),
                lyrics_path=Path(lyrics_path),
            )

            # Save corrected transcription
            raw_stem = self._state.track_stem or "track"
            stem = normalize_filename(raw_stem)
            track_dir = self._track_folder
            output_json = track_dir / f"{stem}_transcription_corrected.json"

            output_json.write_text(
                json.dumps(corrected_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            self._state.corrected_transcribe_json_file = str(output_json)
            self._save_state()

            logger.info(
                "CORRECT_TRANSCRIPT step completed for track_id=%s: corrected_file='%s'",
                self._state.track_id,
                output_json,
            )
        finally:
            await llm_client.close()

    # ------------------------------------------------------------------
    # Step: ALIGN
    # ------------------------------------------------------------------

    async def _step_align(self) -> None:
        # Use corrected transcription if available and exists, otherwise use original
        if self._state.corrected_transcribe_json_file and Path(self._state.corrected_transcribe_json_file).exists():
            transcribe_path = self._state.corrected_transcribe_json_file
            logger.info(
                "ALIGN step: using corrected transcription '%s'",
                transcribe_path,
            )
        else:
            transcribe_path = self._state.transcribe_json_file
            logger.info(
                "ALIGN step: using original transcription '%s'",
                transcribe_path,
            )
        lyrics_path = self._state.source_lyrics_file
        if not transcribe_path:
            raise RuntimeError("transcribe_json_file не задан — шаг TRANSCRIBE не был выполнен")
        if not lyrics_path:
            raise RuntimeError("source_lyrics_file не задан — шаг GET_LYRICS не был выполнен")

        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder
        output_path = track_dir / f"{stem}.aligned.json"

        vocal_file = Path(self._state.vocal_file) if self._state.vocal_file else None

        alignment_service = AlignmentService()
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: alignment_service.align_timestamps(
                transcription_json_path=Path(transcribe_path),
                source_lyrics_path=Path(lyrics_path),
                audio_file=vocal_file,
                max_word_time=self._settings.max_word_time,
                normal_word_time=self._settings.normal_word_time,
            ),
        )
        save_aligned_result(result, output_path)

        self._state.aligned_lyrics_file = str(output_path)
        self._save_state()
        logger.info(
            "ALIGN step completed for track_id=%s: aligned_lyrics saved to '%s'",
            self._state.track_id,
            output_path,
        )

    # ------------------------------------------------------------------
    # Step: GENERATE_ASS
    # ------------------------------------------------------------------

    async def _step_generate_ass(self) -> None:
        aligned_path_str = self._state.aligned_lyrics_file
        if not aligned_path_str:
            raise RuntimeError("aligned_lyrics_file не задан — шаг ALIGN не был выполнен")

        aligned_path = Path(aligned_path_str)
        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder
        output_ass = track_dir / f"{stem}.ass"

        track_title = stem.replace("_", " ")

        # Use segment_groups_file created in MIX_AUDIO step (required artifact)
        segment_groups_path = Path(self._state.segment_groups_file)
        if not segment_groups_path.exists():
            raise RuntimeError(
                f"segment_groups_file не найден: {segment_groups_path} — шаг MIX_AUDIO не был выполнен"
            )
        
        # Fallback volume_segments_path for backward compatibility
        volume_segments_path: Path | None = None
        if self._state.volume_segments_file:
            vsp = Path(self._state.volume_segments_file)
            if vsp.exists():
                volume_segments_path = vsp

        generator = AssGenerator(
            font_size=self._settings.ass_font_size,
            countdown_enabled=self._settings.ass_countdown_enabled,
            countdown_seconds=self._settings.ass_countdown_seconds,
        )
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generator.generate(
                aligned_json_path=aligned_path,
                output_ass_path=output_ass,
                track_title=track_title,
                volume_segments_path=segment_groups_path,
            ),
        )

        self._state.ass_file = str(output_ass)
        self._save_state()
        logger.info(
            "GENERATE_ASS step completed for track_id=%s: ass_file='%s'",
            self._state.track_id,
            output_ass,
        )

        # Опциональная визуализация сегментов
        if self._settings.track_visualization_enabled:
            from app.track_visualizer import TrackVisualizer

            viz_path = track_dir / f"{stem}_timeline.png"
            visualizer = TrackVisualizer()
            try:
                visualizer.generate(
                    output_path=viz_path,
                    transcribe_json_file=(
                        Path(self._state.transcribe_json_file)
                        if self._state.transcribe_json_file else None
                    ),
                    corrected_transcribe_json_file=(
                        Path(self._state.corrected_transcribe_json_file)
                        if self._state.corrected_transcribe_json_file else None
                    ),
                    aligned_lyrics_file=Path(aligned_path),
                    source_lyrics_file=(
                        Path(self._state.source_lyrics_file)
                        if self._state.source_lyrics_file else None
                    ),
                    # Use segment_groups_file from MIX_AUDIO step
                    volume_segments_file=segment_groups_path,
                    track_title=self._state.track_stem or "",
                )
                self._state.visualization_file = str(viz_path)
                self._save_state()
                logger.info(
                    "TrackVisualizer: saved timeline to '%s'", viz_path
                )
            except Exception as exc:
                logger.warning(
                    "TrackVisualizer: failed to generate visualization: %s", exc
                )

    # ------------------------------------------------------------------
    # Step: RENDER_VIDEO
    # ------------------------------------------------------------------

    async def _step_render_video(self) -> None:
        ass_file_str = self._state.ass_file
        if not ass_file_str:
            raise RuntimeError("ass_file не задан — шаг GENERATE_ASS не был выполнен")

        instrumental_file_str = self._state.instrumental_file
        if not instrumental_file_str:
            raise RuntimeError("instrumental_file не задан — шаг SEPARATE не был выполнен")

        track_source_str = self._state.track_source
        if not track_source_str:
            raise RuntimeError("track_source не задан — шаг DOWNLOAD не был выполнен")

        vocal_file_str = self._state.vocal_file
        if not vocal_file_str:
            raise RuntimeError("vocal_file не задан — шаг SEPARATE не был выполнен")

        ass_path = Path(ass_file_str)
        instrumental_path = Path(instrumental_file_str)
        original_path = Path(track_source_str)
        vocal_path = Path(vocal_file_str)
        raw_stem = self._state.track_stem or "track"
        stem = normalize_filename(raw_stem)
        track_dir = self._track_folder
        output_path = track_dir / f"{stem}.mp4"

        renderer = VideoRenderer(
            width=self._settings.video_width,
            height=self._settings.video_height,
            background_color=self._settings.video_background_color,
            ffmpeg_preset=self._settings.video_ffmpeg_preset,
            ffmpeg_crf=self._settings.video_ffmpeg_crf,
            mix_voice_volume=self._settings.audio_mix_voice_volume,
        )

        # Pass backvocal_mix_path if available (from MIX_AUDIO step)
        backvocal_mix_path: Path | None = None
        if self._state.backvocal_mix_file:
            candidate = Path(self._state.backvocal_mix_file)
            if candidate.exists():
                backvocal_mix_path = candidate
                logger.info(
                    "RENDER_VIDEO step: using backvocal_mix_file='%s' for 4th audio track",
                    backvocal_mix_path,
                )
            else:
                logger.warning(
                    "RENDER_VIDEO step: backvocal_mix_file set but not found: '%s'",
                    self._state.backvocal_mix_file,
                )

        # Pass supressedvocal_mix_path if available (from MIX_AUDIO step)
        supressedvocal_mix_path: Path | None = None
        if self._state.supressedvocal_mix:
            candidate = Path(self._state.supressedvocal_mix)
            if candidate.exists():
                supressedvocal_mix_path = candidate
                logger.info(
                    "RENDER_VIDEO step: using supressedvocal_mix='%s' for 3rd audio track",
                    supressedvocal_mix_path,
                )
            else:
                logger.warning(
                    "RENDER_VIDEO step: supressedvocal_mix set but not found: '%s'",
                    self._state.supressedvocal_mix,
                )

        await renderer.render(
            instrumental_path=instrumental_path,
            original_path=original_path,
            vocal_path=vocal_path,
            ass_path=ass_path,
            output_path=output_path,
            backvocal_mix_path=backvocal_mix_path,
            supressedvocal_mix_path=supressedvocal_mix_path,
        )

        self._state.output_file = str(output_path)

        # Формируем ссылку на скачивание, если задан CONTENT_EXTERNAL_URL
        if self._settings.content_external_url:
            base_url = self._settings.content_external_url
            if not base_url.startswith("http://") and not base_url.startswith("https://"):
                base_url = f"https://{base_url}"

            base_url = base_url.rstrip("/")

            if base_url.endswith("/music"):
                endpoint = ""
            else:
                endpoint = "/music"

            track_dir_name = stem
            output_filename = output_path.name
            filepath = f"{track_dir_name}/{output_filename}"
            encoded_path = quote(filepath, safe="/")
            self._state.download_url = (
                f"{base_url}{endpoint}?getfile={encoded_path}"
            )
            logger.info(
                "RENDER_VIDEO step: download URL formed for track_id=%s: %s",
                self._state.track_id,
                self._state.download_url,
            )

        self._save_state()
        logger.info(
            "RENDER_VIDEO step completed for track_id=%s: output_file='%s', download_url=%s",
            self._state.track_id,
            output_path,
            self._state.download_url,
        )

    # ------------------------------------------------------------------
    # Step: SEND_VIDEO — отправка результата пользователю
    # ------------------------------------------------------------------

    async def _step_send_video(self) -> None:
        """Send the rendered video to the user via Telegram bot."""
        output_file_str = self._state.output_file
        if not output_file_str:
            raise RuntimeError("output_file не задан — шаг RENDER_VIDEO не был выполнен")

        if not self._settings.send_video_to_user:
            logger.info(
                "SEND_VIDEO step skipped (disabled in config) for track_id=%s. "
                "Video available at: %s",
                self._state.track_id,
                output_file_str,
            )
            return

        if not self._bot:
            logger.warning(
                "SEND_VIDEO step: bot instance not provided for track_id=%s, skipping send",
                self._state.track_id,
            )
            return

        user_id = self._state.user_id
        if not user_id:
            logger.warning(
                "SEND_VIDEO step: user_id not set for track_id=%s, skipping send",
                self._state.track_id,
            )
            return

        output_path = Path(output_file_str)
        if not output_path.exists():
            logger.warning(
                "SEND_VIDEO step: output file not found for track_id=%s: %s",
                self._state.track_id,
                output_file_str,
            )
            return

        download_url = self._state.download_url
        caption = (
            f"🎉 Обработка завершена успешно!\n"
            f"track_id: <code>{self._state.track_id}</code>"
            + (f"\n📥 Скачать: <a href='{download_url}'>ссылка</a>" if download_url else "")
        )

        try:
            from aiogram.types import FSInputFile
            video_file = FSInputFile(output_path, filename=output_path.name)
            await self._bot.send_video(
                chat_id=user_id,
                video=video_file,
                caption=caption,
                parse_mode="HTML",
            )
            logger.info(
                "SEND_VIDEO step completed for track_id=%s: video sent to user_id=%s",
                self._state.track_id,
                user_id,
            )
        except Exception as exc:
            logger.error(
                "SEND_VIDEO step failed for track_id=%s: %s",
                self._state.track_id,
                exc,
            )
            raise

        # Отправка визуализации timeline, если она была создана
        visualization_file_str = self._state.visualization_file
        if visualization_file_str:
            viz_path = Path(visualization_file_str)
            if viz_path.exists():
                try:
                    from aiogram.types import FSInputFile
                    viz_file = FSInputFile(viz_path, filename=viz_path.name)
                    await self._bot.send_photo(
                        chat_id=user_id,
                        photo=viz_file,
                        caption="📊 Визуализация сегментирования трека",
                    )
                    logger.info(
                        "SEND_VIDEO step: visualization sent to user_id=%s",
                        user_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "SEND_VIDEO step: failed to send visualization for track_id=%s: %s",
                        self._state.track_id,
                        exc,
                    )
            else:
                logger.warning(
                    "SEND_VIDEO step: visualization file not found for track_id=%s: %s",
                    self._state.track_id,
                    visualization_file_str,
                )

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def _cleanup_tmp_folder(self, tmp_folder: Path) -> None:
        """Remove a temporary track folder (e.g. _tmp_<uuid>) after DOWNLOAD renamed it.

        Only removes folders whose name starts with '_tmp_' to avoid accidental deletion.
        """
        if not tmp_folder.name.startswith("_tmp_"):
            logger.warning(
                "Skipping cleanup of non-temporary folder '%s' for track_id=%s",
                tmp_folder,
                self._state.track_id,
            )
            return
        try:
            if tmp_folder.exists():
                shutil.rmtree(tmp_folder)
                logger.info(
                    "Removed temporary folder '%s' for track_id=%s",
                    tmp_folder,
                    self._state.track_id,
                )
        except OSError as exc:
            logger.warning(
                "Failed to remove temporary folder '%s' for track_id=%s: %s",
                tmp_folder,
                self._state.track_id,
                exc,
            )

    def _cleanup_transcription(self, transcription_path: Path) -> None:
        """Clean up transcription JSON to remove unnecessary information.

        Keeps only required fields: duration, language, segments, words
        Segments are cleaned to keep only id, start, end, text fields.
        Words section is kept as is without changes.
        """
        with open(transcription_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        cleaned_data = {
            "duration": data.get("duration"),
            "language": data.get("language"),
            "segments": [],
            "words": data.get("words", [])
        }

        for segment in data.get("segments", []):
            cleaned_segment = {
                "id": segment.get("id"),
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": segment.get("text")
            }
            cleaned_data["segments"].append(cleaned_segment)

        with open(transcription_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

        logger.info(
            "Transcription cleaned up for track_id=%s: kept only required fields",
            self._state.track_id
        )

    @property
    def state(self) -> PipelineState:
        """Access the current pipeline state."""
        return self._state

    @property
    def track_folder(self) -> Path:
        """Access the current track folder."""
        return self._track_folder
