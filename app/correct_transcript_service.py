"""Service for correcting transcription using LLM."""

import json
import logging
import re
from pathlib import Path

from .llm_client import LLMClient

logger = logging.getLogger(__name__)

# Максимальный размер транскрипции (в символах) для одного запроса к LLM
# При превышении — разбиваем на чанки по словам.
# Используется после линеаризации JSON (без indent), поэтому порог выше.
_MAX_TRANSCRIPTION_CHARS = 12000

# System prompt for the LLM
_SYSTEM_PROMPT = """You are a music transcription expert. Your task is to correct transcribed text 
to better match the original lyrics of a song. You must preserve the exact timing information 
from the original transcription - only fix the text content of words and segments.

Important rules:
1. ONLY fix words that are clearly misrecognized
2. Do NOT change the timing (start, end) of any word or segment
3. Do NOT add or remove words - only correct obvious errors
4. Keep the same JSON structure as the input
5. The output must be valid JSON in the exact same format"""

# User prompt template for full transcription
_PROMPT_TEMPLATE = """Here is the original song lyrics:

{lyrics}

Here is the transcription from voice recognition (JSON format):

{transcription}

Your task:
1. Compare the transcription to the original lyrics
2. Fix any misrecognized words while preserving timing
3. Return the corrected transcription in the exact same JSON format
4. Do NOT change any timing information (start, end)
5. Do NOT add or remove words - only correct obvious errors

Return ONLY valid JSON, no additional text:"""

# User prompt template for chunk processing (only words list)
_CHUNK_PROMPT_TEMPLATE = """Here is the original song lyrics (for reference):

{lyrics}

Here is a PART of the transcription words list (JSON array of word objects):

{words_chunk}

Your task:
1. Compare these words to the original lyrics
2. Fix any misrecognized words while preserving timing
3. Return the corrected words array in the exact same JSON format
4. Do NOT change any timing information (start, end)
5. Do NOT add or remove words - only correct obvious errors
6. Return ONLY the JSON array of word objects, no additional text

Return ONLY valid JSON array, no additional text:"""


def _compact_json(data: object) -> str:
    """Serialize data to compact JSON string (no indent, no extra spaces).

    Produces minimal JSON to reduce token usage in LLM prompts.
    Example: {"word": "hello", "start": 1.0, "end": 2.0}

    Args:
        data: Any JSON-serializable object

    Returns:
        Compact JSON string without indentation
    """
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Collapse any remaining multiple whitespace (shouldn't be needed after separators,
    # but kept as a safety measure for edge cases)
    return re.sub(r"[ \t]+", " ", raw).strip()


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
        # Use compact JSON for size estimation (no indent = fewer tokens)
        transcription_str = _compact_json(transcription_data)

        # Read lyrics (collapse multiple blank lines to save tokens)
        lyrics_text = re.sub(r"\n{3,}", "\n\n", lyrics_path.read_text(encoding="utf-8")).strip()

        logger.info(
            "Starting transcription correction: transcription_size=%d (compact), lyrics_length=%d",
            len(transcription_str),
            len(lyrics_text),
        )

        try:
            if len(transcription_str) <= _MAX_TRANSCRIPTION_CHARS:
                # Small transcription — process in one request
                logger.info("Processing transcription in single request")
                corrected = await self._correct_full(
                    transcription_data=transcription_data,
                    transcription_str=transcription_str,
                    lyrics_text=lyrics_text,
                )
            else:
                # Large transcription — process words in chunks
                logger.info(
                    "Transcription too large (%d chars > %d), processing in chunks",
                    len(transcription_str),
                    _MAX_TRANSCRIPTION_CHARS,
                )
                corrected = await self._correct_in_chunks(
                    transcription_data=transcription_data,
                    lyrics_text=lyrics_text,
                )

            logger.info(
                "Transcription correction completed: segments_count=%d, words_count=%d",
                len(corrected.get("segments", [])),
                len(corrected.get("words", [])),
            )

            return corrected

        except Exception as exc:
            logger.error("Transcription correction failed: %s", exc)
            raise RuntimeError(f"Failed to correct transcription: {exc}") from exc

    async def _correct_full(
        self,
        transcription_data: dict,
        transcription_str: str,
        lyrics_text: str,
    ) -> dict:
        """Correct full transcription in a single LLM request.

        Args:
            transcription_data: Parsed transcription data
            transcription_str: Compact JSON string of transcription (pre-computed)
            lyrics_text: Lyrics text (pre-processed)

        Returns:
            Corrected transcription dict
        """
        prompt = _PROMPT_TEMPLATE.format(
            lyrics=lyrics_text,
            transcription=transcription_str,  # already compact
        )

        corrected = await self._llm_client.complete_json(
            prompt=prompt,
            system_prompt=_SYSTEM_PROMPT,
            temperature=0.1,
        )

        if not isinstance(corrected, dict):
            raise RuntimeError(f"LLM returned invalid structure: {type(corrected)}")

        # Ensure segments key exists
        if "segments" not in corrected and "words" not in corrected:
            raise RuntimeError("LLM response missing both 'segments' and 'words' keys")

        return corrected

    async def _correct_in_chunks(
        self,
        transcription_data: dict,
        lyrics_text: str,
    ) -> dict:
        """Correct transcription by processing words in chunks.

        Splits the words list into chunks and corrects each chunk separately,
        then reassembles the full transcription.

        Args:
            transcription_data: Parsed transcription data
            lyrics_text: Lyrics text

        Returns:
            Corrected transcription dict with corrected words and segments
        """
        words = transcription_data.get("words", [])
        segments = transcription_data.get("segments", [])

        if not words:
            logger.warning("No words in transcription, skipping correction")
            return transcription_data

        # Split words into chunks
        chunks = self._split_words_into_chunks(words)
        logger.info("Split %d words into %d chunks", len(words), len(chunks))

        # Correct each chunk
        corrected_words: list[dict] = []
        for i, chunk in enumerate(chunks):
            logger.info("Correcting chunk %d/%d (%d words)", i + 1, len(chunks), len(chunk))
            corrected_chunk = await self._correct_words_chunk(chunk, lyrics_text)
            corrected_words.extend(corrected_chunk)

        # Rebuild segments text from corrected words
        corrected_segments = self._rebuild_segments_from_words(segments, corrected_words)

        # Assemble result
        result = dict(transcription_data)
        result["words"] = corrected_words
        result["segments"] = corrected_segments

        return result

    def _split_words_into_chunks(self, words: list[dict]) -> list[list[dict]]:
        """Split words list into chunks that fit within LLM context.

        Uses compact JSON size for accurate token estimation.

        Args:
            words: List of word objects

        Returns:
            List of word chunks
        """
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        current_size = 0

        for word in words:
            # Use compact JSON for accurate size estimation
            word_str = _compact_json(word)
            word_size = len(word_str)

            if current_size + word_size > _MAX_TRANSCRIPTION_CHARS and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_size = 0

            current_chunk.append(word)
            current_size += word_size

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    async def _correct_words_chunk(
        self,
        words_chunk: list[dict],
        lyrics_text: str,
    ) -> list[dict]:
        """Correct a chunk of words using LLM.

        Args:
            words_chunk: List of word objects to correct
            lyrics_text: Lyrics text for reference (pre-processed)

        Returns:
            Corrected list of word objects
        """
        # Use compact JSON to minimize token usage
        chunk_str = _compact_json(words_chunk)

        prompt = _CHUNK_PROMPT_TEMPLATE.format(
            lyrics=lyrics_text,
            words_chunk=chunk_str,
        )

        try:
            result = await self._llm_client.complete_json(
                prompt=prompt,
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.1,
            )

            # Result should be a list
            if isinstance(result, list):
                # Validate structure: each item should have word, start, end
                if all(isinstance(w, dict) and "word" in w for w in result):
                    return result
                else:
                    logger.warning("LLM returned list with unexpected structure, using original chunk")
                    return words_chunk
            elif isinstance(result, dict):
                # Sometimes LLM wraps array in object
                for key in ("words", "items", "data"):
                    if key in result and isinstance(result[key], list):
                        return result[key]
                logger.warning("LLM returned dict instead of list for words chunk, using original")
                return words_chunk
            else:
                logger.warning("LLM returned unexpected type %s for words chunk, using original", type(result))
                return words_chunk

        except Exception as exc:
            logger.warning(
                "Failed to correct words chunk (%d words), using original: %s",
                len(words_chunk),
                exc,
            )
            return words_chunk

    def _rebuild_segments_from_words(
        self,
        original_segments: list[dict],
        corrected_words: list[dict],
    ) -> list[dict]:
        """Rebuild segments text from corrected words.

        Matches corrected words to segments by timing and rebuilds segment text.

        Args:
            original_segments: Original segments list
            corrected_words: Corrected words list

        Returns:
            Segments with corrected text
        """
        if not original_segments or not corrected_words:
            return original_segments

        corrected_segments = []
        for segment in original_segments:
            seg_start = segment.get("start", 0)
            seg_end = segment.get("end", 0)

            # Find words that belong to this segment by timing
            seg_words = [
                w for w in corrected_words
                if w.get("start", 0) >= seg_start - 0.1 and w.get("end", 0) <= seg_end + 0.1
            ]

            if seg_words:
                # Rebuild segment text from corrected words
                corrected_text = " ".join(w.get("word", "").strip() for w in seg_words)
                new_segment = dict(segment)
                new_segment["text"] = " " + corrected_text
                corrected_segments.append(new_segment)
            else:
                # No words found for this segment, keep original
                corrected_segments.append(segment)

        return corrected_segments
