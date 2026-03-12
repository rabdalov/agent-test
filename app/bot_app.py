import logging
from typing import Any

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import TelegramMethod
from aiogram.types import Update

from .config import Settings
from .handlers_karaoke import KaraokeHandlers


class UpdateLoggingMiddleware(BaseMiddleware):
    """Middleware для логирования входящих обновлений"""

    def __init__(self) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)

    async def __call__(
        self,
        handler: Any,
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        log_msg = self._build_log_message(event)
        self._logger.debug(log_msg)
        return await handler(event, data)

    def _build_log_message(self, event: Update) -> str:
        """Формирует строку лога для входящего обновления"""
        if event.message:
            msg = event.message
            user_id = msg.from_user.id if msg.from_user else "?"
            username = msg.from_user.username or "" if msg.from_user else ""
            chat_id = msg.chat.id if msg.chat else "?"
            message_id = msg.message_id

            content_type, text_preview = self._get_message_content(msg)

            log_parts = [
                f"[IN] user_id={user_id}",
                f"username={username}",
                f"chat_id={chat_id}",
                f"message_id={message_id}",
                f"type={content_type}",
            ]
            if text_preview:
                log_parts.append(f'text="{text_preview}"')
            return " ".join(log_parts)

        if event.callback_query:
            cq = event.callback_query
            user_id = cq.from_user.id if cq.from_user else "?"
            username = cq.from_user.username or "" if cq.from_user else ""
            chat_id = cq.message.chat.id if cq.message and cq.message.chat else "?"
            message_id = cq.message.message_id if cq.message else "?"
            data_preview = (cq.data or "")[:200]

            log_parts = [
                f"[IN] user_id={user_id}",
                f"username={username}",
                f"chat_id={chat_id}",
                f"message_id={message_id}",
                "type=callback_query",
            ]
            if data_preview:
                log_parts.append(f'data="{data_preview}"')
            return " ".join(log_parts)

        if event.edited_message:
            msg = event.edited_message
            user_id = msg.from_user.id if msg.from_user else "?"
            username = msg.from_user.username or "" if msg.from_user else ""
            chat_id = msg.chat.id if msg.chat else "?"
            message_id = msg.message_id
            content_type, text_preview = self._get_message_content(msg)

            log_parts = [
                f"[IN] user_id={user_id}",
                f"username={username}",
                f"chat_id={chat_id}",
                f"message_id={message_id}",
                f"type=edited_{content_type}",
            ]
            if text_preview:
                log_parts.append(f'text="{text_preview}"')
            return " ".join(log_parts)

        # Прочие типы обновлений
        event_type = self._get_event_type(event)
        return f"[IN] type={event_type}"

    def _get_message_content(self, msg: Any) -> tuple[str, str | None]:
        """Определяет тип контента и возвращает (content_type, text_preview)"""
        if msg.audio:
            file_name = msg.audio.file_name or ""
            return "audio", f'file_name="{file_name}"' if file_name else None
        if msg.voice:
            return "voice", None
        if msg.video:
            file_name = msg.video.file_name or ""
            return "video", f'file_name="{file_name}"' if file_name else None
        if msg.video_note:
            return "video_note", None
        if msg.document:
            file_name = msg.document.file_name or ""
            return "document", f'file_name="{file_name}"' if file_name else None
        if msg.photo:
            return "photo", None
        if msg.sticker:
            return "sticker", None
        if msg.animation:
            return "animation", None
        if msg.text:
            text = msg.text
            # Определяем, является ли это командой
            if text.startswith("/"):
                content_type = "command"
            else:
                content_type = "text"
            return content_type, text[:200]
        if msg.caption:
            return "caption", msg.caption[:200]
        return "unknown", None

    def _get_event_type(self, event: Update) -> str:
        """Определяет тип события для прочих обновлений"""
        if event.inline_query:
            return "InlineQuery"
        if event.chosen_inline_result:
            return "ChosenInlineResult"
        if event.channel_post:
            return "ChannelPost"
        if event.edited_channel_post:
            return "EditedChannelPost"
        if event.shipping_query:
            return "ShippingQuery"
        if event.pre_checkout_query:
            return "PreCheckoutQuery"
        if event.poll:
            return "Poll"
        if event.poll_answer:
            return "PollAnswer"
        if event.my_chat_member:
            return "MyChatMember"
        if event.chat_member:
            return "ChatMember"
        if event.chat_join_request:
            return "ChatJoinRequest"
        return "Unknown"


class LoggingSession(AiohttpSession):
    """Сессия с логированием исходящих запросов к Telegram API"""

    def __init__(self) -> None:
        super().__init__()
        self._logger = logging.getLogger(__name__)

    async def make_request(
        self,
        bot: Any,
        method: TelegramMethod,
        timeout: Any = None,
    ) -> Any:
        method_name = type(method).__name__
        log_parts = [f"[OUT] method={method_name}"]

        # Извлекаем chat_id из параметров метода
        chat_id = getattr(method, "chat_id", None)
        if chat_id is not None:
            log_parts.append(f"chat_id={chat_id}")

        # Извлекаем текст из параметров метода (первые 200 символов)
        text = getattr(method, "text", None)
        if text:
            text_preview = str(text)[:200]
            log_parts.append(f'text="{text_preview}"')

        # Для sendVideo/sendDocument — логируем без текста
        caption = getattr(method, "caption", None)
        if caption and not text:
            caption_preview = str(caption)[:200]
            log_parts.append(f'caption="{caption_preview}"')

        self._logger.debug(" ".join(log_parts))

        return await super().make_request(bot, method, timeout)


class BotApp:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=LoggingSession(),
        )
        self._dispatcher = Dispatcher(storage=MemoryStorage())
        # Регистрируем middleware для логирования входящих обновлений
        self._dispatcher.update.middleware(UpdateLoggingMiddleware())

    def register_handlers(self, handlers: KaraokeHandlers) -> None:
        self._dispatcher.include_router(handlers.router)

    async def run_polling(self) -> None:
        await self._dispatcher.start_polling(self._bot)
