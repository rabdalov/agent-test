"""Test script for CorrectTranscriptService using example data.

Usage:
    uv run python scripts/test_correct_transcript.py

Requires:
    - OPENROUTER_API_KEY in environment or .env file
    - data_exapmles/А-Студио - Каменный город_lyrics.txt
    - data_exapmles/А-Студио - Каменный город_transcription.json
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Try manual .env loading
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the test."""
    from app.llm_client import LLMClient
    from app.correct_transcript_service import CorrectTranscriptService

    # Paths to test data
    data_dir = Path(__file__).parent.parent / "data_exapmles"
    lyrics_path = data_dir / "А-Студио - Каменный город_lyrics.txt"
    transcription_path = data_dir / "А-Студио - Каменный город_transcription.json"

    # Validate files exist
    if not lyrics_path.exists():
        logger.error("Lyrics file not found: %s", lyrics_path)
        sys.exit(1)
    if not transcription_path.exists():
        logger.error("Transcription file not found: %s", transcription_path)
        sys.exit(1)

    logger.info("Lyrics file: %s (%d bytes)", lyrics_path, lyrics_path.stat().st_size)
    logger.info("Transcription file: %s (%d bytes)", transcription_path, transcription_path.stat().st_size)

    # Get API key
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.error("OPENROUTER_API_KEY not set in environment")
        sys.exit(1)

    # Get model and API URL
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    api_url = os.getenv("OPENROUTER_API", "https://api.openrouter.ai/v1")

    logger.info("Using model: %s, api_url: %s", model, api_url)

    # Show transcription size info
    transcription_data = json.loads(transcription_path.read_text(encoding="utf-8"))
    transcription_str = json.dumps(transcription_data, indent=2, ensure_ascii=False)
    lyrics_text = lyrics_path.read_text(encoding="utf-8")

    logger.info(
        "Input sizes: transcription=%d chars, lyrics=%d chars",
        len(transcription_str),
        len(lyrics_text),
    )
    logger.info(
        "Transcription has %d segments, %d words",
        len(transcription_data.get("segments", [])),
        len(transcription_data.get("words", [])),
    )

    # Initialize services
    llm_client = LLMClient(
        api_key=api_key,
        model=model,
        api_url=api_url,
        timeout=120,
    )
    service = CorrectTranscriptService(llm_client=llm_client)

    try:
        logger.info("Starting correction...")
        result = await service.correct_transcript(
            transcription_json_path=transcription_path,
            lyrics_path=lyrics_path,
        )

        logger.info("Correction successful!")
        logger.info(
            "Result: %d segments, %d words",
            len(result.get("segments", [])),
            len(result.get("words", [])),
        )

        # Save result
        output_path = data_dir / "А-Студио - Каменный город_corrected.json"
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Result saved to: %s", output_path)

        # Show diff summary: compare original vs corrected words
        original_words = [w.get("word", "").strip() for w in transcription_data.get("words", [])]
        corrected_words = [w.get("word", "").strip() for w in result.get("words", [])]

        changes = 0
        for i, (orig, corr) in enumerate(zip(original_words, corrected_words)):
            if orig != corr:
                changes += 1
                logger.info("Word[%d] changed: '%s' -> '%s'", i, orig, corr)

        logger.info("Total word changes: %d / %d", changes, len(original_words))

    except Exception as exc:
        logger.error("Correction failed: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        await llm_client.close()


if __name__ == "__main__":
    asyncio.run(main())
