from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Settings
from .handlers_karaoke import KaraokeHandlers


class BotApp:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._dispatcher = Dispatcher(storage=MemoryStorage())

    def register_handlers(self, handlers: KaraokeHandlers) -> None:
        self._dispatcher.include_router(handlers.router)

    async def run_polling(self) -> None:
        await self._dispatcher.start_polling(self._bot)


