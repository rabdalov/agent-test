"""
ConfigWatcher — горячая перезагрузка конфигурации из .env без перезапуска бота.

Мониторит файл .env по mtime. При обнаружении изменения перечитывает .env,
пересоздаёт объект Settings и обновляет его во всех компонентах.

Параметры конфигурации, не подлежащие горячей перезагрузке (требуют перезапуска):
  - TELEGRAM_BOT_TOKEN
  - LOG_LEVEL
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable

from .config import Settings, load_settings, _mask_value

_logger = logging.getLogger(__name__)

# Параметры, которые нельзя изменить без перезапуска бота
_RESTART_REQUIRED_FIELDS = frozenset({"telegram_bot_token", "log_level"})


class ConfigWatcher:
    """Следит за изменениями файла .env и перезагружает Settings при необходимости.

    Использование:
        watcher = ConfigWatcher(env_path, initial_settings)
        # Запустить фоновую задачу:
        asyncio.create_task(watcher.watch_loop())
        # Получить актуальные настройки:
        settings = watcher.get_settings()
    """

    def __init__(self, env_path: Path, initial_settings: Settings) -> None:
        self._env_path = env_path
        self._settings = initial_settings
        self._last_mtime: float | None = self._get_mtime()
        self._callbacks: list[Callable[[Settings], None]] = []

    def get_settings(self) -> Settings:
        """Возвращает актуальный объект Settings."""
        return self._settings

    def add_reload_callback(self, callback: Callable[[Settings], None]) -> None:
        """Добавить callback, вызываемый при успешной перезагрузке конфигурации.

        Callback получает новый объект Settings.
        """
        self._callbacks.append(callback)

    def _get_mtime(self) -> float | None:
        """Возвращает mtime файла .env или None, если файл не существует."""
        try:
            return self._env_path.stat().st_mtime
        except OSError:
            return None

    def check_and_reload(self) -> bool:
        """Проверяет mtime .env и при изменении перезагружает конфигурацию.

        Возвращает True, если конфигурация была перезагружена.
        """
        current_mtime = self._get_mtime()
        if current_mtime is None:
            return False
        if self._last_mtime is not None and current_mtime <= self._last_mtime:
            return False

        _logger.info(".env изменён, перезагружаю конфигурацию...")

        # Сбрасываем переменные окружения, загруженные из .env,
        # чтобы load_settings() подхватил новые значения
        self._clear_env_from_dotenv()

        try:
            new_settings = load_settings()
        except Exception as exc:
            _logger.error(
                "Ошибка при перезагрузке конфигурации из .env: %s. "
                "Продолжаю работу с предыдущей конфигурацией.",
                exc,
            )
            # Обновляем mtime, чтобы не пытаться перезагружать снова при той же ошибке
            self._last_mtime = current_mtime
            return False

        self._last_mtime = current_mtime

        # Определяем изменившиеся параметры
        changed_fields: list[str] = []
        restart_required_fields: list[str] = []

        old_settings = self._settings
        for field_name in new_settings.model_fields:
            old_val = getattr(old_settings, field_name, None)
            new_val = getattr(new_settings, field_name, None)
            if old_val != new_val:
                if field_name in _RESTART_REQUIRED_FIELDS:
                    restart_required_fields.append(field_name)
                else:
                    changed_fields.append(field_name)

        # Логируем предупреждения для параметров, требующих перезапуска
        for field_name in restart_required_fields:
            _logger.warning(
                "Параметр %s изменён, но требует перезапуска бота для применения.",
                field_name.upper(),
            )

        # Применяем новые настройки (кроме параметров, требующих перезапуска)
        # Для простоты: применяем весь объект Settings, но логируем предупреждение
        # о параметрах, требующих перезапуска. Сами эти параметры в новом объекте
        # будут иметь новые значения, но компоненты (Bot, logging) уже инициализированы
        # со старыми значениями и не будут переинициализированы.
        self._settings = new_settings

        if changed_fields:
            _logger.info(
                "Конфигурация обновлена. Изменились параметры: %s",
                ", ".join(changed_fields),
            )
        elif not restart_required_fields:
            _logger.info("Конфигурация перезагружена, изменений не обнаружено.")

        # Вызываем callbacks
        for callback in self._callbacks:
            try:
                callback(new_settings)
            except Exception as exc:
                _logger.error("Ошибка в callback перезагрузки конфигурации: %s", exc)

        return True

    def _clear_env_from_dotenv(self) -> None:
        """Сбрасывает переменные окружения, загруженные из .env.

        Это необходимо, чтобы load_settings() перечитал актуальные значения из .env,
        а не использовал закешированные значения из os.environ.
        """
        if not self._env_path.is_file():
            return

        try:
            for line in self._env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _ = stripped.split("=", 1)
                key = key.strip()
                if key and key in os.environ:
                    del os.environ[key]
        except Exception as exc:
            _logger.warning("Не удалось сбросить переменные окружения из .env: %s", exc)

    async def watch_loop(self, interval_sec: int = 30) -> None:
        """Асинхронный цикл мониторинга .env.

        Запускается как фоновая asyncio-задача. Каждые interval_sec секунд
        вызывает check_and_reload().
        """
        _logger.info(
            "ConfigWatcher запущен: мониторинг %s каждые %d сек.",
            self._env_path,
            interval_sec,
        )
        while True:
            await asyncio.sleep(interval_sec)
            try:
                self.check_and_reload()
            except Exception as exc:
                _logger.error("Неожиданная ошибка в ConfigWatcher.watch_loop: %s", exc)
