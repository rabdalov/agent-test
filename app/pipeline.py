import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from .config import Settings
from .demucs_service import DemucsService
from .models import (
    PipelineResult,
    PipelineState,
    PipelineStatus,
    PipelineStep,
    UserRequest,
)

logger = logging.getLogger(__name__)

_STEP_LABELS: dict[PipelineStep, str] = {
    PipelineStep.DOWNLOAD: "скачивание",
    PipelineStep.SEPARATE: "разделение дорожек",
    PipelineStep.TRANSCRIBE: "транскрипция",
    PipelineStep.GET_LYRICS: "получение текста",
    PipelineStep.ALIGN: "выравнивание",
    PipelineStep.GENERATE_ASS: "генерация субтитров",
    PipelineStep.RENDER_VIDEO: "рендеринг видео",
}

_ORDERED_STEPS: list[PipelineStep] = [
    PipelineStep.DOWNLOAD,
    PipelineStep.SEPARATE,
    PipelineStep.TRANSCRIBE,
    PipelineStep.GET_LYRICS,
    PipelineStep.ALIGN,
    PipelineStep.GENERATE_ASS,
    PipelineStep.RENDER_VIDEO,
]


class KaraokePipeline:
    def __init__(self, request: UserRequest, settings: Settings) -> None:
        self._request = request
        self._settings = settings
        self._state = PipelineState(
            track_id=request.track_id,
            status=PipelineStatus.PENDING,
        )
        demucs_output_dir = str(Path(request.track_folder).parent )
        self._demucs_service = DemucsService(
            model=settings.demucs_model,
            output_format=settings.demucs_output_format,
            output_dir=demucs_output_dir,
        )
        self._vocals_path: str | None = None
        self._accompaniment_path: str | None = None

    async def run(
        self,
        progress_callback: Callable[[str], Awaitable[None]],
    ) -> PipelineResult:
        self._state.status = PipelineStatus.IN_PROGRESS
        self._save_state()
        logger.info("Pipeline started for track_id=%s", self._request.track_id)

        step_methods: dict[PipelineStep, Callable[[], Awaitable[None]]] = {
            PipelineStep.DOWNLOAD: self._step_download,
            PipelineStep.SEPARATE: self._step_separate,
            PipelineStep.TRANSCRIBE: self._step_transcribe,
            PipelineStep.GET_LYRICS: self._step_get_lyrics,
            PipelineStep.ALIGN: self._step_align,
            PipelineStep.GENERATE_ASS: self._step_generate_ass,
            PipelineStep.RENDER_VIDEO: self._step_render_video,
        }

        for step in _ORDERED_STEPS:
            label = _STEP_LABELS[step]
            self._state.current_step = step
            self._save_state()

            logger.info("Step %s started for track_id=%s", step.value, self._request.track_id)
            await progress_callback(f"⏳ Шаг {step.value}: {label}...")

            try:
                await step_methods[step]()
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
        self._save_state()
        logger.info("Pipeline completed for track_id=%s", self._request.track_id)

        return PipelineResult(
            track_id=self._request.track_id,
            status=PipelineStatus.COMPLETED,
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

    # ------------------------------------------------------------------
    # Stub step methods — no real logic yet
    # ------------------------------------------------------------------

    async def _step_download(self) -> None:
        await asyncio.sleep(0)

    async def _step_separate(self) -> None:
        audio_path = self._request.source_url_or_file_path
        track_dir = self._request.track_folder

        vocals_path, accompaniment_path = await self._demucs_service.separate(
            audio_path=audio_path,
            track_dir=track_dir,
        )

        self._vocals_path = vocals_path
        self._accompaniment_path = accompaniment_path

        logger.info(
            "SEPARATE step completed for track_id=%s: vocals='%s', accompaniment='%s'",
            self._request.track_id,
            vocals_path,
            accompaniment_path,
        )

    async def _step_transcribe(self) -> None:
        await asyncio.sleep(0)

    async def _step_get_lyrics(self) -> None:
        await asyncio.sleep(0)

    async def _step_align(self) -> None:
        await asyncio.sleep(0)

    async def _step_generate_ass(self) -> None:
        await asyncio.sleep(0)

    async def _step_render_video(self) -> None:
        await asyncio.sleep(0)
