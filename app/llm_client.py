"""LLM client for interacting with OpenRouter API."""

import logging
import socket
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _log_network_diagnostics() -> None:
    """Log network diagnostics for debugging connection issues."""
    try:
        hostname = "api.openrouter.ai"
        logger.debug("Network diagnostics: resolving %s", hostname)
        ip_address = socket.gethostbyname(hostname)
        logger.debug("Network diagnostics: %s resolved to %s", hostname, ip_address)
    except socket.gaierror as e:
        logger.error("Network diagnostics: DNS resolution failed: %s", e)
    except Exception as e:
        logger.warning("Network diagnostics failed: %s", e)


class LLMClient:
    """Client for interacting with LLM via OpenRouter."""

    def __init__(
        self,
        api_key: str | None,
        model: str = "qwen/qwen3-next-80b-a3b-instruct:free",
        api_url: str = "https://api.openrouter.ai/v1",
        timeout: int = 120,
    ) -> None:
        """Initialize LLM client.

        Args:
            api_key: OpenRouter API key
            model: Model identifier (default: qwen/qwen3-next-80b-a3b-instruct:free)
            api_url: OpenRouter API base URL
            timeout: Request timeout in seconds
        """
        self._api_key = api_key
        self._model = model
        self._api_url = api_url
        self._timeout = timeout

        if not api_key:
            raise ValueError("OpenRouter API key is required")

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_url,
            timeout=timeout,
        )
        logger.info(
            "LLMClient initialized with model=%s, api_url=%s, timeout=%d",
            model,
            api_url,
            timeout,
        )
        # Log initial network diagnostics
        _log_network_diagnostics()

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """Send a completion request to the LLM.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0.0 - 2.0)
            max_tokens: Maximum tokens in response

        Returns:
            LLM response text

        Raises:
            RuntimeError: If the API request fails
        """
        messages: list[dict[str, Any]] = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append({"role": "user", "content": prompt})

        try:
            logger.debug(
                "Sending LLM request: model=%s, prompt_length=%d",
                self._model,
                len(prompt),
            )

            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            # Detailed response logging for debugging
            logger.debug(
                "LLM response: id=%s, model=%s, choices_count=%d, usage_prompt=%d, usage_completion=%d",
                response.id,
                response.model,
                len(response.choices),
                response.usage.prompt_tokens if response.usage else 0,
                response.usage.completion_tokens if response.usage else 0,
            )

            # Log each choice for debugging
            for i, choice in enumerate(response.choices):
                logger.debug(
                    "LLM choice[%d]: role=%s, content=%s, finish_reason=%s",
                    i,
                    choice.message.role,
                    repr(choice.message.content)[:200] if choice.message.content else "None",
                    choice.finish_reason,
                )

            content = response.choices[0].message.content
            if content is None:
                # Log full response for debugging empty content
                logger.error(
                    "LLM returned empty content: response_id=%s, model=%s, finish_reason=%s, choices=%s",
                    response.id,
                    response.model,
                    response.choices[0].finish_reason,
                    [({"role": c.message.role, "content": repr(c.message.content)}) for c in response.choices],
                )
                raise RuntimeError("LLM returned empty response")

            logger.debug(
                "LLM response received: content_length=%d",
                len(content),
            )

            return content

        except Exception as exc:
            # Log detailed error information for debugging
            logger.error(
                "LLM request failed: type=%s, message=%s, api_url=%s, model=%s",
                type(exc).__name__,
                str(exc),
                self._api_url,
                self._model,
            )
            # Log network diagnostics
            _log_network_diagnostics()
            raise RuntimeError(f"LLM request failed: {exc}") from exc

    async def complete_json(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """Send a completion request and parse JSON response.

        Args:
            prompt: User prompt (should request JSON output)
            system_prompt: Optional system prompt
            temperature: Sampling temperature

        Returns:
            Parsed JSON response

        Raises:
            RuntimeError: If the API request fails or response is not valid JSON
        """
        # Add JSON formatting instruction to prompt
        json_prompt = f"{prompt}\n\nRespond only with valid JSON, no additional text."

        response_text = await self.complete(
            prompt=json_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )

        # Try to parse JSON from response
        import json

        try:
            # Try direct parse first
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from potential markdown code blocks
            import re

            json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response_text)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass

            # Try to find JSON object without code blocks
            json_match = re.search(r"\{[\s\S]*\}", response_text)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    pass

            raise RuntimeError(f"Failed to parse JSON from LLM response: {response_text[:200]}")

    async def close(self) -> None:
        """Close the client connection."""
        await self._client.close()
