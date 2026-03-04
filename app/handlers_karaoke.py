from aiogram import Router, types
from aiogram.filters import CommandStart

from .config import Settings


class KaraokeHandlers:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.router = Router()
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def handle_start(message: types.Message) -> None:  # type: ignore[unused-ignore]
            await message.answer(
                "Привет! Я бот для подготовки караоке-видео.\n"
                "Сейчас я умею отвечать на /start и повторять текстовые сообщения."
            )

        @self.router.message()
        async def handle_echo(message: types.Message) -> None:  # type: ignore[unused-ignore]
            if not message.text:
                return

            await message.answer(message.text)


