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

from .config import Settings
from .models import LyricsStates, PipelineResult, PipelineState, PipelineStatus, PipelineStep, UserRequest
from .pipeline import KaraokePipeline, LyricsNotFoundError, _ORDERED_STEPS


class KaraokeHandlers:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tracks_root_dir: Path = settings.tracks_root_dir
        self._logger = logging.getLogger(__name__)
        self.router = Router()
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def handle_start(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await state.clear()
            await message.answer(
                "Привет! Я бот для подготовки караоке-видео.\n"
                "Отправьте мне аудиофайл (музыкальную композицию длительностью более 1 минуты), "
                "и я подготовлю данные для караоке-пайплайна."
            )

        @self.router.message(F.audio)
        async def handle_audio(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            audio = message.audio
            if audio is None:
                return

            self._ensure_tracks_root()

            tmp_dir = self._tracks_root_dir / "_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)

            original_name = audio.file_name or f"audio_{audio.file_unique_id}.mp3"
            tmp_path = tmp_dir / original_name

            await message.bot.download(audio, destination=tmp_path)
            duration, artist, title = await self._probe_audio(tmp_path)

            track_name = self._build_track_name(original_name or "track", None, None)

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

            final_path = track_dir / f"{track_name}.mp3"

            try:
                shutil.move(str(tmp_path), final_path)
            except OSError as exc:
                self._logger.error("Failed to move file %s to %s: %s", tmp_path, final_path, exc)
                await message.answer(
                    "Не удалось сохранить аудиофайл. Пожалуйста, попробуйте отправить его ещё раз позже."
                )
                return

            await message.answer(
                "Аудиофайл принят.\n"
                f"track_id: <code>{track_id}</code>\n"
                f'track_name: <code>{track_name}</code>\n'
                f'Путь к файлу: <code>{final_path}</code>',
                parse_mode="HTML",
            )

            user_id: int = message.from_user.id if message.from_user else 0
            request = UserRequest(
                user_id=user_id,
                track_id=track_id,
                source_type="file",
                source_url_or_file_path=str(final_path),
                track_folder=str(track_dir),
            )
            pipeline = KaraokePipeline(request, self._settings)

            async def _audio_progress(msg: str) -> None:
                await message.answer(msg)

            try:
                result = await pipeline.run(_audio_progress)
            except LyricsNotFoundError:
                await self._ask_for_lyrics(message, state, track_id, track_dir)
                return

            if result.status == PipelineStatus.COMPLETED:
                await self._send_result_video(message, result)
            else:
                await message.answer(
                    f"💔 Обработка завершена с ошибкой.\n"
                    f"track_id: <code>{result.track_id}</code>\n"
                    f"Причина: {result.error_message}",
                    parse_mode="HTML",
                )

        @self.router.message(Command("continue"))
        async def handle_continue(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            result = self._find_latest_state()
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

        @self.router.message(Command("step_separate"))
        async def handle_step_separate(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await self._handle_step_command(message, PipelineStep.SEPARATE, state)

        @self.router.message(Command("step_transcribe"))
        async def handle_step_transcribe(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await self._handle_step_command(message, PipelineStep.TRANSCRIBE, state)

        @self.router.message(Command("step_lyrics"))
        async def handle_step_lyrics(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await self._handle_step_command(message, PipelineStep.GET_LYRICS, state)

        @self.router.message(Command("step_align"))
        async def handle_step_align(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await self._handle_step_command(message, PipelineStep.ALIGN, state)

        @self.router.message(Command("step_ass"))
        async def handle_step_ass(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await self._handle_step_command(message, PipelineStep.GENERATE_ASS, state)

        @self.router.message(Command("step_render"))
        async def handle_step_render(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            await self._handle_step_command(message, PipelineStep.RENDER_VIDEO, state)

        # ----- FSM: waiting for user to supply lyrics text -----
        @self.router.message(LyricsStates.waiting_for_lyrics, F.text)
        async def handle_lyrics_input(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
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

            # Continue pipeline from ALIGN step
            await self._run_from_step(message, track_dir, pipeline_state, PipelineStep.ALIGN, state)

        # ----- General text handler (URLs) — must be AFTER FSM handler -----
        @self.router.message(F.text)
        async def handle_text(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
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
                    "Ссылки на Яндекс Музыку и YouTube пока не поддерживаются. "
                    "Поддержка этих источников появится в будущих версиях бота."
                )
                return

            self._ensure_tracks_root()

            track_id = uuid.uuid4().hex
            parsed_url_for_name = urlparse(url)
            url_basename = unquote(parsed_url_for_name.path.rstrip("/").split("/")[-1]) if parsed_url_for_name.path.rstrip("/") else ""
            track_name = self._build_track_name(url_basename or "track", None, None)
            track_dir = self._tracks_root_dir / track_name
            track_dir.mkdir(parents=True, exist_ok=True)

            user_id: int = message.from_user.id if message.from_user else 0
            request = UserRequest(
                user_id=user_id,
                track_id=track_id,
                source_type="url",
                source_url_or_file_path=url,
                track_folder=str(track_dir),
            )

            # Save state.json at <tracks_root_dir> / <track_name> / state.json
            state_path = track_dir / "state.json"
            try:
                state_path.write_text(
                    request.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            except OSError as exc:
                self._logger.error(
                    "Failed to write state file for track %s: %s", track_id, exc
                )
                await message.answer(
                    "Не удалось сохранить информацию о треке. "
                    "Пожалуйста, попробуйте ещё раз позже."
                )
                return

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

            # Update request with local file path and re-save state.json
            request = UserRequest(
                user_id=user_id,
                track_id=track_id,
                source_type="url",
                source_url_or_file_path=str(local_path),
                track_folder=str(track_dir),
            )
            try:
                state_path.write_text(
                    request.model_dump_json(indent=2),
                    encoding="utf-8",
                )
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

            pipeline = KaraokePipeline(request, self._settings)

            async def _url_progress(msg: str) -> None:
                await message.answer(msg)

            try:
                result = await pipeline.run(_url_progress)
            except LyricsNotFoundError:
                await self._ask_for_lyrics(message, state, track_id, track_dir)
                return

            if result.status == PipelineStatus.COMPLETED:
                await self._send_result_video(message, result)
            else:
                await message.answer(
                    f"💔 Обработка завершена с ошибкой.\n"
                    f"track_id: <code>{result.track_id}</code>\n"
                    f"Причина: {result.error_message}",
                    parse_mode="HTML",
                )

        @self.router.message()
        async def handle_non_audio(message: types.Message) -> None:  # type: ignore[unused-ignore]
            await message.answer(
                "Полученное сообщение не является музыкальной композицией. "
                "Пожалуйста, отправьте аудиофайл длительностью более 1 минуты."
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

    def _find_latest_state(self) -> tuple[PipelineState, Path] | None:
        """Find the track folder with the most recently modified state.json.

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
        result = self._find_latest_state()
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

        async def _step_progress(msg: str) -> None:
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
        if video_path_str:
            video_path = Path(video_path_str)
            if video_path.exists():
                try:
                    from aiogram.types import FSInputFile
                    video_file = FSInputFile(video_path, filename=video_path.name)
                    await message.answer_video(
                        video=video_file,
                        caption=(
                            f"🎉 Обработка завершена успешно!\n"
                            f"track_id: <code>{result.track_id}</code>"
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
                self._logger.warning(
                    "Output video file not found for track_id=%s: %s",
                    result.track_id,
                    video_path_str,
                )

        await message.answer(
            f"🎉 Обработка завершена успешно!\n"
            f"track_id: <code>{result.track_id}</code>\n"
            f"Видеофайл: <code>{video_path_str or 'не задан'}</code>",
            parse_mode="HTML",
        )

    def _ensure_tracks_root(self) -> None:
        self._tracks_root_dir.mkdir(parents=True, exist_ok=True)

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
        "music.yandex.ru",
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
        """Return True if *url* points to Yandex Music or YouTube."""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except ValueError:
            return False
        return any(host == blocked or host.endswith("." + blocked) for blocked in self._BLOCKED_HOSTS)
