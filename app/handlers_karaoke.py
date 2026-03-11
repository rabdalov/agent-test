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
from .models import LyricsStates, PipelineResult, PipelineState, PipelineStatus, PipelineStep, SearchStates, TrackLangStates, UserRequest
from .pipeline import KaraokePipeline, LyricsNotFoundError, _ORDERED_STEPS
from .yandex_music_downloader import YandexMusicDownloader
from .youtube_downloader import YouTubeDownloader
from .utils import normalize_filename


class KaraokeHandlers:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tracks_root_dir: Path = settings.tracks_root_dir
        self._logger = logging.getLogger(__name__)
        self.router = Router()
        self._register_handlers()

    def _is_user_allowed(self, message: types.Message) -> bool:
        """Return True if the sender's user_id is in the allowed list."""
        user_id = message.from_user.id if message.from_user else None
        if user_id is None:
            return False
        # Проверяем по новому механизму пользователей
        if self._settings.is_user_denied(user_id):
            return False
        if self._settings.is_user_allowed(user_id):
            return True
        # Если пользователь не в списках, проверяем старый список tlg_allowed_id
        allowed = self._settings.tlg_allowed_id
        if not allowed:
            return True
        return user_id in allowed

    async def _reject_unauthorized(self, message: types.Message) -> None:
        """Send a rejection notice and log the attempt."""
        user_id = message.from_user.id if message.from_user else None
        user_name = message.from_user.full_name if message.from_user else None
        self._logger.warning("Unauthorized access attempt from user_id=%s", user_id)
        # Уведомляем администратора о запросе от неавторизованного пользователя
        await self._notify_admin_of_unauthorized_access(message, user_id, user_name)
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

        @self.router.message(Command("search"))
        async def handle_search(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await state.set_state(SearchStates.waiting_for_query)
            await message.answer(
                "🔍 Введите информацию для поиска в формате Артист - Песня.\n"
                "Например: Полина Гагарина - Shallow"
            )

        # ----- FSM: waiting for search query -----
        @self.router.message(SearchStates.waiting_for_query, F.text)
        async def handle_search_query(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            query = (message.text or "").strip()
            if not query:
                await message.answer("Запрос не может быть пустым. Пожалуйста, введите название трека.")
                return

            await message.answer("🔍 Ищу трек...")

            # Парсим запрос: ожидаем формат "Артист - Песня"
            artist = None
            title = None
            if " - " in query:
                parts = query.split(" - ", 1)
                artist = parts[0].strip()
                title = parts[1].strip()
            else:
                # Если нет разделителя, считаем всё за title
                title = query

            # Сначала ищем в локальном хранилище
            local_results = await self._search_local(artist, title)

            # Если не найдено локально — ищем на Яндекс Музыке
            yandex_results: list[dict[str, Any]] = []
            if not local_results and self._settings.yandex_music_token:
                yandex_results = await self._search_yandex(query)

            # Объединяем результаты (локальные + яндекс)
            all_results = local_results + yandex_results

            if not all_results:
                await message.answer(
                    "🔍 Трек не найден ни в локальном хранилище, ни на Яндекс Музыке.\n"
                    "Попробуйте изменить запрос или добавить трек другим способом."
                )
                await state.clear()
                return

            # Ограничиваем топ-5
            all_results = all_results[:5]

            # Сохраняем результаты в state
            await state.update_data(search_results=all_results, search_query=query)
            await state.set_state(SearchStates.waiting_for_selection)

            # Формируем сообщение с результатами
            result_text = "🎵 Найденные треки:\n\n"
            for i, track in enumerate(all_results, 1):
                source_label = "📁 Локально" if track.get("source") == "local" else "🎧 Яндекс Музыка"
                artist_name = track.get("artist", "Unknown")
                title_name = track.get("title", "Unknown")
                result_text += f"{i}. {artist_name} - {title_name}\n   {source_label}\n\n"

            result_text += "Выберите номер трека для обработки:"

            # Создаём inline клавиатуру с номерами (горизонтально) + кнопка "Я" для Яндекс Музыки
            keyboard_buttons = [
                InlineKeyboardButton(text=str(i), callback_data=f"search_select:{i-1}")
                for i in range(1, len(all_results) + 1)
            ]
            # Добавляем кнопку "Я" для поиска на Яндекс Музыке
            keyboard_buttons.append(
                InlineKeyboardButton(text="Я", callback_data=f"search_yandex:{query}")
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])

            await message.answer(result_text, reply_markup=keyboard)

        # ----- FSM: waiting for search selection -----
        @self.router.callback_query(SearchStates.waiting_for_selection, F.data.startswith("search_select:"))
        async def handle_search_selection(callback: types.CallbackQuery, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            # Проверка доступа
            caller_id = callback.from_user.id if callback.from_user else None
            if caller_id is None:
                await callback.answer("⛔ Не удалось определить пользователя.", show_alert=True)
                return
            if self._settings.is_user_denied(caller_id):
                await callback.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
                return
            if not self._settings.is_user_allowed(caller_id):
                allowed = self._settings.tlg_allowed_id
                if allowed and caller_id not in allowed:
                    await callback.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
                    return

            await callback.answer()

            # Получаем индекс выбранного трека
            try:
                index = int(callback.data.split(":")[1])
            except (IndexError, ValueError):
                await callback.answer("❌ Неверный выбор.", show_alert=True)
                return

            # Получаем данные поиска
            data = await state.get_data()
            search_results: list[dict[str, Any]] = data.get("search_results", [])

            if index < 0 or index >= len(search_results):
                await callback.answer("❌ Неверный номер трека.", show_alert=True)
                return

            selected_track = search_results[index]
            await state.clear()

            # Обрабатываем выбранный трек
            if selected_track.get("source") == "local":
                # Локальный трек — запускаем пайплайн с шага SEPARATE
                await self._handle_local_track(callback.message, selected_track, callback.from_user.id if callback.from_user else 0)  # type: ignore[union-attr]
            else:
                # Яндекс Музыка — запускаем полный пайплайн
                await self._handle_yandex_track_search_result(callback.message, selected_track, state, callback.from_user.id if callback.from_user else 0)  # type: ignore[union-attr]

        # Обработчик кнопки "Я" для поиска на Яндекс Музыке
        @self.router.callback_query(SearchStates.waiting_for_selection, F.data.startswith("search_yandex:"))
        async def handle_search_yandex(callback: types.CallbackQuery, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            # Проверка доступа
            caller_id = callback.from_user.id if callback.from_user else None
            if caller_id is None:
                await callback.answer("⛔ Не удалось определить пользователя.", show_alert=True)
                return
            if self._settings.is_user_denied(caller_id):
                await callback.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
                return
            if not self._settings.is_user_allowed(caller_id):
                allowed = self._settings.tlg_allowed_id
                if allowed and caller_id not in allowed:
                    await callback.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
                    return

            await callback.answer()

            # Получаем запрос из callback_data
            query = callback.data.split(":", 1)[1] if ":" in callback.data else ""
            if not query:
                await callback.answer("❌ Не удалось получить запрос.", show_alert=True)
                return

            await state.clear()

            # Ищем на Яндекс Музыке
            if not self._settings.yandex_music_token:
                await callback.message.answer("❌ Токен Яндекс Музыки не настроен.")  # type: ignore[union-attr]
                return

            await callback.message.answer("🔍 Ищу на Яндекс Музыке...")  # type: ignore[union-attr]

            yandex_results = await self._search_yandex(query)

            if not yandex_results:
                await callback.message.answer("🔍 Трек не найден на Яндекс Музыке.")  # type: ignore[union-attr]
                return

            # Ограничиваем топ-5
            yandex_results = yandex_results[:5]

            # Сохраняем результаты в state
            await state.update_data(search_results=yandex_results, search_query=query)
            await state.set_state(SearchStates.waiting_for_selection)

            # Формируем сообщение с результатами
            result_text = "🎵 Найденные треки на Яндекс Музыке:\n\n"
            for i, track in enumerate(yandex_results, 1):
                artist_name = track.get("artist", "Unknown")
                title_name = track.get("title", "Unknown")
                result_text += f"{i}. {artist_name} - {title_name}\n   🎧 Яндекс Музыка\n\n"

            result_text += "Выберите номер трека для обработки:"

            # Создаём inline клавиатуру (горизонтально)
            keyboard_buttons = [
                InlineKeyboardButton(text=str(i), callback_data=f"search_select:{i-1}")
                for i in range(1, len(yandex_results) + 1)
            ]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])

            await callback.message.answer(result_text, reply_markup=keyboard)  # type: ignore[union-attr]

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

            # Edit notification with success message (or send new)
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                "✅ Текст песни получен. Продолжаю обработку...",
            )

            # Continue pipeline from SEPARATE step (after GET_LYRICS)
            await self._run_from_step(message, track_dir, pipeline_state, PipelineStep.SEPARATE, state)

        # ----- Admin callback handler -----
        @self.router.callback_query(F.data.startswith("admin_"))
        async def handle_admin_callback(callback: types.CallbackQuery) -> None:
            """Handle admin decisions for user access."""
            data = callback.data or ""
            if data.startswith("admin_allow:"):
                _, user_id_str, user_name = data.split(":", 2)
                try:
                    user_id = int(user_id_str)
                except ValueError:
                    await callback.answer("❌ Неверный user_id.", show_alert=True)
                    return
                await self._handle_admin_decision(callback, "allow", user_id, user_name)
            elif data.startswith("admin_deny:"):
                _, user_id_str, user_name = data.split(":", 2)
                try:
                    user_id = int(user_id_str)
                except ValueError:
                    await callback.answer("❌ Неверный user_id.", show_alert=True)
                    return
                await self._handle_admin_decision(callback, "deny", user_id, user_name)
            else:
                await callback.answer("Неизвестная команда.", show_alert=True)

        # ----- FSM: waiting for user to select song language -----
        @self.router.callback_query(TrackLangStates.waiting_for_lang, F.data.startswith("lang_choice:"))
        async def handle_lang_choice(callback: types.CallbackQuery, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            # Check access by callback sender (callback.from_user), not by callback.message.from_user (which is the bot)
            caller_id = callback.from_user.id if callback.from_user else None
            if caller_id is None:
                await callback.answer("⛔ Не удалось определить пользователя.", show_alert=True)
                return
            if self._settings.is_user_denied(caller_id):
                await callback.answer("⛔ У вас нет доступа к этому боту.", show_alert=True)
                return
            if not self._settings.is_user_allowed(caller_id):
                # Проверяем старый список tlg_allowed_id
                allowed = self._settings.tlg_allowed_id
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
                        callback.message, state, track_id, track_dir, pipeline_state  # type: ignore[arg-type]
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

            # Handle YouTube URL separately
            if self._is_youtube_url(url):
                await self._handle_youtube_url(message, state, url)
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
            # Normalize filename for consistency
            filename = normalize_filename(url_basename) if url_basename else "source_file"
            local_path = track_dir / filename

            # Create a temporary PipelineState for error notifications
            error_state = PipelineState(
                track_id=track_id,
                user_id=user_id,
                status=PipelineStatus.PENDING,
            )

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
                await self._send_or_edit_notification(
                    message,
                    error_state,
                    "Не удалось скачать файл по указанной ссылке. "
                    "Пожалуйста, проверьте ссылку и попробуйте ещё раз.",
                )
                return
            except OSError as exc:
                self._logger.error(
                    "Failed to save downloaded file for track %s: %s", track_id, exc
                )
                await self._send_or_edit_notification(
                    message,
                    error_state,
                    "Не удалось сохранить скачанный файл. "
                    "Пожалуйста, попробуйте ещё раз позже.",
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

            # Send notification (reply) and store its ID in pipeline_state_url
            await self._send_or_edit_notification(
                message,
                pipeline_state_url,
                "Файл скачан и принят.\n"
                f"track_id: <code>{track_id}</code>\n"
                f"track_name: <code>{track_name}</code>\n"
                f"Путь к файлу: <code>{local_path}</code>",
                parse_mode="HTML",
            )
            # Update state.json with notification IDs (already done in _send_or_edit_notification)

            # Ask the user for the song language before starting the pipeline
            await self._ask_for_lang(message, state, track_id, str(track_dir), pipeline_state_url)

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
    # Admin notification helpers
    # ------------------------------------------------------------------

    async def _notify_admin_of_unauthorized_access(
        self,
        message: types.Message,
        user_id: int | None,
        user_name: str | None,
    ) -> None:
        """Send a notification to admin about unauthorized access request."""
        admin_id = self._settings.admin_id
        if not admin_id:
            self._logger.warning("No admin_id configured, skipping admin notification")
            return

        # Создаём inline-клавиатуру с кнопками "Добавить" и "Отклонить"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Добавить",
                        callback_data=f"admin_allow:{user_id}:{user_name or ''}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=f"admin_deny:{user_id}:{user_name or ''}"
                    )
                ]
            ]
        )

        try:
            await message.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"⚠️ *Запрос доступа от неавторизованного пользователя*\n\n"
                    f"*User ID:* `{user_id}`\n"
                    f"*Имя:* {user_name or 'не указано'}\n\n"
                    f"Выберите действие:"
                ),
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        except Exception as exc:
            self._logger.error("Failed to send admin notification: %s", exc)

    async def _handle_admin_decision(
        self,
        callback: types.CallbackQuery,
        decision: str,  # "allow" или "deny"
        user_id: int,
        user_name: str | None
    ) -> None:
        """Handle admin's decision to allow or deny a user."""
        if decision == "allow":
            self._settings.add_allowed_user(user_id, user_name)
            await callback.answer(f"✅ Пользователь {user_id} добавлен в разрешённые.", show_alert=True)
        else:
            self._settings.add_denied_user(user_id, user_name)
            await callback.answer(f"❌ Пользователь {user_id} добавлен в отклонённые.", show_alert=True)

        # Удаляем клавиатуру из сообщения
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Language selection FSM helpers
    # ------------------------------------------------------------------

    async def _ask_for_lang(
        self,
        message: types.Message,
        state: FSMContext,
        track_id: str,
        track_folder: str,
        pipeline_state: PipelineState | None = None,
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
        text = "🎵 На каком языке исполняется эта песня?\n\nВыберите язык исполнения:"
        
        # If we have a pipeline_state with notification IDs, edit that notification
        if pipeline_state and pipeline_state.notification_chat_id and pipeline_state.notification_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=pipeline_state.notification_chat_id,
                    message_id=pipeline_state.notification_message_id,
                    text=text,
                    reply_markup=keyboard,
                )
                return
            except Exception as exc:
                self._logger.warning(
                    "Failed to edit notification for language selection track_id=%s: %s. Sending new.",
                    track_id,
                    exc,
                )
                # Fall through to sending new message
        
        # Otherwise send a new reply message
        sent = await message.reply(text, reply_markup=keyboard)
        # If pipeline_state is provided, update it with new notification IDs
        if pipeline_state:
            pipeline_state.notification_chat_id = sent.chat.id
            pipeline_state.notification_message_id = sent.message_id
            # Update state.json
            track_dir = Path(track_folder)
            state_path = track_dir / "state.json"
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state.json with notification IDs for track_id=%s: %s",
                    track_id,
                    exc,
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
        pipeline_state: PipelineState | None = None,
    ) -> None:
        """Transition user to FSM state waiting_for_lyrics and ask to send lyrics."""
        await state.set_state(LyricsStates.waiting_for_lyrics)
        await state.update_data(track_id=track_id)
        self._logger.info(
            "LyricsNotFoundError for track_id=%s — requesting lyrics from user", track_id
        )
        text = "🎵 Не удалось автоматически найти текст песни.\n\nПожалуйста, пришли полный текст песни в следующем сообщении."
        
        # If we have a pipeline_state with notification IDs, edit that notification
        if pipeline_state and pipeline_state.notification_chat_id and pipeline_state.notification_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=pipeline_state.notification_chat_id,
                    message_id=pipeline_state.notification_message_id,
                    text=text,
                )
                return
            except Exception as exc:
                self._logger.warning(
                    "Failed to edit notification for lyrics request track_id=%s: %s. Sending new.",
                    track_id,
                    exc,
                )
                # Fall through to sending new message
        
        # Otherwise send a new reply message
        await message.reply(text)

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

        # If state already has notification IDs, edit that notification; otherwise send new reply
        if state.notification_chat_id and state.notification_message_id:
            # Edit existing notification to show pipeline start
            try:
                await message.bot.edit_message_text(
                    chat_id=state.notification_chat_id,
                    message_id=state.notification_message_id,
                    text="⏳ Начинаю обработку...",
                )
                # Use existing IDs for progress editing
                notification_chat_id = state.notification_chat_id
                notification_message_id = state.notification_message_id
            except Exception as edit_err:
                self._logger.warning(
                    "Failed to edit existing notification for track_id=%s: %s. Sending new.",
                    state.track_id,
                    edit_err,
                )
                # Fallback: send new reply and store IDs
                initial_msg = await message.reply("⏳ Начинаю обработку...")
                notification_chat_id = initial_msg.chat.id
                notification_message_id = initial_msg.message_id
                state.notification_chat_id = notification_chat_id
                state.notification_message_id = notification_message_id
                # Update state.json
                state_path = track_dir / "state.json"
                try:
                    state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
                except OSError as exc:
                    self._logger.error(
                        "Failed to update state.json with notification IDs for track_id=%s: %s",
                        state.track_id,
                        exc,
                    )
        else:
            # No existing notification, send new reply and store IDs
            initial_msg = await message.reply("⏳ Начинаю обработку...")
            notification_chat_id = initial_msg.chat.id
            notification_message_id = initial_msg.message_id
            state.notification_chat_id = notification_chat_id
            state.notification_message_id = notification_message_id
            # Update state.json
            state_path = track_dir / "state.json"
            try:
                state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state.json with notification IDs for track_id=%s: %s",
                    state.track_id,
                    exc,
                )

        async def _step_progress(msg: str) -> None:
            # Edit the notification message stored in state
            try:
                await message.bot.edit_message_text(
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                    text=msg,
                )
            except Exception as edit_err:
                self._logger.warning("Failed to edit notification for track_id=%s: %s", state.track_id, edit_err)
                await message.answer(msg)

        try:
            result = await pipeline.run(_step_progress, start_from_step=step)
        except LyricsNotFoundError:
            await self._ask_for_lyrics(message, fsm_context, state.track_id, track_dir, state)
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
    # Notification editing helpers
    # ------------------------------------------------------------------

    async def _edit_notification(
        self,
        pipeline_state: PipelineState,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Edit the notification message stored in pipeline_state.
        
        If pipeline_state does not have notification_chat_id and notification_message_id,
        or editing fails, fall back to sending a new message (but we cannot know where to send).
        In that case, log a warning and do nothing (caller should handle).
        """
        if not pipeline_state.notification_chat_id or not pipeline_state.notification_message_id:
            self._logger.warning(
                "Cannot edit notification for track_id=%s: missing chat_id or message_id",
                pipeline_state.track_id,
            )
            return
        
        # We cannot edit without a message context; this method should be called only when
        # we have access to a message object (e.g., from a handler). Since we don't have
        # message here, we cannot edit. This method is currently unused; we should use
        # _send_or_edit_notification instead.
        self._logger.warning(
            "_edit_notification called without message context for track_id=%s; cannot edit",
            pipeline_state.track_id,
        )
        # Do nothing; caller should handle fallback

    async def _send_or_edit_notification(
        self,
        message: types.Message,
        pipeline_state: PipelineState,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Send a new notification (reply to user's message) and store its ID in pipeline_state,
        or edit existing notification if already present.
        
        Returns the sent/edited message (or None if failed).
        """
        # If we already have a notification message, edit it
        if pipeline_state.notification_chat_id and pipeline_state.notification_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=pipeline_state.notification_chat_id,
                    message_id=pipeline_state.notification_message_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                )
                return
            except Exception as exc:
                self._logger.warning(
                    "Failed to edit existing notification for track_id=%s: %s. Sending new.",
                    pipeline_state.track_id,
                    exc,
                )
                # Fall through to sending new message
        
        # Send new reply message
        sent = await message.reply(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        pipeline_state.notification_chat_id = sent.chat.id
        pipeline_state.notification_message_id = sent.message_id
        # Update state.json
        track_dir = self._find_track_dir_by_id(pipeline_state.track_id)
        if track_dir:
            state_path = track_dir / "state.json"
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state.json with notification IDs for track_id=%s: %s",
                    pipeline_state.track_id,
                    exc,
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

        # Send notification (reply) and store its ID in pipeline_state
        await self._send_or_edit_notification(
            message,
            pipeline_state,
            "Аудиофайл принят.\n"
            f"track_id: <code>{track_id}</code>\n"
            f"track_name: <code>{track_name}</code>\n"
            f"Путь к файлу: <code>{final_path}</code>",
            parse_mode="HTML",
        )
        # Update state.json with notification IDs (already done in _send_or_edit_notification)

        # Ask the user for the song language before starting the pipeline
        await self._ask_for_lang(message, state, track_id, str(track_dir), pipeline_state)

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
            # Используем тот же формат, что и в yandex_music_downloader.py: "artist - title"
            base = " - ".join(parts)
        else:
            base = Path(original_filename).stem

        # Применяем нормализацию имени файла согласно правилам
        return normalize_filename(base)

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    _URL_PATTERN: re.Pattern[str] = re.compile(
        r"https?://[^\s]+",
        re.IGNORECASE,
    )

    _BLOCKED_HOSTS: tuple[str, ...] = ()

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

    def _is_youtube_url(self, url: str) -> bool:
        """Return True if *url* points to YouTube."""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
        except ValueError:
            return False
        youtube_hosts = (
            "youtube.com",
            "www.youtube.com",
            "youtu.be",
            "m.youtube.com",
            "music.youtube.com",
        )
        return any(host == h or host.endswith("." + h) for h in youtube_hosts)

    async def _handle_yandex_music_url(
        self,
        message: types.Message,
        state: FSMContext,
        url: str,
    ) -> None:
        """Handle Yandex Music URL: download track and fetch lyrics."""
        self._ensure_tracks_root()

        user_id: int = message.from_user.id if message.from_user else 0
        track_id = uuid.uuid4().hex  # generate early for notification

        # Create preliminary PipelineState (without track details)
        pipeline_state = PipelineState(
            track_id=track_id,
            user_id=user_id,
            status=PipelineStatus.PENDING,
        )
        # Send initial notification (reply) and store its ID
        await self._send_or_edit_notification(
            message,
            pipeline_state,
            f"⏳ Загружаю трек с Яндекс Музыки...\nURL: {url}",
            parse_mode="HTML",
        )
        # Update state.json (will be created later when we have track_dir)

        # Initialize Yandex Music downloader
        downloader = YandexMusicDownloader(token=self._settings.yandex_music_token)

        try:
            # First get track info to build directory name
            track_info = await downloader.get_track_info(url)
        except Exception as exc:
            self._logger.error(
                "Failed to get track info from Yandex Music: %s", exc
            )
            # Edit notification with error
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                f"❌ Не удалось получить информацию о треке: {exc}",
                parse_mode="HTML",
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
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                f"❌ Не удалось скачать трек с Яндекс Музыки: {exc}",
                parse_mode="HTML",
            )
            return

        # Check duration
        duration, _, _ = await self._probe_audio(track_info.local_path)
        if duration is None or duration < 60:
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                f"Полученный трек не является музыкальной композицией "
                "(длительность менее 1 минуты или не удалось определить длительность).",
                parse_mode="HTML",
            )
            return

        # Try to fetch lyrics/LRC from Yandex Music
        lyrics_saved = False
        lyrics_path = None
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

        # Update PipelineState with track details
        pipeline_state.track_file_name = track_info.local_path.name
        pipeline_state.track_source = str(track_info.local_path)
        pipeline_state.track_stem = track_info.track_stem
        if lyrics_saved:
            pipeline_state.source_lyrics_file = lyrics_path

        state_path = track_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error(
                "Failed to write state.json for track_id=%s: %s", track_id, exc
            )

        # Edit notification with success message
        lyrics_msg = "\nТекст с таймкодами получен с Яндекс Музыки!" if lyrics_saved else ""
        await self._send_or_edit_notification(
            message,
            pipeline_state,
            f"✅ Трек загружен с Яндекс Музыки!\n"
            f"track_id: <code>{track_id}</code>\n"
            f"Название: <code>{track_info.track_stem}</code>\n"
            f"Длительность: {int(duration // 60)}:{int(duration % 60):02d}"
            f"{lyrics_msg}",
            parse_mode="HTML",
        )

        # Ask for language and continue with pipeline
        await self._ask_for_lang(message, state, track_id, str(track_dir), pipeline_state)

    # ------------------------------------------------------------------
    # Search functionality
    # ------------------------------------------------------------------

    async def _search_local(self, artist: str | None, title: str | None) -> list[dict[str, Any]]:
        """Search for tracks in local storage (TRACKS_ROOT_DIR).

        Scans the directory 1 level deep.
        - Filters by extension (.mp3, .flac, .mp4, .avi)
        - Excludes files containing (Instrumental) or (Vocal)
        - Searches by simultaneous presence of BOTH artist AND title
        - Normalizes text: removes punctuation, case-insensitive
        - Only includes folders with state.json
        - Returns track_source from state.json
        - Sorts by creation date (descending), top-5
        """
        if not self._tracks_root_dir.exists():
            return []

        results: list[dict[str, Any]] = []
        audio_extensions = (".mp3", ".flac", ".mp4", ".avi")
        
        # Нормализуем artist и title: убираем знаки препинания, приводим к нижнему регистру
        def normalize(text: str) -> str:
            # Удаляем знаки препинания и приводим к нижнему регистру
            return re.sub(r'[^\w\s]', '', text).lower().strip()
        
        normalized_artist = normalize(artist) if artist else None
        normalized_title = normalize(title) if title else None
        
        # Сканируем директорию 1 уровень вглубь
        try:
            for item in self._tracks_root_dir.iterdir():
                if not item.is_dir():
                    continue
                
                # Проверяем наличие state.json в папке
                state_path = item / "state.json"
                if not state_path.exists():
                    continue
                
                # Читаем state.json
                state_data = None
                try:
                    state_data = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                
                # Получаем track_source из state.json
                track_source = state_data.get("track_source")
                if not track_source:
                    continue
                
                # Проверяем файл по расширению
                source_path = Path(track_source)
                if not source_path.exists() or source_path.suffix.lower() not in audio_extensions:
                    continue
                
                # Исключаем файлы содержащие (Instrumental) или (Vocal)
                filename_lower = source_path.name.lower()
                if "(instrumental)" in filename_lower or "(vocal)" in filename_lower:
                    continue
                
                # Получаем имя файла без расширения для анализа
                file_stem = source_path.stem
                
                # Нормализуем имя файла для сравнения
                normalized_stem = normalize(file_stem)
                
                # Проверяем строгое соответствие: и artist И title должны присутствовать
                if normalized_artist and normalized_title:
                    # Оба должны присутствовать в нормализованном имени файла
                    if normalized_artist in normalized_stem and normalized_title in normalized_stem:
                        # Извлекаем artist и title из state.json или из имени файла
                        track_artist = state_data.get("track_artist") or state_data.get("artist") or ""
                        track_title = state_data.get("track_title") or state_data.get("title") or file_stem
                        
                        # Пробуем получить дату создания файла
                        try:
                            ctime = source_path.stat().st_ctime
                        except OSError:
                            ctime = 0
                        
                        results.append({
                            "source": "local",
                            "file_path": str(source_path),
                            "track_source": track_source,
                            "artist": track_artist,
                            "title": track_title,
                            "ctime": ctime,
                            "state": state_data,
                            "track_dir": str(item),
                        })
        except OSError as exc:
            self._logger.error("Error scanning local storage: %s", exc)

        # Сортируем по времени создания (убывание) и берём топ-5
        results.sort(key=lambda x: x.get("ctime", 0), reverse=True)
        return results[:5]

    async def _search_yandex(self, query: str) -> list[dict[str, Any]]:
        """Search for tracks on Yandex Music.

        Returns top-5 results with metadata (title, artist, album).
        """
        if not self._settings.yandex_music_token:
            return []

        try:
            downloader = YandexMusicDownloader(token=self._settings.yandex_music_token)
            client = downloader._get_client()

            # Выполняем поиск
            search_results = client.search(query=query)

            results: list[dict[str, Any]] = []

            # Обрабатываем результаты поиска
            if search_results and hasattr(search_results, 'tracks'):
                tracks = search_results.tracks
                if tracks and hasattr(tracks, 'results'):
                    for track in tracks.results[:5]:
                        artists = ", ".join(artist.name for artist in track.artists) if track.artists else "Unknown"
                        results.append({
                            "source": "yandex",
                            "track_id": track.id,
                            "title": track.title,
                            "artist": artists,
                            "album": track.album.title if track.album else None,
                        })

            return results[:5]

        except Exception as exc:
            self._logger.error("Error searching Yandex Music: %s", exc)
            return []

    async def _handle_local_track(
        self,
        message: types.Message,
        track_info: dict[str, Any],
        user_id: int,
    ) -> None:
        """Handle a local track selection with smart resume logic.

        Logic:
        1. If state.json exists:
           - If status=COMPLETED: restart from RENDER_VIDEO step only
           - If status=FAILED: continue from last failed step (auto-resume)
           - Otherwise: continue from current step
        2. If no state.json:
           - Copy file to target folder with normalization
           - Check if file already exists in target folder
           - Run full pipeline
        """
        file_path = track_info.get("file_path")
        track_dir = track_info.get("track_dir")

        if not file_path or not track_dir:
            await message.answer("❌ Не удалось определить путь к файлу.")
            return

        track_dir_path = Path(track_dir)
        file_path_obj = Path(file_path)

        # Проверяем наличие state.json в папке с найденным треком
        state_path = track_dir_path / "state.json"
        pipeline_state: PipelineState | None = None
        track_id: str

        if state_path.exists():
            try:
                pipeline_state = PipelineState.model_validate_json(
                    state_path.read_text(encoding="utf-8")
                )
                track_id = pipeline_state.track_id
            except Exception:
                track_id = uuid.uuid4().hex
                pipeline_state = None
        else:
            track_id = uuid.uuid4().hex

        # Логика обработки в зависимости от наличия и статуса state.json
        if pipeline_state is not None and state_path.exists():
            # === СЛУЧАЙ 1: Есть state.json - используем существующую логику ===
            await self._handle_local_track_with_state(
                message, track_dir_path, pipeline_state, track_id, user_id
            )
        else:
            # === СЛУЧАЙ 2: Нет state.json - создаём с нуля ===
            await self._handle_local_track_new(
                message, file_path_obj, track_id, user_id
            )

    async def _handle_local_track_with_state(
        self,
        message: types.Message,
        track_dir_path: Path,
        pipeline_state: PipelineState,
        track_id: str,
        user_id: int,
    ) -> None:
        """Handle local track when state.json already exists."""
        # Используем существующий track_dir из state.json
        target_dir = track_dir_path

        # Определяем стартовый шаг на основе статуса
        start_step: PipelineStep | None = None
        status = pipeline_state.status

        # === ПРОВЕРКА: Если COMPLETED и output_file существует - отправляем ссылку без запуска пайплайна ===
        if status == PipelineStatus.COMPLETED and pipeline_state.output_file:
            output_file_path = Path(pipeline_state.output_file)
            if output_file_path.exists():
                # Файл существует - используем ту же логику формирования ссылки, что и в _send_result_video()
                download_url: str | None = pipeline_state.download_url

                # Если download_url не сохранён в state.json, формируем его по аналогии с pipeline.py
                if not download_url:
                    base_url = self._settings.content_external_url
                    if not base_url.startswith("http://") and not base_url.startswith("https://"):
                        base_url = f"https://{base_url}"
                    base_url = base_url.rstrip("/")
                    endpoint = "" if base_url.endswith("/music") else "/music"
                    filepath = str(output_file_path)
                    from urllib.parse import quote
                    encoded_path = quote(filepath, safe="/")
                    download_url = f"{base_url}{endpoint}?getfile={encoded_path}"

                # Отправляем сообщение со ссылкой на готовый файл
                download_url_msg = f"\n📥 Скачать: <a href='{download_url}'>ссылка</a>" if download_url else ""
                await message.answer(
                    f"🎉 Видео уже готово!\n"
                    f"track_id: <code>{track_id}</code>\n"
                    f"Файл: <code>{output_file_path.name}</code>"
                    f"{download_url_msg}",
                    parse_mode="HTML",
                )
                return
            # Если файл не существует - продолжаем с обычной логикой (перезапуск RENDER_VIDEO)

        if status == PipelineStatus.COMPLETED:
            # Если пайплайн завершён - повторяем только RENDER_VIDEO
            start_step = PipelineStep.RENDER_VIDEO
            status_msg = f"▶️ Пайплайн уже завершён. Повторяю шаг RENDER_VIDEO..."
        elif status == PipelineStatus.FAILED:
            # Если пайплайн упал - продолжаем с последнего шага
            # Pайплайн сам определит шаг на основе current_step
            start_step = None  # Авто-продолжение
            status_msg = f"▶️ Пайпайн был прерван. Продолжаю с последнего шага..."
        else:
            # Для других статусов - тоже авто-продолжение
            start_step = None
            status_msg = f"▶️ Продолжаю обработку локального трека..."

        # Создаём UserRequest
        source_file = pipeline_state.track_source or str(target_dir / pipeline_state.track_file_name) if pipeline_state.track_file_name else ""
        request = UserRequest(
            user_id=user_id,
            track_id=track_id,
            source_type="file",
            source_url_or_file_path=source_file,
            track_folder=str(target_dir),
        )
        pipeline = KaraokePipeline(request, self._settings)

        await message.answer(
            f"{status_msg}\n"
            f"Папка: <code>{target_dir.name}</code>",
            parse_mode="HTML",
        )

        # Отправляем начальное сообщение
        initial_msg = await message.reply("⏳ Начинаю обработку...")
        notification_chat_id = initial_msg.chat.id
        notification_message_id = initial_msg.message_id

        async def _step_progress(msg: str) -> None:
            try:
                await message.bot.edit_message_text(
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                    text=msg,
                )
            except Exception as edit_err:
                self._logger.warning("Failed to edit notification: %s", edit_err)
                await message.answer(msg)

        try:
            result = await pipeline.run(_step_progress, start_from_step=start_step)
        except LyricsNotFoundError:
            await message.answer(
                "🎵 Не удалось автоматически найти текст песни.\n\n"
                "Пожалуйста, пришлите полный текст песни в следующем сообщении."
            )
            pipeline_state.track_id = track_id
            try:
                state_path = target_dir / "state.json"
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state.json with track_id for track_id=%s: %s",
                    track_id, exc,
                )
            return

        if result.status == PipelineStatus.COMPLETED:
            await self._send_result_video(message, result)
        else:
            await message.answer(
                f"💔 Обработка завершена с ошибкой.\n"
                f"Причина: {result.error_message}",
                parse_mode="HTML",
            )

    async def _handle_local_track_new(
        self,
        message: types.Message,
        file_path_obj: Path,
        track_id: str,
        user_id: int,
    ) -> None:
        """Handle local track when there's no existing state.json."""
        # Используем нормализацию имени файла
        track_stem = normalize_filename(file_path_obj.stem)

        # Определяем целевую папку
        target_dir = self._tracks_root_dir / track_stem
        target_dir.mkdir(parents=True, exist_ok=True)

        # Проверяем, находится ли файл уже в целевой папке
        target_file = target_dir / file_path_obj.name

        if file_path_obj.exists() and file_path_obj != target_file:
            # Файл в другом месте - копируем в целевую папку
            if not target_file.exists():
                try:
                    shutil.copy2(file_path_obj, target_file)
                    self._logger.info(
                        "Copied file from %s to %s", file_path_obj, target_file
                    )
                except OSError as exc:
                    self._logger.error(
                        "Failed to copy file from %s to %s: %s",
                        file_path_obj, target_file, exc,
                    )
                    await message.answer(
                        f"❌ Не удалось скопировать файл в целевую папку: {exc}"
                    )
                    return
            actual_file_path = target_file
        elif target_file.exists():
            # Файл уже в целевой папке
            actual_file_path = target_file
        else:
            # Файл не найден - используем исходный путь
            actual_file_path = file_path_obj

        # Создаём новый PipelineState
        pipeline_state = PipelineState(
            track_id=track_id,
            user_id=user_id,
            status=PipelineStatus.PENDING,
            track_source=str(actual_file_path),
            track_file_name=actual_file_path.name,
            track_stem=track_stem,
        )

        # Сохраняем state.json
        state_path = target_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error(
                "Failed to write state.json for track_id=%s: %s",
                track_id, exc,
            )

        # Создаём UserRequest
        request = UserRequest(
            user_id=user_id,
            track_id=track_id,
            source_type="file",
            source_url_or_file_path=str(actual_file_path),
            track_folder=str(target_dir),
        )
        pipeline = KaraokePipeline(request, self._settings)

        await message.answer(
            f"▶️ Запускаю полный пайплайн для локального трека...\n"
            f"Файл: <code>{actual_file_path.name}</code>\n"
            f"Папка: <code>{target_dir.name}</code>",
            parse_mode="HTML",
        )

        # Отправляем начальное сообщение
        initial_msg = await message.reply("⏳ Начинаю обработку...")
        notification_chat_id = initial_msg.chat.id
        notification_message_id = initial_msg.message_id

        async def _step_progress(msg: str) -> None:
            try:
                await message.bot.edit_message_text(
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                    text=msg,
                )
            except Exception as edit_err:
                self._logger.warning("Failed to edit notification: %s", edit_err)
                await message.answer(msg)

        try:
            # Запускаем полный пайплайн (без start_from_step)
            result = await pipeline.run(_step_progress)
        except LyricsNotFoundError:
            await message.answer(
                "🎵 Не удалось автоматически найти текст песни.\n\n"
                "Пожалуйста, пришлите полный текст песни в следующем сообщении."
            )
            pipeline_state.track_id = track_id
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state.json with track_id for track_id=%s: %s",
                    track_id, exc,
                )
            return

        if result.status == PipelineStatus.COMPLETED:
            await self._send_result_video(message, result)
        else:
            await message.answer(
                f"💔 Обработка завершена с ошибкой.\n"
                f"Причина: {result.error_message}",
                parse_mode="HTML",
            )

    async def _handle_yandex_track_search_result(
        self,
        message: types.Message,
        track_info: dict[str, Any],
        state: FSMContext,
        user_id: int,
    ) -> None:
        """Handle a Yandex Music track selection - download and run full pipeline."""
        yandex_track_id = track_info.get("track_id")
        if not yandex_track_id:
            await message.answer("❌ Не удалось определить ID трека.")
            return

        # Создаём URL для скачивания
        track_url = f"https://music.yandex.ru/track/{yandex_track_id}"

        await message.answer(
            f"⏳ Загружаю трек с Яндекс Музыки...\n"
            f"{track_info.get('artist', '')} - {track_info.get('title', '')}",
        )

        # Запускаем загрузку через существующий обработчик
        await self._handle_yandex_music_url(message, state, track_url)

    async def _handle_youtube_url(
        self,
        message: types.Message,
        state: FSMContext,
        url: str,
    ) -> None:
        """Handle YouTube URL: download audio and proceed with pipeline."""
        self._ensure_tracks_root()

        user_id: int = message.from_user.id if message.from_user else 0
        track_id = uuid.uuid4().hex  # generate early for notification

        # Create preliminary PipelineState (without track details)
        pipeline_state = PipelineState(
            track_id=track_id,
            user_id=user_id,
            status=PipelineStatus.PENDING,
        )
        # Send initial notification (reply) and store its ID
        await self._send_or_edit_notification(
            message,
            pipeline_state,
            f"⏳ Загружаю аудио с YouTube...\nURL: {url}",
            parse_mode="HTML",
        )
        # Update state.json (will be created later when we have track_dir)

        # Initialize YouTube downloader
        downloader = YouTubeDownloader(quality="best")

        try:
            # First get video metadata to build directory name
            meta = await downloader.get_track_info(url)
        except Exception as exc:
            self._logger.error(
                "Failed to get video metadata from YouTube: %s", exc
            )
            # Edit notification with error
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                f"❌ Не удалось получить информацию о видео: {exc}",
                parse_mode="HTML",
            )
            return

        # Check duration (must be at least 1 minute)
        if meta.duration < 60:
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                "Полученное видео не является музыкальной композицией "
                "(длительность менее 1 минуты).",
                parse_mode="HTML",
            )
            return

        # Build track name like in standard flow (using _build_track_name)
        track_name = self._build_track_name(
            meta.title or "video",
            meta.artist,
            meta.title
        )

        # Create track directory with normalized name (not UUID!)
        track_dir = self._tracks_root_dir / track_name
        track_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Now download audio to the created directory
            track_info = await downloader.download(url, track_dir)
        except Exception as exc:
            self._logger.error(
                "Failed to download audio from YouTube: %s", exc
            )
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                f"❌ Не удалось скачать аудио с YouTube: {exc}",
                parse_mode="HTML",
            )
            return

        # Verify duration again using ffprobe (optional but safe)
        duration, _, _ = await self._probe_audio(track_info.local_path)
        if duration is None or duration < 60:
            await self._send_or_edit_notification(
                message,
                pipeline_state,
                "Полученный аудиофайл не является музыкальной композицией "
                "(длительность менее 1 минуты или не удалось определить длительность).",
                parse_mode="HTML",
            )
            return

        # Update PipelineState with track details
        pipeline_state.track_file_name = track_info.local_path.name
        pipeline_state.track_source = str(track_info.local_path)
        pipeline_state.track_stem = track_info.track_stem

        state_path = track_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error(
                "Failed to write state.json for track_id=%s: %s", track_id, exc
            )

        # Edit notification with success message
        await self._send_or_edit_notification(
            message,
            pipeline_state,
            f"✅ Аудио загружено с YouTube!\n"
            f"track_id: <code>{track_id}</code>\n"
            f"Название: <code>{track_info.track_stem}</code>\n"
            f"Длительность: {int(duration // 60)}:{int(duration % 60):02d}",
            parse_mode="HTML",
        )

        # Ask for language and continue with pipeline
        await self._ask_for_lang(message, state, track_id, str(track_dir), pipeline_state)
