import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .alignment_service import AlignmentService, save_aligned_result
from .ass_generator import AssGenerator
from .config import Settings
from .correct_transcript_service import CorrectTranscriptService
from .demucs_service import DemucsService
from .llm_client import LLMClient
from .lyrics_service import LyricsService
from .speeches_client import SpeechesClient
from .video_renderer import VideoRenderer
from .models import (
    PipelineResult,
    PipelineState,
    PipelineStatus,
    PipelineStep,
    UserRequest,
)


class LyricsNotFoundError(Exception):
    """Raised when lyrics cannot be found automatically."""
    pass

logger = logging.getLogger(__name__)

_STEP_LABELS: dict[PipelineStep, str] = {
    PipelineStep.DOWNLOAD: "скачивание",
    PipelineStep.GET_LYRICS: "получение текста",
    PipelineStep.SEPARATE: "разделение дорожек",
    PipelineStep.TRANSCRIBE: "транскрипция",
    PipelineStep.CORRECT_TRANSCRIPT: "корректировка транскрипции",
    PipelineStep.ALIGN: "выравнивание",
    PipelineStep.GENERATE_ASS: "генерация субтитров",
    PipelineStep.RENDER_VIDEO: "рендеринг видео",
}

_ORDERED_STEPS: list[PipelineStep] = [
    PipelineStep.DOWNLOAD,
    PipelineStep.GET_LYRICS,
    PipelineStep.SEPARATE,
    PipelineStep.TRANSCRIBE,
    PipelineStep.CORRECT_TRANSCRIPT,
    PipelineStep.ALIGN,
    PipelineStep.GENERATE_ASS,
    PipelineStep.RENDER_VIDEO,
]

# Required artifact fields that must be set before a given step can run.
# If a step is absent, no prerequisite artifacts are needed.
_STEP_REQUIRED_ARTIFACTS: dict[PipelineStep, list[str]] = {
    PipelineStep.GET_LYRICS: ["track_file_name", "track_stem"],
    PipelineStep.SEPARATE: ["track_source"],
    PipelineStep.TRANSCRIBE: ["vocal_file"],
    PipelineStep.CORRECT_TRANSCRIPT: ["transcribe_json_file", "source_lyrics_file"],
    PipelineStep.ALIGN: ["source_lyrics_file", "transcribe_json_file"],
    PipelineStep.GENERATE_ASS: ["aligned_lyrics_file"],
    PipelineStep.RENDER_VIDEO: ["ass_file", "vocal_file", "instrumental_file"],
}


class KaraokePipeline:
    def __init__(self, request: UserRequest, settings: Settings) -> None:
        self._request = request
        self._settings = settings
        self._state = PipelineState(
            track_id=request.track_id,
            user_id=request.user_id,
            status=PipelineStatus.PENDING,
        )
        demucs_output_dir = str(Path(request.track_folder).parent)
        self._demucs_service = DemucsService(
            model=settings.demucs_model,
            output_format=settings.demucs_output_format,
            output_dir=demucs_output_dir,
        )
        self._speeches_client = SpeechesClient(settings=settings)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        progress_callback: Callable[[str], Awaitable[None]],
        start_from_step: PipelineStep | None = None,
    ) -> PipelineResult:
        """Run the pipeline.

        Resolution order for the starting step:
        1. If *start_from_step* is explicitly provided — use it (after
           validating prerequisite artifacts).
        2. Else if a ``state.json`` exists for this track with
           ``status == FAILED`` — resume from ``current_step``.
        3. Otherwise — run from the very beginning (DOWNLOAD).
        """
        first_step: PipelineStep

        logger.info(
            "Pipeline.run called for track_id=%s with start_from_step=%s, request.source=%s",
            self._request.track_id,
            start_from_step,
            self._request.source_url_or_file_path,
        )

        if start_from_step is not None:
            # Mode 3: explicit start_from_step
            saved = self._load_state()
            logger.info(
                "Pipeline mode 3: explicit start_from_step=%s, saved state exists=%s",
                start_from_step,
                saved is not None,
            )
            if saved is not None:
                # Restore artefacts accumulated in previous runs
                self._state = saved
                # Always keep user_id from the current request
                self._state.user_id = self._request.user_id
                # Also restore track_source if not set in request
                if not self._state.track_source and self._request.source_url_or_file_path:
                    self._state.track_source = self._request.source_url_or_file_path
            validation_error = self._validate_artifacts_for_step(start_from_step)
            if validation_error:
                logger.error(
                    "Cannot start from step %s for track_id=%s: %s",
                    start_from_step.value,
                    self._request.track_id,
                    validation_error,
                )
                self._state.status = PipelineStatus.FAILED
                self._state.error_message = validation_error
                self._save_state()
                return PipelineResult(
                    track_id=self._request.track_id,
                    status=PipelineStatus.FAILED,
                    error_message=validation_error,
                )
            first_step = start_from_step
            logger.info(
                "Pipeline starting from explicit step %s for track_id=%s",
                first_step.value,
                self._request.track_id,
            )
        else:
            saved = self._load_state()
            if saved is not None and saved.status == PipelineStatus.FAILED and saved.current_step is not None:
                # Mode 2: resume from last failed step
                self._state = saved
                # Always keep user_id from the current request
                self._state.user_id = self._request.user_id
                first_step = saved.current_step
                logger.info(
                    "Pipeline resuming from step %s for track_id=%s",
                    first_step.value,
                    self._request.track_id,
                )
                await progress_callback(
                    f"🔄 Возобновление с шага {first_step.value}: {_STEP_LABELS[first_step]}..."
                )
            else:
                # Mode 1: fresh start — but preserve any fields already saved (e.g. lang chosen by the user)
                # Copy all available artifacts from saved state for fresh start
                if saved is not None:
                    if saved.lang is not None:
                        self._state.lang = saved.lang
                    # Copy all artifact paths that exist in saved state
                    if saved.source_lyrics_file is not None:
                        self._state.source_lyrics_file = saved.source_lyrics_file
                    if saved.track_file_name is not None:
                        self._state.track_file_name = saved.track_file_name
                    if saved.track_stem is not None:
                        self._state.track_stem = saved.track_stem
                    if saved.track_source is not None:
                        self._state.track_source = saved.track_source
                    # Copy other artifacts that might exist
                    for field in ['vocal_file', 'instrumental_file', 'transcribe_json_file',
                                  'corrected_transcribe_json_file', 'aligned_lyrics_file', 'ass_file', 'output_file']:
                        saved_value = getattr(saved, field, None)
                        if saved_value is not None:
                            setattr(self._state, field, saved_value)
                first_step = PipelineStep.DOWNLOAD
                logger.info(
                    "Pipeline starting fresh for track_id=%s (lang=%s)",
                    self._request.track_id,
                    self._state.lang,
                )

        return await self._execute_from(first_step, progress_callback)

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

        step_methods: dict[PipelineStep, Callable[[], Awaitable[None]]] = {
            PipelineStep.DOWNLOAD: self._step_download,
            PipelineStep.GET_LYRICS: self._step_get_lyrics,
            PipelineStep.SEPARATE: self._step_separate,
            PipelineStep.TRANSCRIBE: self._step_transcribe,
            PipelineStep.CORRECT_TRANSCRIPT: self._step_correct_transcribe,
            PipelineStep.ALIGN: self._step_align,
            PipelineStep.GENERATE_ASS: self._step_generate_ass,
            PipelineStep.RENDER_VIDEO: self._step_render_video,
        }

        start_index = _ORDERED_STEPS.index(first_step)
        steps_to_run = _ORDERED_STEPS[start_index:]

        for step in steps_to_run:
            label = _STEP_LABELS[step]
            self._state.current_step = step
            self._state.status = PipelineStatus.IN_PROGRESS
            self._save_state()

            logger.info("Step %s started for track_id=%s", step.value, self._request.track_id)
            await progress_callback(f"⏳ Шаг {step.value}: {label}...")

            try:
                await step_methods[step]()
            except LyricsNotFoundError:
                # Let this propagate — the handler will request lyrics from the user.
                self._state.status = PipelineStatus.FAILED
                self._state.error_message = "Требуется ручной ввод текста песни"
                self._save_state()
                raise
            except Exception as exc:
                error_msg = f"Ошибка на шаге {step.value}: {exc}"
                logger.error(
                    "Step %s failed for track_id=%s: %s",
                    step.value,
                    self._request.track_id,
                    exc,
                )
                self._state.status = PipelineStatus.FAILED
                self._state.error_message = error_msg
                self._save_state()
                await progress_callback(f"❌ Шаг {step.value} завершился с ошибкой: {exc}")
                return PipelineResult(
                    track_id=self._request.track_id,
                    status=PipelineStatus.FAILED,
                    error_message=error_msg,
                )

            logger.info("Step %s completed for track_id=%s", step.value, self._request.track_id)
            await progress_callback(f"✅ Шаг {step.value} завершён")

        self._state.status = PipelineStatus.COMPLETED
        self._state.error_message = None
        self._save_state()
        logger.info("Pipeline completed for track_id=%s", self._request.track_id)

        return PipelineResult(
            track_id=self._request.track_id,
            status=PipelineStatus.COMPLETED,
            final_video_path=self._state.output_file,
        )

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        state_path = Path(self._request.track_folder) / "state.json"
        try:
            state_path.write_text(self._state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "Failed to save state.json for track_id=%s: %s",
                self._request.track_id,
                exc,
            )

    def _load_state(self) -> PipelineState | None:
        state_path = Path(self._request.track_folder) / "state.json"
        if not state_path.exists():
            return None
        try:
            return PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "Failed to load state.json for track_id=%s: %s",
                self._request.track_id,
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
    # Stub step methods — no real logic yet
    # ------------------------------------------------------------------

    async def _step_download(self) -> None:
        source = self._request.source_url_or_file_path
        stem = Path(source).stem
        self._state.track_source = source
        self._state.track_file_name = Path(source).name
        self._state.track_stem = stem
        self._save_state()
        await asyncio.sleep(0)

    async def _step_separate(self) -> None:
        audio_path = self._request.source_url_or_file_path
        track_dir = self._request.track_folder

        vocals_path, accompaniment_path = await self._demucs_service.separate(
            audio_path=audio_path,
            track_dir=track_dir,
        )

        self._state.vocal_file = vocals_path
        self._state.instrumental_file = accompaniment_path

        logger.info(
            "SEPARATE step completed for track_id=%s: vocals='%s', accompaniment='%s'",
            self._request.track_id,
            vocals_path,
            accompaniment_path,
        )
        self._save_state()

    async def _step_transcribe(self) -> None:
        vocal_file_str = self._state.vocal_file
        if not vocal_file_str:
            raise RuntimeError("vocal_file не задан — шаг SEPARATE не был выполнен")

        vocal_file = Path(vocal_file_str)
        stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
        track_dir = Path(self._request.track_folder)
        output_json = track_dir / f"{stem}_transcription.json"

        # Передаём язык из состояния (выбранный пользователем) или None (SpeechesClient использует lang_default)
        await self._speeches_client.transcribe(
            vocal_file=vocal_file,
            output_json=output_json,
            language=self._state.lang,
        )

        self._state.transcribe_json_file = str(output_json)
        self._save_state()

    async def _step_correct_transcribe(self) -> None:
        # Check if step is enabled in config
        if not self._settings.correct_transcript_enabled:
            logger.info(
                "CORRECT_TRANSCRIPT step skipped (disabled in config) for track_id=%s",
                self._request.track_id,
            )
            return

        # Check if we have API key
        if not self._settings.openrouter_api_key:
            logger.warning(
                "CORRECT_TRANSCRIPT step skipped (no API key) for track_id=%s",
                self._request.track_id,
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
            import json

            stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
            track_dir = Path(self._request.track_folder)
            output_json = track_dir / f"{stem}_transcription_corrected.json"

            output_json.write_text(
                json.dumps(corrected_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            self._state.corrected_transcribe_json_file = str(output_json)
            self._save_state()

            logger.info(
                "CORRECT_TRANSCRIPT step completed for track_id=%s: corrected_file='%s'",
                self._request.track_id,
                output_json,
            )
        finally:
            await llm_client.close()

    async def _step_get_lyrics(self) -> None:
        # Проверяем, есть ли уже файл с текстом песни в state
        existing_lyrics_file = self._state.source_lyrics_file
        if existing_lyrics_file:
            lyrics_path = Path(existing_lyrics_file)
            if lyrics_path.exists() and lyrics_path.stat().st_size > 0:
                lyrics = lyrics_path.read_text(encoding="utf-8")
                logger.info(
                    "GET_LYRICS step skipped for track_id=%s: lyrics loaded from existing file '%s'",
                    self._request.track_id,
                    lyrics_path,
                )
                self._state.source_lyrics_file = str(lyrics_path)
                self._save_state()
                return

        stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
        track_dir = Path(self._request.track_folder)

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
            self._request.track_id,
            lyrics_file,
        )

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

        stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
        track_dir = Path(self._request.track_folder)
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
            self._request.track_id,
            output_path,
        )

    async def _step_generate_ass(self) -> None:
        aligned_path_str = self._state.aligned_lyrics_file
        if not aligned_path_str:
            raise RuntimeError("aligned_lyrics_file не задан — шаг ALIGN не был выполнен")

        aligned_path = Path(aligned_path_str)
        stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
        track_dir = Path(self._request.track_folder)
        output_ass = track_dir / f"{stem}.ass"

        track_title = stem.replace("_", " ")

        generator = AssGenerator(font_size=self._settings.ass_font_size)
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: generator.generate(
                aligned_json_path=aligned_path,
                output_ass_path=output_ass,
                track_title=track_title,
            ),
        )

        self._state.ass_file = str(output_ass)
        self._save_state()
        logger.info(
            "GENERATE_ASS step completed for track_id=%s: ass_file='%s'",
            self._request.track_id,
            output_ass,
        )

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

        ass_path = Path(ass_file_str)
        instrumental_path = Path(instrumental_file_str)
        original_path = Path(track_source_str)
        stem = self._state.track_stem or Path(self._request.source_url_or_file_path).stem
        track_dir = Path(self._request.track_folder)
        output_path = track_dir / f"{stem}.mp4"

        renderer = VideoRenderer(
            width=self._settings.video_width,
            height=self._settings.video_height,
            background_color=self._settings.video_background_color,
            ffmpeg_preset=self._settings.video_ffmpeg_preset,
            ffmpeg_crf=self._settings.video_ffmpeg_crf,
        )

        await renderer.render(
            instrumental_path=instrumental_path,
            original_path=original_path,
            ass_path=ass_path,
            output_path=output_path,
        )

        self._state.output_file = str(output_path)
        self._save_state()
        logger.info(
            "RENDER_VIDEO step completed for track_id=%s: output_file='%s'",
            self._request.track_id,
            output_path,
        )
