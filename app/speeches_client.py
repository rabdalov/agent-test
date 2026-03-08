import json
import logging
from pathlib import Path

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class SpeechesClient:
    """HTTP-клиент для транскрибации аудио через speeches.ai API."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.speeches_base_url.rstrip("/")
        self._model = settings.transcription_model_id
        self._language = settings.lang_default
        self._prompt = settings.prompt_speeches
        self._timeout = settings.speeches_timeout

    async def transcribe(self, vocal_file: Path, output_json: Path, language: str | None = None) -> Path:
        """Транскрибирует аудиофайл vocal_file через speeches.ai API.

        Отправляет POST-запрос с multipart/form-data, сохраняет ответ
        (verbose_json) в файл output_json.

        :param vocal_file: Путь к входному аудиофайлу (вокальная дорожка)
        :param output_json: Путь к выходному JSON-файлу с результатом транскрипции
        :param language: Язык аудио ('ru', 'en' и т.д.); если None — используется lang_default из настроек
        :returns: Путь к output_json
        :raises RuntimeError: При HTTP-ошибке или недоступности сервиса
        """
        url = f"{self._base_url}/v1/audio/transcriptions"
        effective_language = language if language is not None else self._language

        logger.info(
            "SpeechesClient: starting transcription for '%s' via %s (language=%s)",
            vocal_file,
            url,
            effective_language,
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            with vocal_file.open("rb") as audio_fp:
                files = {"file": (vocal_file.name, audio_fp, "audio/mpeg")}
                data = {
                    "model": self._model,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                    "stream": "false",
                    "language": effective_language,
                    "temperature": "0.0",
                    "prompt": self._prompt,
                }
                headers = {"Authorization": "Bearer dummy"}

                try:
                    response = await client.post(url, headers=headers, data=data, files=files)
                except httpx.RequestError as exc:
                    raise RuntimeError(
                        f"SpeechesClient: не удалось подключиться к speeches.ai ({url}): {exc}"
                    ) from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"SpeechesClient: speeches.ai вернул HTTP {response.status_code}: {response.text}"
            )

        result = response.json()
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(
            "SpeechesClient: transcription saved to '%s'",
            output_json,
        )

        return output_json
