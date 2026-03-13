import asyncio
import logging
from pathlib import Path

from .bot_app import BotApp
from .config import load_settings, settings_for_logging, setup_logging, _BASE_DIR
from .config_watcher import ConfigWatcher
from .handlers_karaoke import KaraokeHandlers


async def _run() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Loaded settings: %s", settings_for_logging(settings))
    logger.info("Starting bot")

    # Создаём ConfigWatcher для горячей перезагрузки .env
    env_path = _BASE_DIR / ".env"
    config_watcher = ConfigWatcher(env_path=env_path, initial_settings=settings)

    bot_app = BotApp(settings, config_watcher=config_watcher)
    handlers = KaraokeHandlers(settings, config_watcher=config_watcher)
    bot_app.register_handlers(handlers)

    await bot_app.run_polling()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
