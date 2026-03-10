import asyncio
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .models import LyricsStates, PipelineResult, PipelineState, PipelineStatus, PipelineStep, TrackLangStates, UserRequest
from .pipeline import KaraokePipeline, LyricsNotFoundError, _ORDERED_STEPS
from .yandex_music_downloader import YandexMusicDownloader


class KaraokeHandlers:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tracks_root_dir: Path = settings.tracks_root_dir
        self._logger = logging.getLogger(__name__)
        self.router = Router()
        self._register_handlers()

    def _is_user_allowed(self, message: types.Message) -> bool:
        """Return True if the sender's user_id is in the allowed list."""
        allowed = self._settings.tlg_allowed_id
        if not allowed:
            return True
        user_id = message.from_user.id if message.from_user else None
        return user_id in allowed

    async def _reject_unauthorized(self, message: types.Message) -> None:
        """Send a rejection notice and log the attempt."""
        user_id = message.from_user.id if message.from_user else "unknown"
        self._logger.warning("Unauthorized access attempt from user_id=%s", user_id)
        await message.answer("⛔ У вас нет доступа к этому боту.")

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def handle_start(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await state.clear()
            await message.answer(
                "Привет! Я бот для подготовки караоке-видео.\n"
                "Отправьте мне аудиофайл (mp3 или flac) или ссылку на аудиофайл (включая Я-Муззыку), "
                "и я подготовлю караоке-видео с тремя дорожками: минус, плюс, задавленный плюс."
            )

        @self.router.message(F.audio)
        async def handle_audio(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            audio = message.audio
            if audio is None:
                return
            original_name = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
            await self._handle_media_file(message, state, audio, original_name)

        @self.router.message(F.video)
        async def handle_video(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            video = message.video
            if video is None:
                return
            original_name = video.file_name or f"video_{video.file_unique_id}.mp4"
            await self._handle_media_file(message, state, video, original_name)

        @self.router.message(Command("continue"))
        async def handle_continue(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            caller_user_id = message.from_user.id if message.from_user else None
            result = self._find_latest_state(user_id=caller_user_id)
            if result is None:
                await message.answer(
                    "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                )
                return

            state_obj, track_dir = result
            track_name = track_dir.name

            if state_obj.status == PipelineStatus.COMPLETED:
                if state_obj.current_step is None:
                    await message.answer(
                        "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                    )
                    return
                current_index = _ORDERED_STEPS.index(state_obj.current_step)
                if current_index + 1 >= len(_ORDERED_STEPS):
                    await message.answer(
                        "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                    )
                    return
                next_step = _ORDERED_STEPS[current_index + 1]
                step_name = next_step.value
                await message.answer(
                    f"▶️ Продолжаю обработку со шага {step_name}...\n"
                    f"track: <code>{track_name}</code>",
                    parse_mode="HTML",
                )
                await self._run_from_step(message, track_dir, state_obj, next_step, state)

            elif state_obj.status == PipelineStatus.FAILED:
                if state_obj.current_step is None:
                    await message.answer(
                        "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                    )
                    return
                step_name = state_obj.current_step.value
                await message.answer(
                    f"🔁 Повторяю шаг {step_name}...\n"
                    f"track: <code>{track_name}</code>",
                    parse_mode="HTML",
                )
                await self._run_from_step(message, track_dir, state_obj, state_obj.current_step, state)

            else:
                await message.answer(
                    "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                )

        @self.router.message(Command("step_lyrics"))
        async def handle_step_lyrics(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.GET_LYRICS, state)

        @self.router.message(Command("step_separate"))
        async def handle_step_separate(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.SEPARATE, state)

        @self.router.message(Command("step_transcribe"))
        async def handle_step_transcribe(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.TRANSCRIBE, state)

        @self.router.message(Command("step_correct"))
        async def handle_step_correct(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.CORRECT_TRANSCRIPT, state)

        @self.router.message(Command("step_align"))
        async def handle_step_align(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.ALIGN, state)

        @self.router.message(Command("step_ass"))
        async def handle_step_ass(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.GENERATE_ASS, state)

        @self.router.message(Command("step_render"))
        async def handle_step_render(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.RENDER_VIDEO, state)

        # ----- FSM: waiting for user to supply lyrics text -----
        @self.router.message(LyricsStates.waiting_for_lyrics, F.text)
        async def handle_lyrics_input(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            lyrics_text = (message.text or "").strip()
            if not lyrics_text:
                await message.answer("Текст песни не может быть пустым. Пришли полный текст.")
                return

            data = await state.get_data()
            track_id: str | None = data.get("track_id")
            if not track_id:
                await state.clear()
                await message.answer("❌ Не удалось определить трек. Пожалуйста, начните обработку заново.")
                return

            # Find track dir by track_id via state.json files
            track_dir = self._find_track_dir_by_id(track_id)
            if track_dir is None:
                await state.clear()
                await message.answer("❌ Папка трека не найдена. Пожалуйста, начните обработку заново.")
                return

            # Read existing PipelineState to get track_stem
            state_path = track_dir / "state.json"
            try:
                pipeline_state = PipelineState.model_validate_json(
                    state_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                self._logger.error("Failed to read state.json for track_id=%s: %s", track_id, exc)
                await state.clear()
                await message.answer("❌ Не удалось прочитать состояние трека. Пожалуйста, начните заново.")
                return

            track_stem = pipeline_state.track_stem or track_dir.name

            # Save lyrics to file
            lyrics_file = track_dir / f"{track_stem}_lyrics.txt"
            try:
                lyrics_file.write_text(lyrics_text, encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to write lyrics file for track_id=%s: %s", track_id, exc)
                await state.clear()
                await message.answer("❌ Не удалось сохранить текст песни. Попробуйте ещё раз.")
                return

            # Update PipelineState
            pipeline_state.source_lyrics_file = str(lyrics_file)
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json for track_id=%s: %s", track_id, exc)

            # Clear FSM state
            await state.clear()

            await message.answer("✅ Текст песни получен. Продолжаю обработку...")

            # Continue pipeline from SEPARATE step (after GET_LYRICS)
            await self._run_from_step(message, track_dir, pipeline_state, PipelineStep.SEPARATE, state)

        # ----- FSM: waiting for user to select song language -----
        @self.router.callback_query(TrackLangStates.waiting_for_lang, F.data.startswith("lang_choice:"))
        async def handle_lang_choice(callback: types.CallbackQuery, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            # Check access by callback sender (callback.from_user), not by callback.message.from_user (which is the bot)
            allowed = self._settings.tlg_allowed_id
            caller_id = callback.from_user.id if callback.from_user else None
            if allowed and caller_id not in allowed:
                await callback.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
                return

            lang = (callback.data or "").split(":", 1)[-1]  # "ru" or "en"
            data = await state.get_data()
            track_id: str | None = data.get("track_id")
            track_folder: str | None = data.get("track_folder")

            await callback.answer()  # acknowledge button press

            if not track_id or not track_folder:
                await state.clear()
                if callback.message:
                    await callback.message.answer(  # type: ignore[union-attr]
                        "❌ Не удалось определить трек. Пожалуйста, начните обработку заново."
                    )
                return

            track_dir = Path(track_folder)

            # Persist lang into state.json so the pipeline can pick it up
            state_path = track_dir / "state.json"
            try:
                pipeline_state = PipelineState.model_validate_json(
                    state_path.read_text(encoding="utf-8")
                )
            except Exception as exc:
                self._logger.error("Failed to read state.json for track_id=%s: %s", track_id, exc)
                await state.clear()
                if callback.message:
                    await callback.message.answer(  # type: ignore[union-attr]
                        "❌ Не удалось прочитать состояние трека. Пожалуйста, начните заново."
                    )
                return

            pipeline_state.lang = lang
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json with lang for track_id=%s: %s", track_id, exc)

            await state.clear()

            lang_label = "🇷🇺 Русский" if lang == "ru" else "🇬🇧 English"
            if callback.message:
                await callback.message.edit_text(  # type: ignore[union-attr]
                    f"Язык исполнения выбран: {lang_label}\n\nЗапускаю обработку..."
                )

            # Rebuild UserRequest from saved state and run the pipeline
            source = pipeline_state.track_source or pipeline_state.track_file_name or ""
            if pipeline_state.track_file_name:
                candidate = track_dir / pipeline_state.track_file_name
                if candidate.exists():
                    source = str(candidate)

            request = UserRequest(
                user_id=pipeline_state.user_id or 0,
                track_id=track_id,
                source_type="file",
                source_url_or_file_path=source,
                track_folder=str(track_dir),
            )
            pipeline = KaraokePipeline(request, self._settings)

            # Send initial progress message and get its ID
            initial_msg = await callback.message.edit_text("⏳ Начинаю обработку...")  # type: ignore[union-attr]
            message_id = initial_msg.message_id

            async def _lang_progress(msg: str) -> None:
                # Edit the initial message instead of sending new ones
                try:
                    await callback.message.bot.edit_message_text(  # type: ignore[union-attr]
                        text=msg,
                        chat_id=callback.message.chat.id,  # type: ignore[union-attr]
                        message_id=message_id,
                    )
                except Exception as edit_err:
                    self._logger.warning("Failed to edit message: %s", edit_err)
                    if callback.message:
                        await callback.message.answer(msg)  # type: ignore[union-attr]

            try:
                result = await pipeline.run(_lang_progress)
            except LyricsNotFoundError:
                if callback.message:
                    await self._ask_for_lyrics(
                        callback.message, state, track_id, track_dir  # type: ignore[arg-type]
                    )
                return

            if result.status == PipelineStatus.COMPLETED:
                if callback.message:
                    await self._send_result_video(callback.message, result)  # type: ignore[arg-type]
            else:
                if callback.message:
                    await callback.message.answer(  # type: ignore[union-attr]
                        f"💔 Обработка завершена с ошибкой.\n"
                        f"track_id: <code>{result.track_id}</code>\n"
                        f"Причина: {result.error_message}",
                        parse_mode="HTML",
                    )

        # ----- General text handler (URLs) — must be AFTER FSM handler -----
        @self.router.message(F.text)
        async def handle_text(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            text = (message.text or "").strip()
            url = self._extract_url(text)
            if url is None:
                await message.answer(
                    "Полученное сообщение не является музыкальной композицией. "
                    "Пожалуйста, отправьте аудиофайл длительностью более 1 минуты "
                    "или ссылку на трек."
                )
                return

            if self._is_blocked_url(url):
                await message.answer(
                    "Ссылки на YouTube пока не поддерживаются. "
                    "Поддержка YouTube появится в будущих версиях бота."
                )
                return

            # Handle Yandex Music URL separately
            if self._is_yandex_music_url(url):
                await self._handle_yandex_music_url(message, state, url)
                return

            self._ensure_tracks_root()

            track_id = uuid.uuid4().hex
            parsed_url_for_name = urlparse(url)
            url_basename = unquote(parsed_url_for_name.path.rstrip("/").split("/")[-1]) if parsed_url_for_name.path.rstrip("/") else ""
            track_name = self._build_track_name(url_basename or "track", None, None)
            track_dir = self._tracks_root_dir / track_name
            track_dir.mkdir(parents=True, exist_ok=True)

            user_id: int = message.from_user.id if message.from_user else 0
            state_path = track_dir / "state.json"

            # Download the file by HTTP URL and save locally
            filename = url_basename if url_basename else "source_file"
            local_path = track_dir / filename

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with local_path.open("wb") as f:
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
            except httpx.HTTPError as exc:
                self._logger.error(
                    "Failed to download file for track %s from %s: %s", track_id, url, exc
                )
                await message.answer(
                    "Не удалось скачать файл по указанной ссылке. "
                    "Пожалуйста, проверьте ссылку и попробуйте ещё раз."
                )
                return
            except OSError as exc:
                self._logger.error(
                    "Failed to save downloaded file for track %s: %s", track_id, exc
                )
                await message.answer(
                    "Не удалось сохранить скачанный файл. "
                    "Пожалуйста, попробуйте ещё раз позже."
                )
                return

            # Save preliminary PipelineState with local file path, so language callback can find the track
            pipeline_state_url = PipelineState(
                track_id=track_id,
                user_id=user_id,
                status=PipelineStatus.PENDING,
                track_file_name=local_path.name,
                track_source=str(local_path),
                track_stem=track_name,
            )
            try:
                state_path.write_text(pipeline_state_url.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state file for track %s: %s", track_id, exc
                )

            await message.answer(
                "Файл скачан и принят.\n"
                f"track_id: <code>{track_id}</code>\n"
                f"track_name: <code>{track_name}</code>\n"
                f"Путь к файлу: <code>{local_path}</code>",
                parse_mode="HTML",
            )

            # Ask the user for the song language before starting the pipeline
            await self._ask_for_lang(message, state, track_id, str(track_dir))

        @self.router.message()
        async def handle_non_audio(message: types.Message) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await message.answer(
                "Полученное сообщение не является музыкальной композицией. "
                "Пожалуйста, отправьте аудиофайл длительностью более 1 минуты."
            )

    # ------------------------------------------------------------------
    # Language selection FSM helpers
    # ------------------------------------------------------------------

    async def _ask_for_lang(
        self,
        message: types.Message,
        state: FSMContext,
        track_id: str,
        track_folder: str,
    ) -> None:
        """Enter FSM state waiting_for_lang and send an inline keyboard to select song language."""
        await state.set_state(TrackLangStates.waiting_for_lang)
        await state.update_data(track_id=track_id, track_folder=track_folder)
        self._logger.info("Asking user for song language for track_id=%s", track_id)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_choice:ru"),
                    InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_choice:en"),
                ]
            ]
        )
        await message.answer(
            "🎵 На каком языке исполняется эта песня?\n\n"
            "Выберите язык исполнения:",
            reply_markup=keyboard,
        )

    # ------------------------------------------------------------------
    # Lyrics FSM helpers
    # ------------------------------------------------------------------

    async def _ask_for_lyrics(
        self,
        message: types.Message,
        state: FSMContext,
        track_id: str,
        track_dir: Path,
    ) -> None:
        """Transition user to FSM state waiting_for_lyrics and ask to send lyrics."""
        await state.set_state(LyricsStates.waiting_for_lyrics)
        await state.update_data(track_id=track_id)
        self._logger.info(
            "LyricsNotFoundError for track_id=%s — requesting lyrics from user", track_id
        )
        await message.answer(
            "🎵 Не удалось автоматически найти текст песни.\n\n"
            "Пожалуйста, пришли полный текст песни в следующем сообщении."
        )

    def _find_track_dir_by_id(self, track_id: str) -> Path | None:
        """Scan tracks root for a track_dir whose state.json has matching track_id."""
        if not self._tracks_root_dir.exists():
            return None
        for subdir in self._tracks_root_dir.iterdir():
            if not subdir.is_dir():
                continue
            state_path = subdir / "state.json"
            if not state_path.exists():
                continue
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                if data.get("track_id") == track_id:
                    return subdir
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------
    # Step-command helpers
    # ------------------------------------------------------------------

    def _find_latest_state(self, user_id: int | None = None) -> tuple[PipelineState, Path] | None:
        """Find the track folder with the most recently modified state.json.

        If *user_id* is given, only consider tracks belonging to that user.
        Returns a tuple of (PipelineState, track_dir) or None if not found.
        """
        best_mtime: float | None = None
        best_state: PipelineState | None = None
        best_dir: Path | None = None

        if not self._tracks_root_dir.exists():
            return None

        for subdir in self._tracks_root_dir.iterdir():
            if not subdir.is_dir():
                continue
            state_path = subdir / "state.json"
            if not state_path.exists():
                continue
            try:
                mtime = state_path.stat().st_mtime
                state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._logger.warning("Failed to read state.json in %s: %s", subdir, exc)
                continue
            # Filter by user_id when provided
            if user_id is not None and state.user_id is not None and state.user_id != user_id:
                continue
            if best_mtime is None or mtime > best_mtime:
                best_mtime = mtime
                best_state = state
                best_dir = subdir

        if best_state is None or best_dir is None:
            return None
        return best_state, best_dir

    async def _handle_step_command(
        self,
        message: types.Message,
        step: PipelineStep,
        state: FSMContext,
    ) -> None:
        """Common handler logic for /step_* commands."""
        caller_user_id = message.from_user.id if message.from_user else None
        result = self._find_latest_state(user_id=caller_user_id)
        if result is None:
            await message.answer(
                "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
            )
            return

        pipeline_state, track_dir = result
        track_name = track_dir.name
        step_name = step.value

        await message.answer(
            f"▶️ Запускаю обработку с шага {step_name}...\n"
            f"track: <code>{track_name}</code>",
            parse_mode="HTML",
        )
        await self._run_from_step(message, track_dir, pipeline_state, step, state)

    async def _run_from_step(
        self,
        message: types.Message,
        track_dir: Path,
        state: PipelineState,
        step: PipelineStep,
        fsm_context: FSMContext,
    ) -> None:
        """Reconstruct UserRequest from saved state and run the pipeline from the given step."""
        self._logger.info(
            "_run_from_step called for track_id=%s, step=%s, source_lyrics_file=%s",
            state.track_id,
            step.value,
            state.source_lyrics_file,
        )
        # Build a minimal UserRequest from persisted state data
        source = state.track_source or state.track_file_name or ""
        # Prefer the local track_file_name path inside track_dir
        if state.track_file_name:
            candidate = track_dir / state.track_file_name
            if candidate.exists():
                source = str(candidate)
            elif not source:
                source = state.track_file_name

        request = UserRequest(
            user_id=message.from_user.id if message.from_user else 0,
            track_id=state.track_id,
            source_type="file",
            source_url_or_file_path=source,
            track_folder=str(track_dir),
        )
        pipeline = KaraokePipeline(request, self._settings)

        # Send initial progress message and get its ID
        initial_msg = await message.answer("⏳ Начинаю обработку...")
        message_id = initial_msg.message_id

        async def _step_progress(msg: str) -> None:
            # Edit the initial message instead of sending new ones
            try:
                await message.bot.edit_message_text(  # type: ignore[union-attr]
                    text=msg,
                    chat_id=message.chat.id,
                    message_id=message_id,
                )
            except Exception as edit_err:
                self._logger.warning("Failed to edit message: %s", edit_err)
                await message.answer(msg)

        try:
            result = await pipeline.run(_step_progress, start_from_step=step)
        except LyricsNotFoundError:
            await self._ask_for_lyrics(message, fsm_context, state.track_id, track_dir)
            return

        if result.status == PipelineStatus.COMPLETED:
            await self._send_result_video(message, result)
        else:
            await message.answer(
                f"💔 Шаг завершён с ошибкой.\n"
                f"track_id: <code>{result.track_id}</code>\n"
                f"Причина: {result.error_message}",
                parse_mode="HTML",
            )

    # ------------------------------------------------------------------
    # Video delivery helper
    # ------------------------------------------------------------------

    async def _send_result_video(
        self,
        message: types.Message,
        result: PipelineResult,
    ) -> None:
        """Send the rendered MP4 video to the user, or a text message if unavailable."""
        video_path_str = result.final_video_path
        download_url: str | None = None

        # Try to read download_url from state.json
        if video_path_str:
            video_path = Path(video_path_str)
            state_path = video_path.parent / "state.json"
            if state_path.exists():
                try:
                    from .models import PipelineState
                    state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
                    download_url = state.download_url
                except Exception as exc:
                    self._logger.warning(
                        "Failed to read download_url from state.json for track_id=%s: %s",
                        result.track_id,
                        exc,
                    )

        if video_path_str:
            video_path = Path(video_path_str)
            if video_path.exists():
                # Check if we should send the video to the user based on configuration
                if self._settings.send_video_to_user:
                    try:
                        from aiogram.types import FSInputFile
                        video_file = FSInputFile(video_path, filename=video_path.name)
                        await message.answer_video(
                            video=video_file,
                            caption=(
                                f"🎉 Обработка завершена успешно!\n"
                                f"track_id: <code>{result.track_id}</code>"
                                + (f"\n📥 Скачать: <a href='{download_url}'>ссылка</a>" if download_url else "")
                            ),
                            parse_mode="HTML",
                        )
                        return
                    except Exception as exc:
                        self._logger.error(
                            "Failed to send video for track_id=%s: %s",
                            result.track_id,
                            exc,
                        )
                        # Fall through to text-only response
                else:
                    # Video sending is disabled, skip sending but log the fact
                    self._logger.info(
                        "Video sending is disabled via configuration. Video file is available at: %s",
                        video_path_str
                    )
            else:
                self._logger.warning(
                    "Output video file not found for track_id=%s: %s",
                    result.track_id,
                    video_path_str,
                )

        # Send completion message regardless of whether video was sent
        download_url_msg = f"\n📥 Скачать: <a href='{download_url}'>ссылка</a>" if download_url else ""
        await message.answer(
            f"🎉 Обработка завершена успешно!\n"
            f"track_id: <code>{result.track_id}</code>\n"
            f"Видеофайл: <code>{video_path_str or 'не задан'}</code>"
            f"{download_url_msg}",
            parse_mode="HTML",
        )

    def _ensure_tracks_root(self) -> None:
        self._tracks_root_dir.mkdir(parents=True, exist_ok=True)

    async def _handle_media_file(
        self,
        message: types.Message,
        state: FSMContext,
        media: Any,
        original_name: str,
    ) -> None:
        """Common handler for audio and video file messages (mp3, flac, mp4, etc.)."""
        self._ensure_tracks_root()

        tmp_dir = self._tracks_root_dir / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = tmp_dir / original_name

        await message.bot.download(media, destination=tmp_path)  # type: ignore[union-attr]
        duration, artist, title = await self._probe_audio(tmp_path)

        track_name = self._build_track_name(original_name, None, None)

        if duration is None or duration < 60:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    self._logger.warning("Failed to remove temporary file %s", tmp_path)

            await message.answer(
                f'Полученный файл "{track_name}" не является музыкальной композицией '
                "(длительность менее 1 минуты или не удалось определить длительность)."
            )
            return

        track_id = uuid.uuid4().hex
        track_dir = self._tracks_root_dir / track_name
        track_dir.mkdir(parents=True, exist_ok=True)

        # Preserve original file extension (mp3, flac, mp4, …)
        src_suffix = Path(original_name).suffix or ".mp3"
        final_path = track_dir / f"{track_name}{src_suffix}"

        try:
            shutil.move(str(tmp_path), final_path)
        except OSError as exc:
            self._logger.error("Failed to move file %s to %s: %s", tmp_path, final_path, exc)
            await message.answer(
                "Не удалось сохранить аудиофайл. Пожалуйста, попробуйте отправить его ещё раз позже."
            )
            return

        user_id: int = message.from_user.id if message.from_user else 0

        await message.answer(
            "Аудиофайл принят.\n"
            f"track_id: <code>{track_id}</code>\n"
            f"track_name: <code>{track_name}</code>\n"
            f"Путь к файлу: <code>{final_path}</code>",
            parse_mode="HTML",
        )

        # Save preliminary PipelineState so that the language callback can find the track
        pipeline_state = PipelineState(
            track_id=track_id,
            user_id=user_id,
            status=PipelineStatus.PENDING,
            track_file_name=final_path.name,
            track_source=str(final_path),
            track_stem=track_name,
        )
        state_path = track_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error("Failed to write state.json for track_id=%s: %s", track_id, exc)

        # Ask the user for the song language before starting the pipeline
        await self._ask_for_lang(message, state, track_id, str(track_dir))

    async def _probe_audio(self, path: Path) -> tuple[float | None, str | None, str | None]:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            self._logger.error("Failed to start ffprobe for %s: %s", path, exc)
            return None, None, None

        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            self._logger.warning(
                "ffprobe returned non-zero exit code for %s: %s", path, stderr.decode("utf-8", "ignore")
            )
            return None, None, None

        try:
            payload: dict[str, Any] = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._logger.warning("Failed to parse ffprobe output for %s: %s", path, exc)
            return None, None, None

        fmt = payload.get("format") or {}
        duration_raw = fmt.get("duration")
        duration: float | None
        if duration_raw is None:
            duration = None
        else:
            try:
                duration = float(duration_raw)
            except (TypeError, ValueError):
                duration = None

        tags = fmt.get("tags") or {}
        artist = tags.get("artist") or tags.get("ARTIST")
        title = tags.get("title") or tags.get("TITLE")

        return duration, artist, title

    def _build_track_name(
        self,
        original_filename: str,
        artist: str | None,
        title: str | None,
    ) -> str:
        if artist or title:
            parts = [part for part in [artist, title] if part]
            base = "-".join(parts)
        else:
            base = Path(original_filename).stem

        base = base.strip()
        if not base:
            base = "track"

        normalized = re.sub(r"[^\w\s\-]+", "", base)  # Удаляем спецсимволы, кроме букв/цифр/пробелов/дефиса
        normalized = re.sub(r"\s+", " ", normalized)  # Сжимаем множественные пробелы в один
        if not normalized:
            normalized = base

        return normalized

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    _URL_PATTERN: re.Pattern[str] = re.compile(
        r"https?://[^\s]+",
        re.IGNORECASE,
    )

    _BLOCKED_HOSTS: tuple[str, ...] = (
        "youtube.com",
        "www.youtube.com",
        "youtu.be",
        "m.youtube.com",
    )

    def _extract_url(self, text: str) -> str | None:
        """Return the first HTTP(S) URL found in *text*, or None.

        If the message starts with http(s)://, treat the entire stripped
        text as the URL (replacing spaces with %20 so filenames with spaces are
        preserved).  Otherwise, find the first occurrence of http and take
        everything from that position to the end of the string (again encoding
        spaces as %20).
        """
        stripped = text.strip()
        if re.match(r"https?://", stripped, re.IGNORECASE):
            # Entire message is the URL candidate
            return stripped.replace(" ", "%20")
        match = re.search(r"https?://", stripped, re.IGNORECASE)
        if match:
            return stripped[match.start():].replace(" ", "%20")
        return None

    def _is_blocked_url(self, url: str) -> bool:
        """Return True if *url* points to YouTube."""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except ValueError:
            return False
        return any(host == blocked or host.endswith("." + blocked) for blocked in self._BLOCKED_HOSTS)

    def _is_yandex_music_url(self, url: str) -> bool:
        """Return True if *url* points to Yandex Music."""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except ValueError:
            return False
        return host == "music.yandex.ru" or host.endswith(".music.yandex.ru")

    async def _handle_yandex_music_url(
        self,
        message: types.Message,
        state: FSMContext,
        url: str,
    ) -> None:
        """Handle Yandex Music URL: download track and fetch lyrics."""
        self._ensure_tracks_root()

        user_id: int = message.from_user.id if message.from_user else 0

        await message.answer(
            f"⏳ Загружаю трек с Яндекс Музыки...\n"
            f"URL: {url}"
        )

        # Initialize Yandex Music downloader
        downloader = YandexMusicDownloader(token=self._settings.yandex_music_token)

        try:
            # First get track info to build directory name
            track_info = await downloader.get_track_info(url)
        except Exception as exc:
            self._logger.error(
                "Failed to get track info from Yandex Music: %s", exc
            )
            await message.answer(
                f"❌ Не удалось получить информацию о треке: {exc}"
            )
            return

        # Build track name like in standard flow (using _build_track_name)
        track_name = self._build_track_name(
            track_info.title or "track",
            track_info.artist,
            track_info.title
        )

        # Create track directory with normalized name (not UUID!)
        track_dir = self._tracks_root_dir / track_name
        track_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Now download track to the created directory
            track_info = await downloader.download(url, track_dir)
        except Exception as exc:
            self._logger.error(
                "Failed to download track from Yandex Music: %s", exc
            )
            await message.answer(
                f"❌ Не удалось скачать трек с Яндекс Музыки: {exc}"
            )
            return

        # Check duration
        duration, _, _ = await self._probe_audio(track_info.local_path)
        if duration is None or duration < 60:
            await message.answer(
                f"Полученный трек не является музыкальной композицией "
                "(длительность менее 1 минуты или не удалось определить длительность)."
            )
            return

        # Generate track_id for PipelineState (UUID, not used for directory name)
        track_id = uuid.uuid4().hex

        # Try to fetch lyrics/LRC from Yandex Music
        lyrics_saved = False
        try:
            lyrics_result = await downloader.fetch_lyrics(track_info.track_id)

            # Save LRC (preferred) or plain text as source_lyrics_file
            stem = track_info.track_stem
            if lyrics_result.lrc_text:
                lrc_file = track_dir / f"{stem}_lyrics.txt"
                lrc_file.write_text(lyrics_result.lrc_text, encoding="utf-8")
                lyrics_path = str(lrc_file)
                lyrics_saved = True
                self._logger.info(
                    "Saved LRC lyrics for track_id=%s to %s", track_id, lyrics_path
                )
            elif lyrics_result.plain_text:
                txt_file = track_dir / f"{stem}_lyrics.txt"
                txt_file.write_text(lyrics_result.plain_text, encoding="utf-8")
                lyrics_path = str(txt_file)
                lyrics_saved = True
                self._logger.info(
                    "Saved plain text lyrics for track_id=%s to %s", track_id, lyrics_path
                )
        except Exception as exc:
            self._logger.warning(
                "Failed to fetch lyrics from Yandex Music for track_id=%s: %s",
                track_id,
                exc,
            )
            # Continue without lyrics - pipeline will request from user if needed

        # Save PipelineState
        pipeline_state = PipelineState(
            track_id=track_id,
            user_id=user_id,
            status=PipelineStatus.PENDING,
            track_file_name=track_info.local_path.name,
            track_source=str(track_info.local_path),
            track_stem=track_info.track_stem,
        )
        if lyrics_saved:
            pipeline_state.source_lyrics_file = lyrics_path

        state_path = track_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error(
                "Failed to write state.json for track_id=%s: %s", track_id, exc
            )

        # Notify user
        lyrics_msg = "\nТекст с таймкодами получен с Яндекс Музыки!" if lyrics_saved else ""
        await message.answer(
            f"✅ Трек загружен с Яндекс Музыки!\n"
            f"track_id: <code>{track_id}</code>\n"
            f"Название: <code>{track_info.track_stem}</code>\n"
            f"Длительность: {int(duration // 60)}:{int(duration % 60):02d}"
            f"{lyrics_msg}",
            parse_mode="HTML",
        )

        # Ask for language and continue with pipeline
        await self._ask_for_lang(message, state, track_id, str(track_dir))
