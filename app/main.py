import asyncio
import logging

from .bot_app import BotApp
from .config import load_settings, settings_for_logging, setup_logging
from .handlers_karaoke import KaraokeHandlers


async def _run() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Loaded settings: %s", settings_for_logging(settings))
    logger.info("Starting bot")

    bot_app = BotApp(settings)
    handlers = KaraokeHandlers(settings)
    bot_app.register_handlers(handlers)

    await bot_app.run_polling()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

