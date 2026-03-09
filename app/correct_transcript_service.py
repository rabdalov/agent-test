"""Service for correcting transcription using LLM."""

import json
import logging
from pathlib import Path

from .llm_client import LLMClient

logger = logging.getLogger(__name__)

# System prompt for the LLM
_SYSTEM_PROMPT = """You are a music transcription expert. Your task is to correct transcribed text 
to better match the original lyrics of a song. You must preserve the exact timing information 
from the original transcription - only fix the text content of words and segments.

Important rules:
1. ONLY fix words that are clearly misrecognized
2. Do NOT change the timing (start_time, end_time) of any word or segment
3. Do NOT add or remove words - only correct obvious errors
4. Keep the same JSON structure as the input
5. The output must be valid JSON in the exact same format"""

# User prompt template
_PROMPT_TEMPLATE = """Here is the original song lyrics:

{lyrics}

Here is the transcription from voice recognition (JSON format):

{transcription}

Your task:
1. Compare the transcription to the original lyrics
2. Fix any misrecognized words while preserving timing
3. Return the corrected transcription in the exact same JSON format
4. Do NOT change any timing information (start_time, end_time)
5. Do NOT add or remove words - only correct obvious errors

Return ONLY valid JSON, no additional text:"""


class CorrectTranscriptService:
    """Service for correcting transcription using LLM."""

    def __init__(self, llm_client: LLMClient) -> None:
        """Initialize the service.

        Args:
            llm_client: LLM client for making API requests
        """
        self._llm_client = llm_client
        logger.info("CorrectTranscriptService initialized")

    async def correct_transcript(
        self,
        transcription_json_path: Path,
        lyrics_path: Path,
    ) -> dict:
        """Correct transcription using LLM.

        Args:
            transcription_json_path: Path to the transcription JSON file
            lyrics_path: Path to the source lyrics file

        Returns:
            Corrected transcription as a dictionary (same format as input)

        Raises:
            RuntimeError: If correction fails
        """
        # Read transcription JSON
        transcription_data = json.loads(
            transcription_json_path.read_text(encoding="utf-8")
        )
        transcription_str = json.dumps(transcription_data, indent=2, ensure_ascii=False)

        # Read lyrics
        lyrics_text = lyrics_path.read_text(encoding="utf-8")

        logger.info(
            "Starting transcription correction: transcription_size=%d, lyrics_length=%d",
            len(transcription_str),
            len(lyrics_text),
        )

        # Build prompt
        prompt = _PROMPT_TEMPLATE.format(
            lyrics=lyrics_text,
            transcription=transcription_str,
        )

        try:
            # Send to LLM
            corrected_json_str = await self._llm_client.complete_json(
                prompt=prompt,
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.3,
            )

            # Validate the response has the expected structure
            if not isinstance(corrected_json_str, dict):
                raise RuntimeError(f"LLM returned invalid structure: {type(corrected_json_str)}")

            # Validate that we have segments
            if "segments" not in corrected_json_str:
                corrected_json_str = {"segments": corrected_json_str}

            logger.info(
                "Transcription correction completed: segments_count=%d",
                len(corrected_json_str.get("segments", [])),
            )

            return corrected_json_str

        except Exception as exc:
            logger.error("Transcription correction failed: %s", exc)
            raise RuntimeError(f"Failed to correct transcription: {exc}") from exc
