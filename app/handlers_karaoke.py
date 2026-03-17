import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .config_watcher import ConfigWatcher
from .models import (
    LyricsChoiceStates,
    LyricsConfirmStates,
    LyricsStates,
    PipelineResult,
    PipelineState,
    PipelineStatus,
    PipelineStep,
    SearchStates,
    SourceType,
    TrackLangStates,
)
from .pipeline import KaraokePipeline, LyricsNotFoundError, WaitingForInputError, _ORDERED_STEPS
from .yandex_music_downloader import YandexMusicDownloader
from .utils import normalize_filename


class KaraokeHandlers:
    def __init__(self, settings: Settings, config_watcher: ConfigWatcher | None = None) -> None:
        self._config_watcher = config_watcher
        self._settings_obj = settings
        self._tracks_root_dir: Path = settings.tracks_root_dir
        self._logger = logging.getLogger(__name__)
        self.router = Router()
        self._register_handlers()

        # Регистрируем callback для обновления _tracks_root_dir при перезагрузке конфигурации
        if config_watcher is not None:
            config_watcher.add_reload_callback(self._on_settings_reloaded)

    @property
    def _settings(self) -> Settings:
        """Возвращает актуальные настройки через ConfigWatcher (если задан) или исходный объект."""
        if self._config_watcher is not None:
            return self._config_watcher.get_settings()
        return self._settings_obj

    def _on_settings_reloaded(self, new_settings: Settings) -> None:
        """Callback, вызываемый при перезагрузке конфигурации."""
        self._tracks_root_dir = new_settings.tracks_root_dir
        self._logger.info(
            "KaraokeHandlers: конфигурация обновлена, tracks_root_dir=%s",
            self._tracks_root_dir,
        )

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def _is_user_allowed(self, message: types.Message) -> bool:
        """Return True if the sender's user_id is in the allowed list."""
        user_id = message.from_user.id if message.from_user else None
        if user_id is None:
            return False
        if self._settings.is_user_denied(user_id):
            return False
        if self._settings.is_user_allowed(user_id):
            return True
        allowed = self._settings.tlg_allowed_id
        if not allowed:
            return True
        return user_id in allowed

    async def _reject_unauthorized(self, message: types.Message) -> None:
        """Send a rejection notice and log the attempt."""
        user_id = message.from_user.id if message.from_user else None
        user_name = message.from_user.full_name if message.from_user else None
        self._logger.warning("Unauthorized access attempt from user_id=%s", user_id)
        await self._notify_admin_of_unauthorized_access(message, user_id, user_name)
        await message.answer("⛔ У вас нет доступа к этому боту.")

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def handle_start(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await state.clear()
            await message.answer(
                "🎤 Караоке-бот\n\n"
                "Я создаю видео с караоке-эффектом — слова подсвечиваются синхронно с вокалом.\n\n"
                "📎 Отправьте аудиофайл (mp3, flac) или ссылку (YouTube, Яндекс.Музыка, любой URL).\n\n"
                "🎵 Результат — MP4 с тремя аудиодорожками:\n"
                "   • Instrumental (минус)\n"
                "   • Original (оригинал)\n"
                "   • Instrumental+Voice (микс)\n\n"
                "⏱ Длительность трека: от 1 минуты."
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
            await self._start_pipeline(
                message=message,
                state=state,
                source_type=SourceType.TELEGRAM_FILE,
                source=original_name,
                telegram_file_id=audio.file_id,
            )

        @self.router.message(F.video)
        async def handle_video(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            video = message.video
            if video is None:
                return
            original_name = video.file_name or f"video_{video.file_unique_id}.mp4"
            await self._start_pipeline(
                message=message,
                state=state,
                source_type=SourceType.TELEGRAM_FILE,
                source=original_name,
                telegram_file_id=video.file_id,
            )

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

            pipeline_state, track_dir = result
            track_name = track_dir.name

            if pipeline_state.status == PipelineStatus.COMPLETED:
                if pipeline_state.current_step is None:
                    await message.answer(
                        "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                    )
                    return
                current_index = _ORDERED_STEPS.index(pipeline_state.current_step)
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
                await self._run_from_step(message, track_dir, pipeline_state, next_step, state)

            elif pipeline_state.status == PipelineStatus.FAILED:
                if pipeline_state.current_step is None:
                    await message.answer(
                        "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                    )
                    return
                step_name = pipeline_state.current_step.value
                await message.answer(
                    f"🔁 Повторяю шаг {step_name}...\n"
                    f"track: <code>{track_name}</code>",
                    parse_mode="HTML",
                )
                await self._run_from_step(message, track_dir, pipeline_state, pipeline_state.current_step, state)

            else:
                await message.answer(
                    "❌ Нет активного трека для продолжения. Пожалуйста, начните новую обработку."
                )

        @self.router.message(Command("step_download"))
        async def handle_step_download(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.DOWNLOAD, state)

        @self.router.message(Command("step_language"))
        async def handle_step_language(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.ASK_LANGUAGE, state)

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

        @self.router.message(Command("step_chorus"))
        async def handle_step_chorus(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.DETECT_CHORUS, state)

        @self.router.message(Command("step_mix"))
        async def handle_step_mix(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.MIX_AUDIO, state)

        @self.router.message(Command("step_transcribe"))
        async def handle_step_transcribe(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.TRANSCRIBE, state)

        @self.router.message(Command("step_generate_lyrics"))
        async def handle_step_generate_lyrics(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
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

            pipeline_state, track_dir = result
            track_name = track_dir.name

            # Автоматически устанавливаем флаг для ручного запуска GENERATE_LYRICS
            if not pipeline_state.use_transcription_as_lyrics:
                pipeline_state.use_transcription_as_lyrics = True
                state_path = track_dir / "state.json"
                try:
                    state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
                    self._logger.info(
                        "Auto-set use_transcription_as_lyrics=true for track_id=%s (manual step command)",
                        pipeline_state.track_id
                    )
                except OSError as exc:
                    self._logger.error("Failed to update state.json with flag for track_id=%s: %s", pipeline_state.track_id, exc)

            step_name = PipelineStep.GENERATE_LYRICS.value
            sent_msg = await message.answer(
                f"▶️ Запускаю обработку с шага {step_name}...\n"
                f"track: <code>{track_name}</code>",
                parse_mode="HTML",
            )

            # Update state with new notification IDs
            pipeline_state.notification_chat_id = sent_msg.chat.id
            pipeline_state.notification_message_id = sent_msg.message_id
            state_path = track_dir / "state.json"
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error(
                    "Failed to update state.json with notification IDs for track_id=%s: %s",
                    pipeline_state.track_id, exc,
                )

            await self._run_from_step(message, track_dir, pipeline_state, PipelineStep.GENERATE_LYRICS, state)

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

        @self.router.message(Command("step_send"))
        async def handle_step_send(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return
            await self._handle_step_command(message, PipelineStep.SEND_VIDEO, state)

        @self.router.message(Command("step_visualize"))
        async def handle_step_visualize(message: types.Message, state: FSMContext) -> None:  # type: ignore[unused-ignore]
            """Send visualization file to user if it exists."""
            if not self._is_user_allowed(message):
                await self._reject_unauthorized(message)
                return

            caller_user_id = message.from_user.id if message.from_user else None
            result = self._find_latest_state(user_id=caller_user_id)

            if result is None:
                await message.answer(
                    "❌ Нет активного трека. Пожалуйста, начните новую обработку."
                )
                return

            pipeline_state, track_dir = result
            track_name = track_dir.name

            # Check if visualization file exists
            viz_file_str = pipeline_state.visualization_file
            if not viz_file_str:
                await message.answer(
                    f"❌ Файл визуализации не найден для трека <code>{track_name}</code>.\n"
                    f"Убедитесь, что шаг GENERATE_ASS выполнен с включённым TRACK_VISUALIZATION_ENABLED.",
                    parse_mode="HTML",
                )
                return

            viz_path = Path(viz_file_str)
            if not viz_path.exists():
                await message.answer(
                    f"❌ Файл визуализации не существует: <code>{viz_path.name}</code>",
                    parse_mode="HTML",
                )
                return

            # Send the visualization file
            try:
                from aiogram.types import FSInputFile
                viz_file = FSInputFile(viz_path, filename=viz_path.name)
                await message.answer_photo(
                    photo=viz_file,
                    caption=f"📊 Визуализация сегментирования трека\n<code>{track_name}</code>",
                    parse_mode="HTML",
                )
                self._logger.info(
                    "Sent visualization file to user_id=%s: %s",
                    caller_user_id, viz_path
                )
            except Exception as exc:
                self._logger.error(
                    "Failed to send visualization file for track_id=%s: %s",
                    pipeline_state.track_id, exc
                )
                await message.answer(
                    f"❌ Ошибка при отправке файла визуализации: {exc}"
                )

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

            user_id = message.from_user.id if message.from_user else None
            user_name = message.from_user.full_name if message.from_user else None
            self._logger.info(
                "Поиск трека: пользователь user_id=%s (%s) запросил '%s'",
                user_id, user_name, query
            )

            await message.answer("🔍 Ищу трек...")

            artist = None
            title = None
            parts = re.split(r'\s*-\s*', query, 1)
            if len(parts) == 2:
                artist = parts[0].strip()
                title = parts[1].strip()
            else:
                title = query

            local_results = await self._search_local(artist, title)
            yandex_results: list[dict[str, Any]] = []
            if not local_results and self._settings.yandex_music_token:
                yandex_results = await self._search_yandex(f"{artist} {title}")

            all_results = local_results + yandex_results

            if not all_results:
                await message.answer(
                    "🔍 Трек не найден ни в локальном хранилище, ни на Яндекс Музыке.\n"
                    "Попробуйте изменить запрос или добавить трек другим способом."
                )
                await state.clear()
                return

            all_results = all_results[:5]

            self._logger.info(
                "Результаты поиска для запроса '%s': локальные=%d, яндекс=%d, всего=%d",
                query, len(local_results), len(yandex_results), len(all_results)
            )

            await state.update_data(search_results=all_results, search_query=query)
            await state.set_state(SearchStates.waiting_for_selection)

            result_text = "🎵 Найденные треки:\n\n"
            for i, track in enumerate(all_results, 1):
                source_label = "📁 Локально" if track.get("source") == "local" else "🎧 Яндекс Музыка"
                artist_name = track.get("artist", "Unknown")
                title_name = track.get("title", "Unknown")
                result_text += f"{i}. {artist_name} - {title_name}\n   {source_label}\n\n"

            result_text += "Выберите номер трека для обработки:"

            keyboard_buttons = [
                InlineKeyboardButton(text=str(i), callback_data=f"search_select:{i-1}")
                for i in range(1, len(all_results) + 1)
            ]
            keyboard_buttons.append(
                InlineKeyboardButton(text="Я", callback_data="search_yandex:1")
            )
            keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])

            await message.answer(result_text, reply_markup=keyboard)

        # ----- FSM: waiting for search selection -----
        @self.router.callback_query(SearchStates.waiting_for_selection, F.data.startswith("search_select:"))
        async def handle_search_selection(callback: types.CallbackQuery, state: FSMContext) -> None:  # type: ignore[unused-ignore]
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

            try:
                index = int(callback.data.split(":")[1])
            except (IndexError, ValueError):
                await callback.answer("❌ Неверный выбор.", show_alert=True)
                return

            data = await state.get_data()
            search_results: list[dict[str, Any]] = data.get("search_results", [])

            if index < 0 or index >= len(search_results):
                await callback.answer("❌ Неверный номер трека.", show_alert=True)
                return

            selected_track = search_results[index]
            await state.clear()

            if selected_track.get("source") == "local":
                await self._handle_local_track(
                    callback.message,  # type: ignore[arg-type]
                    selected_track,
                    callback.from_user.id if callback.from_user else 0,
                    state,
                )
            else:
                # Яндекс Музыка — запускаем через _start_pipeline
                yandex_track_id = selected_track.get("track_id")
                if not yandex_track_id:
                    await callback.message.answer("❌ Не удалось определить ID трека.")  # type: ignore[union-attr]
                    return
                track_url = f"https://music.yandex.ru/track/{yandex_track_id}"
                await callback.message.answer(  # type: ignore[union-attr]
                    f"⏳ Загружаю трек с Яндекс Музыки...\n"
                    f"{selected_track.get('artist', '')} - {selected_track.get('title', '')}",
                )
                await self._start_pipeline(
                    message=callback.message,  # type: ignore[arg-type]
                    state=state,
                    source_type=SourceType.YANDEX_MUSIC,
                    source=track_url,
                    user_id=callback.from_user.id if callback.from_user else 0,
                )

        # Обработчик кнопки "Я" для поиска на Яндекс Музыке
        @self.router.callback_query(SearchStates.waiting_for_selection, F.data.startswith("search_yandex:"))
        async def handle_search_yandex(callback: types.CallbackQuery, state: FSMContext) -> None:  # type: ignore[unused-ignore]
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

            data = await state.get_data()
            query = data.get("search_query", "")
            if not query:
                await callback.answer("❌ Не удалось получить запрос.", show_alert=True)
                return

            await state.clear()

            if not self._settings.yandex_music_token:
                await callback.message.answer("❌ Токен Яндекс Музыки не настроен.")  # type: ignore[union-attr]
                return

            await callback.message.answer("🔍 Ищу на Яндекс Музыке...")  # type: ignore[union-attr]

            yandex_results = await self._search_yandex(query)

            if not yandex_results:
                await callback.message.answer("🔍 Трек не найден на Яндекс Музыке.")  # type: ignore[union-attr]
                return

            yandex_results = yandex_results[:5]

            await state.update_data(search_results=yandex_results, search_query=query)
            await state.set_state(SearchStates.waiting_for_selection)

            result_text = "🎵 Найденные треки на Яндекс Музыке:\n\n"
            for i, track in enumerate(yandex_results, 1):
                artist_name = track.get("artist", "Unknown")
                title_name = track.get("title", "Unknown")
                lyrics_info=track.get("lyrics_info","Unknown")
                lrc_exists=False
                txt_exists=False
                lrc=""
                if lyrics_info!="Unknown":
                    lrc_exists=lyrics_info.get("has_available_sync_lyrics",False)
                    if lrc_exists:
                        lrc="lrc,"
                    txt_exists=lyrics_info.get("has_available_text_lyrics",False)
                    if txt_exists:
                        lrc+="txt"
                result_text += f"{i}. {artist_name} - {title_name}\n   🎧 Яндекс Музыка {lrc}\n\n"

            result_text += "Выберите номер трека для обработки:"

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

            track_dir = self._find_track_dir_by_id(track_id)
            if track_dir is None:
                await state.clear()
                await message.answer("❌ Папка трека не найдена. Пожалуйста, начните обработку заново.")
                return

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

            lyrics_file = track_dir / f"{track_stem}_lyrics.txt"
            try:
                lyrics_file.write_text(lyrics_text, encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to write lyrics file for track_id=%s: %s", track_id, exc)
                await state.clear()
                await message.answer("❌ Не удалось сохранить текст песни. Попробуйте ещё раз.")
                return

            pipeline_state.source_lyrics_file = str(lyrics_file)
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json for track_id=%s: %s", track_id, exc)

            await state.clear()

            await self._send_or_edit_notification(
                message,
                pipeline_state,
                "✅ Текст песни получен. Продолжаю обработку...",
            )

            # Continue pipeline from SEPARATE step (after GET_LYRICS)
            await self._run_from_step(message, track_dir, pipeline_state, PipelineStep.SEPARATE, state)

        # ----- Callback: lyrics choice (transcription or upload) -----
        @self.router.callback_query(LyricsChoiceStates.waiting_for_choice, F.data == "lyrics_choice:transcription")
        async def handle_lyrics_choice_transcription(callback: types.CallbackQuery, state: FSMContext) -> None:
            """Пользователь выбрал использовать транскрипцию."""
            # Получаем данные из FSM
            data = await state.get_data()
            track_id = data.get("track_id")
            track_folder = data.get("track_folder")

            if not track_id or not track_folder:
                await callback.answer("❌ Не удалось определить трек.", show_alert=True)
                await state.clear()
                return

            # Читаем state.json
            track_dir = Path(track_folder)
            state_path = track_dir / "state.json"
            try:
                pipeline_state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._logger.error("Failed to read state.json for track_id=%s: %s", track_id, exc)
                await callback.answer("❌ Ошибка чтения состояния трека.", show_alert=True)
                await state.clear()
                return

            # Устанавливаем флаг
            pipeline_state.use_transcription_as_lyrics = True
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json for track_id=%s: %s", track_id, exc)
                await callback.answer("❌ Ошибка сохранения состояния.", show_alert=True)
                await state.clear()
                return

            # Очищаем FSM
            await state.clear()
            await callback.answer("Выбран вариант с транскрипцией")

            # Продолжаем пайплайн с SEPARATE
            if callback.message:
                await self._run_from_step(callback.message, track_dir, pipeline_state, PipelineStep.SEPARATE, state)

        @self.router.callback_query(LyricsChoiceStates.waiting_for_choice, F.data == "lyrics_choice:upload")
        async def handle_lyrics_choice_upload(callback: types.CallbackQuery, state: FSMContext) -> None:
            """Пользователь выбрал загрузить текст вручную."""
            # Переходим в FSM ожидания текста
            await state.set_state(LyricsStates.waiting_for_lyrics)

            text = "Пожалуйста, пришлите полный текст песни в следующем сообщении."
            await callback.answer("Ожидаю текст песни")

            if callback.message:
                try:
                    await callback.message.edit_text(text)
                except Exception:
                    await callback.message.answer(text)

        # ----- Callback: lyrics confirmation (ok or upload) -----
        @self.router.callback_query(LyricsConfirmStates.waiting_for_confirmation, F.data == "lyrics_confirm:ok")
        async def handle_lyrics_confirm_ok(callback: types.CallbackQuery, state: FSMContext) -> None:
            """Пользователь подтвердил текст из транскрипции."""
            # Получаем данные из FSM
            data = await state.get_data()
            track_id = data.get("track_id")
            track_folder = data.get("track_folder")

            if not track_id or not track_folder:
                await callback.answer("❌ Не удалось определить трек.", show_alert=True)
                await state.clear()
                return

            # Читаем state.json
            track_dir = Path(track_folder)
            state_path = track_dir / "state.json"
            try:
                pipeline_state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._logger.error("Failed to read state.json for track_id=%s: %s", track_id, exc)
                await callback.answer("❌ Ошибка чтения состояния трека.", show_alert=True)
                await state.clear()
                return

            # Переименовываем временный файл в финальный
            if not pipeline_state.temp_lyrics_file:
                await callback.answer("❌ Временный файл с текстом не найден.", show_alert=True)
                await state.clear()
                return

            temp_path = Path(pipeline_state.temp_lyrics_file)
            stem = pipeline_state.track_stem or "track"
            final_path = track_dir / f"{stem}_lyrics.txt"

            try:
                temp_path.rename(final_path)
            except OSError as exc:
                self._logger.error("Failed to rename temp lyrics file for track_id=%s: %s", track_id, exc)
                await callback.answer("❌ Ошибка сохранения текста.", show_alert=True)
                await state.clear()
                return

            # Обновляем состояние
            pipeline_state.source_lyrics_file = str(final_path)
            pipeline_state.temp_lyrics_file = None
            try:
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json for track_id=%s: %s", track_id, exc)

            # Очищаем FSM
            await state.clear()
            await callback.answer("Текст подтверждён")

            # Продолжаем с DETECT_CHORUS (после GENERATE_LYRICS)
            if callback.message:
                await self._run_from_step(callback.message, track_dir, pipeline_state, PipelineStep.DETECT_CHORUS, state)

        @self.router.callback_query(LyricsConfirmStates.waiting_for_confirmation, F.data == "lyrics_confirm:upload")
        async def handle_lyrics_confirm_upload(callback: types.CallbackQuery, state: FSMContext) -> None:
            """Пользователь хочет загрузить свой текст вместо транскрипции."""
            # Получаем данные
            data = await state.get_data()
            track_folder = data.get("track_folder")

            if not track_folder:
                await callback.answer("❌ Не удалось определить трек.", show_alert=True)
                await state.clear()
                return

            # Сбрасываем флаг и удаляем временный файл
            track_dir = Path(track_folder)
            state_path = track_dir / "state.json"
            try:
                pipeline_state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))

                # Удаляем временный файл если есть
                if pipeline_state.temp_lyrics_file:
                    temp_path = Path(pipeline_state.temp_lyrics_file)
                    if temp_path.exists():
                        temp_path.unlink()

                pipeline_state.use_transcription_as_lyrics = False
                pipeline_state.temp_lyrics_file = None
                state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except Exception as exc:
                self._logger.error("Failed to update state.json: %s", exc)

            # Переходим в FSM ожидания текста
            await state.set_state(LyricsStates.waiting_for_lyrics)

            text = "Пожалуйста, пришлите полный текст песни в следующем сообщении."
            await callback.answer("Ожидаю текст песни")

            if callback.message:
                try:
                    await callback.message.edit_text(text)
                except Exception:
                    await callback.message.answer(text)

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

            lang = (callback.data or "").split(":", 1)[-1]  # "ru" or "en"
            data = await state.get_data()
            track_id: str | None = data.get("track_id")
            track_folder: str | None = data.get("track_folder")

            await callback.answer()

            if not track_id or not track_folder:
                await state.clear()
                if callback.message:
                    await callback.message.answer(  # type: ignore[union-attr]
                        "❌ Не удалось определить трек. Пожалуйста, начните обработку заново."
                    )
                return

            track_dir = Path(track_folder)

            # Persist lang into state.json
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

            # Resume pipeline from GET_LYRICS step (after ASK_LANGUAGE)
            if callback.message:
                await self._run_from_step(
                    callback.message,  # type: ignore[arg-type]
                    track_dir,
                    pipeline_state,
                    PipelineStep.GET_LYRICS,
                    state,
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

            # Determine source type by URL
            if self._is_yandex_music_url(url):
                await self._start_pipeline(
                    message=message,
                    state=state,
                    source_type=SourceType.YANDEX_MUSIC,
                    source=url,
                )
            elif self._is_youtube_url(url):
                await self._start_pipeline(
                    message=message,
                    state=state,
                    source_type=SourceType.YOUTUBE,
                    source=url,
                )
            else:
                await self._start_pipeline(
                    message=message,
                    state=state,
                    source_type=SourceType.HTTP_URL,
                    source=url,
                )

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
    # Unified pipeline entry point
    # ------------------------------------------------------------------

    async def _start_pipeline(
        self,
        message: types.Message,
        state: FSMContext,
        source_type: SourceType,
        source: str,
        user_id: int | None = None,
        telegram_file_id: str | None = None,
    ) -> None:
        """Unified entry point for all pipeline starts.

        Creates PipelineState, saves state.json, sends initial notification,
        and runs the pipeline. Handles WaitingForInputError (ASK_LANGUAGE step)
        by transitioning to FSM state for language selection.
        """
        self._ensure_tracks_root()

        if user_id is None:
            user_id = message.from_user.id if message.from_user else 0

        # Create a temporary track folder (will be renamed by DOWNLOAD step)
        tmp_track_id = uuid.uuid4().hex
        tmp_track_dir = self._tracks_root_dir / f"_tmp_{tmp_track_id}"
        tmp_track_dir.mkdir(parents=True, exist_ok=True)

        # Create pipeline with new state
        pipeline = KaraokePipeline.create_new(
            settings=self._settings,
            user_id=user_id,
            source_type=source_type,
            source_url=source,
            track_folder=str(tmp_track_dir),
            bot=message.bot,
            telegram_file_id=telegram_file_id,
        )

        pipeline_state = pipeline.state
        track_id = pipeline_state.track_id

        # Send initial notification (reply to user's message)
        source_label = {
            SourceType.TELEGRAM_FILE: f"Файл: {source}",
            SourceType.YANDEX_MUSIC: f"Яндекс Музыка: {source}",
            SourceType.YOUTUBE: f"YouTube: {source}",
            SourceType.HTTP_URL: f"URL: {source}",
            SourceType.LOCAL_FILE: f"Локальный файл: {source}",
        }.get(source_type, source)

        sent = await message.reply(
            f"⏳ Принято в обработку.\n{source_label}",
            parse_mode="HTML",
        )
        pipeline_state.notification_chat_id = sent.chat.id
        pipeline_state.notification_message_id = sent.message_id

        # Save state with notification IDs
        state_path = tmp_track_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error("Failed to write initial state.json for track_id=%s: %s", track_id, exc)

        notification_chat_id = sent.chat.id
        notification_message_id = sent.message_id

        async def _progress(msg: str) -> None:
            try:
                await message.bot.edit_message_text(
                    text=msg,
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                )
            except Exception as edit_err:
                self._logger.warning("Failed to edit notification for track_id=%s: %s", track_id, edit_err)

        try:
            result = await pipeline.run(_progress)
        except WaitingForInputError:
            # Pipeline paused waiting for user input
            # Update state with new track_folder (may have changed during DOWNLOAD)
            pipeline_state = pipeline.state
            new_track_dir = pipeline.track_folder

            # Update notification IDs in state
            pipeline_state.notification_chat_id = notification_chat_id
            pipeline_state.notification_message_id = notification_message_id
            new_state_path = new_track_dir / "state.json"
            try:
                new_state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json after WaitingForInputError for track_id=%s: %s", track_id, exc)

            # Определяем, какой шаг вызвал ожидание
            current_step = pipeline_state.current_step

            if current_step == PipelineStep.ASK_LANGUAGE:
                await self._ask_for_lang(message, state, track_id, str(new_track_dir), pipeline_state)
            elif current_step == PipelineStep.GENERATE_LYRICS:
                await self._show_lyrics_confirmation(message, state, track_id, new_track_dir, pipeline_state)
            else:
                self._logger.warning("Unexpected WaitingForInputError at step %s for track_id=%s", current_step, track_id)
                try:
                    await message.bot.edit_message_text(
                        text=f"❌ Неожиданное ожидание ввода на шаге {current_step}",
                        chat_id=notification_chat_id,
                        message_id=notification_message_id,
                    )
                except Exception:
                    await message.answer(f"❌ Неожиданное ожидание ввода на шаге {current_step}")
            return
        except LyricsNotFoundError:
            # Pipeline paused at GET_LYRICS — ask user for lyrics
            pipeline_state = pipeline.state
            new_track_dir = pipeline.track_folder

            pipeline_state.notification_chat_id = notification_chat_id
            pipeline_state.notification_message_id = notification_message_id
            new_state_path = new_track_dir / "state.json"
            try:
                new_state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json for lyrics request track_id=%s: %s", track_id, exc)

            await self._ask_for_lyrics(message, state, track_id, new_track_dir, pipeline_state)
            return
        except Exception as exc:
            self._logger.error("Pipeline failed for track_id=%s: %s", track_id, exc)
            try:
                await message.bot.edit_message_text(
                    text=f"❌ Ошибка обработки: {exc}",
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                )
            except Exception:
                await message.answer(f"❌ Ошибка обработки: {exc}")
            return

        # Pipeline completed
        if result.status == PipelineStatus.COMPLETED:
            await self._send_result_notification(message, result, notification_chat_id, notification_message_id)
        else:
            error_msg = result.error_message or "Неизвестная ошибка"
            try:
                await message.bot.edit_message_text(
                    text=f"💔 Обработка завершена с ошибкой.\nПричина: {error_msg}",
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                )
            except Exception:
                await message.answer(f"💔 Обработка завершена с ошибкой.\nПричина: {error_msg}")

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
        decision: str,
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

        sent = await message.reply(text, reply_markup=keyboard)
        if pipeline_state:
            pipeline_state.notification_chat_id = sent.chat.id
            pipeline_state.notification_message_id = sent.message_id
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
        """Ask user to choose lyrics source: transcription or manual upload."""
        await state.set_state(LyricsChoiceStates.waiting_for_choice)
        await state.update_data(track_id=track_id, track_folder=str(track_dir))
        self._logger.info(
            "LyricsNotFoundError for track_id=%s — asking user for lyrics source", track_id
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="📝 Транскрипция", callback_data="lyrics_choice:transcription"),
                    InlineKeyboardButton(text="📤 Загрузить", callback_data="lyrics_choice:upload"),
                ]
            ]
        )

        text = "🎵 Не удалось автоматически найти текст песни.\n\nВыберите вариант:"

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
                    "Failed to edit notification for lyrics choice track_id=%s: %s.",
                    track_id,
                    exc,
                )

        await message.answer(text, reply_markup=keyboard)

    async def _show_lyrics_confirmation(
        self,
        message: types.Message,
        state: FSMContext,
        track_id: str,
        track_dir: Path,
        pipeline_state: PipelineState | None = None,
    ) -> None:
        """Показать пользователю сгенерированный текст для подтверждения."""
        await state.set_state(LyricsConfirmStates.waiting_for_confirmation)
        await state.update_data(track_id=track_id, track_folder=str(track_dir))

        # Читаем временный файл с текстом
        state_path = track_dir / "state.json"
        try:
            pipeline_state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
            temp_lyrics_path = Path(pipeline_state.temp_lyrics_file) if pipeline_state.temp_lyrics_file else None

            if temp_lyrics_path and temp_lyrics_path.exists():
                lyrics = temp_lyrics_path.read_text(encoding="utf-8")
            else:
                lyrics = "[Ошибка: текст не найден]"
        except Exception as exc:
            self._logger.error("Failed to read temp lyrics file for track_id=%s: %s", track_id, exc)
            lyrics = "[Ошибка: не удалось прочитать текст]"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Ок", callback_data="lyrics_confirm:ok"),
                    InlineKeyboardButton(text="📤 Загрузить", callback_data="lyrics_confirm:upload"),
                ]
            ]
        )

        # Отправляем первые 1000 символов текста
        preview = lyrics[:1000] + "..." if len(lyrics) > 1000 else lyrics
        text = f"📝 Текст, сгенерированный из транскрипции:\n\n<pre>{preview}</pre>\n\nПодтвердить или загрузить свой?"

        if pipeline_state and pipeline_state.notification_chat_id and pipeline_state.notification_message_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=pipeline_state.notification_chat_id,
                    message_id=pipeline_state.notification_message_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
                return
            except Exception as exc:
                self._logger.warning(
                    "Failed to edit notification for lyrics confirmation track_id=%s: %s.",
                    track_id,
                    exc,
                )

        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    # ------------------------------------------------------------------
    # Track search helpers
    # ------------------------------------------------------------------

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
            if user_id is not None and state.user_id is not None and state.user_id != user_id:
                continue
            if best_mtime is None or mtime > best_mtime:
                best_mtime = mtime
                best_state = state
                best_dir = subdir

        if best_state is None or best_dir is None:
            return None

        self._logger.info(
            "_find_latest_state: found track_id=%s in %s (mtime=%s)",
            best_state.track_id, best_dir, best_mtime
        )
        return best_state, best_dir

    def _find_state_by_id(self, track_id: str) -> tuple[PipelineState, Path] | None:
        """Find the track folder with the given track_id."""
        if not self._tracks_root_dir.exists():
            return None

        for subdir in self._tracks_root_dir.iterdir():
            if not subdir.is_dir():
                continue
            state_path = subdir / "state.json"
            if not state_path.exists():
                continue
            try:
                state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self._logger.warning("Failed to read state.json in %s: %s", subdir, exc)
                continue

            if state.track_id == track_id:
                self._logger.info("_find_state_by_id: found track_id=%s in %s", track_id, subdir)
                return state, subdir

        self._logger.warning("_find_state_by_id: track_id=%s not found", track_id)
        return None

    # ------------------------------------------------------------------
    # Step-command helpers
    # ------------------------------------------------------------------

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

        # Send new message and use its message_id for progress updates
        sent_msg = await message.answer(
            f"▶️ Запускаю обработку с шага {step_name}...\n"
            f"track: <code>{track_name}</code>",
            parse_mode="HTML",
        )

        # Update state with new notification IDs
        pipeline_state.notification_chat_id = sent_msg.chat.id
        pipeline_state.notification_message_id = sent_msg.message_id
        state_path = track_dir / "state.json"
        try:
            state_path.write_text(pipeline_state.model_dump_json(indent=2), encoding="utf-8")
        except OSError as exc:
            self._logger.error(
                "Failed to update state.json with notification IDs for track_id=%s: %s",
                pipeline_state.track_id, exc,
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
        """Reconstruct pipeline from saved state and run from the given step."""
        self._logger.info(
            "_run_from_step called for track_id=%s, step=%s",
            state.track_id,
            step.value,
        )

        pipeline = KaraokePipeline.from_state(
            settings=self._settings,
            state=state,
            track_folder=str(track_dir),
            bot=message.bot,
        )

        # Use notification IDs from state (already updated by caller)
        notification_chat_id = state.notification_chat_id
        notification_message_id = state.notification_message_id

        async def _step_progress(msg: str) -> None:
            try:
                await message.bot.edit_message_text(
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                    text=msg,
                )
            except Exception as edit_err:
                self._logger.warning(
                    "Failed to edit notification for track_id=%s: %s. Sending new message.",
                    state.track_id, edit_err
                )
                # Try to send a new message instead
                try:
                    await message.answer(msg)
                except Exception as send_err:
                    self._logger.error(
                        "Failed to send new notification message for track_id=%s: %s",
                        state.track_id, send_err
                    )

        try:
            result = await pipeline.run(_step_progress, start_from_step=step)
        except WaitingForInputError:
            # Pipeline paused waiting for user input
            updated_state = pipeline.state
            updated_state.notification_chat_id = notification_chat_id
            updated_state.notification_message_id = notification_message_id
            new_track_dir = pipeline.track_folder
            new_state_path = new_track_dir / "state.json"
            try:
                new_state_path.write_text(updated_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json after WaitingForInput: %s", exc)

            # Определяем, какой шаг вызвал ожидание
            current_step = updated_state.current_step

            if current_step == PipelineStep.ASK_LANGUAGE:
                await self._ask_for_lang(message, fsm_context, state.track_id, str(new_track_dir), updated_state)
            elif current_step == PipelineStep.GENERATE_LYRICS:
                await self._show_lyrics_confirmation(message, fsm_context, state.track_id, new_track_dir, updated_state)
            else:
                self._logger.warning("Unexpected WaitingForInputError at step %s in _run_from_step", current_step)
                try:
                    await message.bot.edit_message_text(
                        text=f"❌ Неожиданное ожидание ввода на шаге {current_step}",
                        chat_id=notification_chat_id,
                        message_id=notification_message_id,
                    )
                except Exception:
                    await message.answer(f"❌ Неожиданное ожидание ввода на шаге {current_step}")
            return
        except LyricsNotFoundError:
            updated_state = pipeline.state
            updated_state.notification_chat_id = notification_chat_id
            updated_state.notification_message_id = notification_message_id
            new_track_dir = pipeline.track_folder
            new_state_path = new_track_dir / "state.json"
            try:
                new_state_path.write_text(updated_state.model_dump_json(indent=2), encoding="utf-8")
            except OSError as exc:
                self._logger.error("Failed to update state.json for lyrics request: %s", exc)
            await self._ask_for_lyrics(message, fsm_context, state.track_id, new_track_dir, updated_state)
            return

        if result.status == PipelineStatus.COMPLETED:
            await self._send_result_notification(message, result, notification_chat_id, notification_message_id)
        else:
            error_msg = result.error_message or "Неизвестная ошибка"
            try:
                await message.bot.edit_message_text(
                    text=f"💔 Шаг завершён с ошибкой.\nПричина: {error_msg}",
                    chat_id=notification_chat_id,
                    message_id=notification_message_id,
                )
            except Exception:
                await message.answer(
                    f"💔 Шаг завершён с ошибкой.\n"
                    f"track_id: <code>{result.track_id}</code>\n"
                    f"Причина: {error_msg}",
                    parse_mode="HTML",
                )

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    async def _send_or_edit_notification(
        self,
        message: types.Message,
        pipeline_state: PipelineState,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        parse_mode: str = "HTML",
    ) -> None:
        """Send a new notification (reply) or edit existing one."""
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

        sent = await message.reply(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        pipeline_state.notification_chat_id = sent.chat.id
        pipeline_state.notification_message_id = sent.message_id
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

    async def _send_result_notification(
        self,
        message: types.Message,
        result: PipelineResult,
        notification_chat_id: int,
        notification_message_id: int,
    ) -> None:
        """Send completion notification (edit existing message)."""
        video_path_str = result.final_video_path
        download_url: str | None = None

        if video_path_str:
            video_path = Path(video_path_str)
            state_path = video_path.parent / "state.json"
            if state_path.exists():
                try:
                    state = PipelineState.model_validate_json(state_path.read_text(encoding="utf-8"))
                    download_url = state.download_url
                except Exception as exc:
                    self._logger.warning(
                        "Failed to read download_url from state.json for track_id=%s: %s",
                        result.track_id,
                        exc,
                    )

        download_url_msg = f"\n📥 Скачать: <a href='{download_url}'>ссылка</a>" if download_url else ""
        completion_text = (
            f"🎉 Обработка завершена успешно!\n"
            f"track_id: <code>{result.track_id}</code>\n"
            f"Видеофайл: <code>{video_path_str or 'не задан'}</code>"
            f"{download_url_msg}"
        )

        try:
            await message.bot.edit_message_text(
                text=completion_text,
                chat_id=notification_chat_id,
                message_id=notification_message_id,
                parse_mode="HTML",
            )
        except Exception:
            await message.answer(completion_text, parse_mode="HTML")

    # ------------------------------------------------------------------
    # Local track handling (from /search)
    # ------------------------------------------------------------------

    async def _handle_local_track(
        self,
        message: types.Message,
        track_info: dict[str, Any],
        user_id: int,
        state: FSMContext,
    ) -> None:
        """Handle a local track selection with smart resume logic."""
        file_path = track_info.get("file_path")
        track_dir = track_info.get("track_dir")

        if not file_path or not track_dir:
            await message.answer("❌ Не удалось определить путь к файлу.")
            return

        track_dir_path = Path(track_dir)
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

        if pipeline_state is not None:
            # === СЛУЧАЙ 1: Есть state.json ===
            # Если COMPLETED и output_file существует — отправляем ссылку
            if pipeline_state.status == PipelineStatus.COMPLETED and pipeline_state.output_file:
                output_file_path = Path(pipeline_state.output_file)
                if output_file_path.exists():
                    # Обновляем mtime файла state.json, чтобы этот трек стал "активным"
                    self._update_state_mtime(state_path)

                    result = PipelineResult(
                        track_id=track_id,
                        status=PipelineStatus.COMPLETED,
                        final_video_path=str(output_file_path),
                    )
                    # Send a new message with result
                    initial_msg = await message.reply("⏳ Отправляю результат...")
                    await self._send_result_notification(
                        message, result, initial_msg.chat.id, initial_msg.message_id
                    )
                    return

            # Определяем стартовый шаг
            if pipeline_state.status == PipelineStatus.COMPLETED:
                start_step = PipelineStep.RENDER_VIDEO
            elif pipeline_state.status == PipelineStatus.FAILED:
                start_step = pipeline_state.current_step
            else:
                start_step = pipeline_state.current_step

            if start_step is None:
                start_step = PipelineStep.SEPARATE

            # Обновляем mtime файла state.json, чтобы этот трек стал "активным"
            self._update_state_mtime(state_path)

            await self._run_from_step(message, track_dir_path, pipeline_state, start_step, state)
        else:
            # === СЛУЧАЙ 2: Нет state.json — запускаем через _start_pipeline ===
            await self._start_pipeline(
                message=message,
                state=state,
                source_type=SourceType.LOCAL_FILE,
                source=file_path,
                user_id=user_id,
            )

    # ------------------------------------------------------------------
    # Search functionality
    # ------------------------------------------------------------------

    async def _search_local(self, artist: str | None, title: str | None) -> list[dict[str, Any]]:
        """Search for tracks in local storage (TRACKS_ROOT_DIR)."""
        if not self._tracks_root_dir.exists():
            return []

        results: list[dict[str, Any]] = []
        audio_extensions = (".mp3", ".flac", ".mp4", ".avi")

        def normalize(text: str) -> str:
            return re.sub(r'[^\w\s]', '', text).lower().strip()

        normalized_artist = normalize(artist) if artist else None
        normalized_title = normalize(title) if title else None

        try:
            for item in self._tracks_root_dir.iterdir():
                if not item.is_dir():
                    continue

                state_path = item / "state.json"
                if not state_path.exists():
                    continue

                state_data = None
                try:
                    state_data = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    continue

                track_source = state_data.get("track_source")
                if not track_source:
                    continue

                source_path = Path(track_source)
                if not source_path.exists() or source_path.suffix.lower() not in audio_extensions:
                    continue

                filename_lower = source_path.name.lower()
                if "(instrumental)" in filename_lower or "(vocal)" in filename_lower:
                    continue

                file_stem = source_path.stem
                normalized_stem = normalize(file_stem)

                if normalized_artist and normalized_title:
                    if normalized_artist in normalized_stem and normalized_title in normalized_stem:
                        track_artist = state_data.get("track_artist") or state_data.get("artist") or ""
                        track_title = state_data.get("track_title") or state_data.get("title") or file_stem

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

        results.sort(key=lambda x: x.get("ctime", 0), reverse=True)
        return results[:5]

    async def _search_yandex(self, query: str) -> list[dict[str, Any]]:
        """Search for tracks on Yandex Music."""
        if not self._settings.yandex_music_token:
            return []

        self._logger.info("Поиск треков на Яндекс Музыке: '%s'", query)

        try:
            downloader = YandexMusicDownloader(token=self._settings.yandex_music_token)
            client = downloader._get_client()

            search_results = client.search(text=query)

            results: list[dict[str, Any]] = []

            if search_results and hasattr(search_results, 'tracks'):
                tracks = search_results.tracks
                if tracks and hasattr(tracks, 'results'):
                    for track in tracks.results[:5]:
                        artists = ", ".join(artist.name for artist in track.artists) if track.artists else "Unknown"

                        album_title = None
                        if hasattr(track, 'albums') and track.albums:
                            album = track.albums[0] if isinstance(track.albums, list) else track.albums
                            if hasattr(album, 'title'):
                                album_title = album.title
                        elif hasattr(track, 'album') and track.album:
                            album = track.album
                            if hasattr(album, 'title'):
                                album_title = album.title

                        results.append({
                            "source": "yandex",
                            "track_id": track.id,
                            "title": track.title,
                            "artist": artists,
                            "album": album_title,
                        })

            self._logger.info("Найдено %d треков на Яндекс Музыке", len(results))
            return results[:5]

        except Exception as exc:
            self._logger.error("Error searching Yandex Music: %s", exc)
            return []

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _extract_url(self, text: str) -> str | None:
        """Return the first HTTP(S) URL found in *text*, or None."""
        stripped = text.strip()
        if re.match(r"https?://", stripped, re.IGNORECASE):
            return stripped.replace(" ", "%20")
        match = re.search(r"https?://", stripped, re.IGNORECASE)
        if match:
            return stripped[match.start():].replace(" ", "%20")
        return None

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

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _update_state_mtime(self, state_path: Path) -> None:
        """Update the modification time of state.json to make this track "active"."""
        try:
            if state_path.exists():
                # Set mtime to current time to make this track the "latest"
                os.utime(state_path, None)
                self._logger.info("Updated mtime for state.json: %s", state_path)
        except OSError as exc:
            self._logger.warning("Failed to update mtime for state.json: %s", exc)

    def _ensure_tracks_root(self) -> None:
        self._tracks_root_dir.mkdir(parents=True, exist_ok=True)
