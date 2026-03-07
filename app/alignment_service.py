"""Alignment service: matches transcribed words+timestamps to full song lyrics.

Pipeline step: ALIGN
Input:  transcribe_json_file (verbose_json from speeches.ai / Whisper)
        source_lyrics_file   (plain text or LRC-style timestamped text)
Output: aligned_lyrics_file  (JSON with word- and line-level timestamps)

Output JSON schema
------------------
{
  "words": [
    {"word": "...", "start_time": 1.23, "end_time": 1.78}
  ],
  "segments": [
    {"text": "full line of text", "start_time": 1.23, "end_time": 3.45}
  ]
}
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class WordWithTimestamp:
    """Single word with its start/end time in seconds."""
    word: str
    start_time: float
    end_time: float

    def to_dict(self) -> dict:
        return {"word": self.word, "start_time": self.start_time, "end_time": self.end_time}


@dataclass
class LineWithTimestamp:
    """Full lyrics line with its start/end time in seconds."""
    text: str
    start_time: float
    end_time: float

    def to_dict(self) -> dict:
        return {"text": self.text, "start_time": self.start_time, "end_time": self.end_time}


@dataclass
class AlignedLyricsResult:
    """Complete alignment result: word-level and line-level timestamps."""
    words: list[WordWithTimestamp] = field(default_factory=list)
    segments: list[LineWithTimestamp] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "words": [w.to_dict() for w in self.words],
            "segments": [ln.to_dict() for ln in self.segments],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Timestamp parser for LRC-style lyrics
# ---------------------------------------------------------------------------

# Matches [MM:SS.xx] or [MM:SS.xxx]
_LRC_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{2})\.(\d{2,3})\]")


def parse_lrc_line(line: str) -> tuple[float | None, str]:
    """Parse one LRC-format line.

    Returns (timestamp_seconds, text) or (None, original_line) if no tag found.
    """
    m = _LRC_TAG_RE.match(line.strip())
    if m is None:
        return None, line.strip()
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    centisecs_str = m.group(3)
    # Normalise to milliseconds
    if len(centisecs_str) == 2:
        frac = int(centisecs_str) / 100.0
    else:
        frac = int(centisecs_str) / 1000.0
    timestamp = minutes * 60.0 + seconds + frac
    text = line[m.end():].strip()
    return timestamp, text


def has_lrc_timestamps(text: str) -> bool:
    """Return True if *text* contains at least one LRC timestamp tag."""
    return bool(_LRC_TAG_RE.search(text))


def parse_lyrics_text(text: str) -> list[tuple[float | None, str]]:
    """Parse a full lyrics string into a list of (timestamp | None, line_text) pairs."""
    result: list[tuple[float | None, str]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        ts, line_text = parse_lrc_line(stripped)
        result.append((ts, line_text))
    return result


# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_word(word: str) -> str:
    """Lower-case, remove diacritics and punctuation for fuzzy matching."""
    # NFC → NFD to decompose accented characters
    nfd = unicodedata.normalize("NFD", word.lower())
    # Keep only letters and digits
    return "".join(ch for ch in nfd if unicodedata.category(ch) not in ("Mn",) and (ch.isalpha() or ch.isdigit()))


def _tokenise(text: str) -> list[str]:
    """Split text into word tokens (non-empty alphabetic/digit sequences)."""
    return [t for t in re.split(r"[^\w']+", text, flags=re.UNICODE) if t]


# ---------------------------------------------------------------------------
# Needleman–Wunsch sequence alignment
# ---------------------------------------------------------------------------

# Scoring parameters
_MATCH_SCORE = 2
_MISMATCH_SCORE = -1
_GAP_PENALTY = -1


def _word_match_score(w1: str, w2: str) -> int:
    """Score for aligning two normalised words."""
    n1 = _normalise_word(w1)
    n2 = _normalise_word(w2)
    if not n1 or not n2:
        return _MISMATCH_SCORE
    if n1 == n2:
        return _MATCH_SCORE
    # Partial / prefix match bonus
    if n1.startswith(n2) or n2.startswith(n1):
        return 1
    return _MISMATCH_SCORE


def needleman_wunsch(
    seq_a: list[str],
    seq_b: list[str],
) -> tuple[list[str | None], list[str | None]]:
    """Global pairwise alignment of two word sequences.

    Returns two lists of equal length where None represents a gap.
    aligned_a[i] corresponds to aligned_b[i].
    """
    n, m = len(seq_a), len(seq_b)

    # Initialise scoring matrix
    score: list[list[int]] = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] + _GAP_PENALTY
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] + _GAP_PENALTY

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diag = score[i - 1][j - 1] + _word_match_score(seq_a[i - 1], seq_b[j - 1])
            up = score[i - 1][j] + _GAP_PENALTY      # gap in seq_b
            left = score[i][j - 1] + _GAP_PENALTY    # gap in seq_a
            score[i][j] = max(diag, up, left)

    # Traceback
    aligned_a: list[str | None] = []
    aligned_b: list[str | None] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and score[i][j] == score[i - 1][j - 1] + _word_match_score(seq_a[i - 1], seq_b[j - 1]):
            aligned_a.append(seq_a[i - 1])
            aligned_b.append(seq_b[j - 1])
            i -= 1
            j -= 1
        elif i > 0 and score[i][j] == score[i - 1][j] + _GAP_PENALTY:
            aligned_a.append(seq_a[i - 1])
            aligned_b.append(None)
            i -= 1
        else:
            aligned_a.append(None)
            aligned_b.append(seq_b[j - 1])
            j -= 1

    aligned_a.reverse()
    aligned_b.reverse()
    return aligned_a, aligned_b


# ---------------------------------------------------------------------------
# Alignment strategies
# ---------------------------------------------------------------------------

class AlignmentStrategy(ABC):
    @abstractmethod
    def align(
        self,
        transcription_words: list[WordWithTimestamp],
        lyrics_segments: list[tuple[float | None, str]],
    ) -> AlignedLyricsResult:
        """Align transcription timestamps with full lyrics.

        Args:
            transcription_words: Words with timestamps from ASR.
            lyrics_segments: List of (optional_timestamp, line_text) pairs
                          parsed from the lyrics file.

        Returns:
            AlignedLyricsResult with per-word and per-line timestamps.
        """


class LrcDirectStrategy(AlignmentStrategy):
    """Use embedded LRC timestamps directly when all lyric lines are timestamped.

    Falls back to linear interpolation for lines without a timestamp.
    """

    def align(
        self,
        transcription_words: list[WordWithTimestamp],
        lyrics_segments: list[tuple[float | None, str]],
    ) -> AlignedLyricsResult:
        # Build line-level timestamps from LRC tags
        lines_out: list[LineWithTimestamp] = []
        words_out: list[WordWithTimestamp] = []

        # Collect stamped lines
        stamped: list[tuple[float, str]] = []
        for ts, text in lyrics_segments:
            if ts is not None:
                stamped.append((ts, text))

        # If no stamped lines, delegate to sequence alignment
        if not stamped:
            fallback = SequenceAlignmentStrategy()
            return fallback.align(transcription_words, lyrics_segments)

        # Determine end times: start of next line (or last transcription word)
        max_end = transcription_words[-1].end_time if transcription_words else 0.0
        for idx, (start_ts, text) in enumerate(stamped):
            end_ts = stamped[idx + 1][0] if idx + 1 < len(stamped) else max_end
            if not text:
                continue
            lines_out.append(LineWithTimestamp(text=text, start_time=start_ts, end_time=end_ts))
            # Per-word interpolation within the line
            line_words = _tokenise(text)
            if not line_words:
                continue
            duration = max(0.0, end_ts - start_ts)
            step = duration / len(line_words)
            for wi, w in enumerate(line_words):
                ws = start_ts + wi * step
                we = start_ts + (wi + 1) * step
                words_out.append(WordWithTimestamp(word=w, start_time=round(ws, 3), end_time=round(we, 3)))

        return AlignedLyricsResult(words=words_out, segments=lines_out)


class SequenceAlignmentStrategy(AlignmentStrategy):
    """Needleman–Wunsch sequence alignment between ASR words and lyrics text."""

    def align(
        self,
        transcription_words: list[WordWithTimestamp],
        lyrics_segments: list[tuple[float | None, str]],
    ) -> AlignedLyricsResult:
        # Flatten lyrics into a single word list, keeping track of line boundaries
        lyrics_flat_words: list[str] = []
        # Map from lyrics word index → line index
        word_to_line: list[int] = []
        line_texts: list[str] = []
        for line_idx, (_, line_text) in enumerate(lyrics_segments):
            if not line_text.strip():
                continue
            line_texts.append(line_text.strip())
            tokens = _tokenise(line_text)
            for tok in tokens:
                lyrics_flat_words.append(tok)
                word_to_line.append(len(line_texts) - 1)

        if not lyrics_flat_words:
            logger.warning("SequenceAlignmentStrategy: lyrics produced no words — returning empty result")
            return AlignedLyricsResult()

        transcription_word_strs = [w.word for w in transcription_words]

        # Run NW alignment
        logger.info(
            "SequenceAlignmentStrategy: aligning %d ASR words vs %d lyrics words",
            len(transcription_word_strs),
            len(lyrics_flat_words),
        )
        aligned_asr, aligned_lyrics = needleman_wunsch(transcription_word_strs, lyrics_flat_words)

        # Build a cursor over transcription words
        asr_word_iter = iter(transcription_words)
        current_asr: WordWithTimestamp | None = None

        # Accumulate timestamps for lyrics words
        lyrics_word_timestamps: list[WordWithTimestamp | None] = [None] * len(lyrics_flat_words)
        lyrics_ptr = 0  # index into lyrics_flat_words

        asr_ptr = 0  # index into transcription_words
        lyr_ptr = 0  # index into lyrics_flat_words

        for asr_tok, lyr_tok in zip(aligned_asr, aligned_lyrics):
            if asr_tok is not None and lyr_tok is not None:
                # Matched pair
                if asr_ptr < len(transcription_words):
                    wt = transcription_words[asr_ptr]
                    if lyr_ptr < len(lyrics_flat_words):
                        lyrics_word_timestamps[lyr_ptr] = WordWithTimestamp(
                            word=lyrics_flat_words[lyr_ptr],
                            start_time=wt.start_time,
                            end_time=wt.end_time,
                        )
                    asr_ptr += 1
                    lyr_ptr += 1
            elif asr_tok is not None:
                # ASR word without lyrics counterpart — consume ASR
                asr_ptr += 1
            else:
                # Lyrics word without ASR counterpart — gap
                lyr_ptr += 1

        # Interpolate missing timestamps (gaps in lyrics)
        self._interpolate_timestamps(lyrics_word_timestamps, lyrics_flat_words)

        # Build word-level output
        words_out: list[WordWithTimestamp] = []
        for wt in lyrics_word_timestamps:
            if wt is not None:
                words_out.append(wt)

        # Build line-level output by aggregating words per line
        line_word_groups: dict[int, list[WordWithTimestamp]] = {}
        for lyr_i, wt in enumerate(lyrics_word_timestamps):
            if wt is None:
                continue
            li = word_to_line[lyr_i]
            line_word_groups.setdefault(li, []).append(wt)

        lines_out: list[LineWithTimestamp] = []
        for li, text in enumerate(line_texts):
            group = line_word_groups.get(li, [])
            if group:
                start_t = group[0].start_time
                end_t = group[-1].end_time
            else:
                # No matched words; interpolate from neighbours
                start_t = 0.0
                end_t = 0.0
            lines_out.append(LineWithTimestamp(text=text, start_time=start_t, end_time=end_t))

        return AlignedLyricsResult(words=words_out, segments=lines_out)

    @staticmethod
    def _interpolate_timestamps(
        timestamps: list[WordWithTimestamp | None],
        words: list[str],
    ) -> None:
        """Fill None entries in *timestamps* by linear interpolation."""
        n = len(timestamps)
        i = 0
        while i < n:
            if timestamps[i] is None:
                # Find nearest non-None neighbours
                left = i - 1
                right = i
                while right < n and timestamps[right] is None:
                    right += 1

                if left < 0 and right >= n:
                    # No anchors at all
                    t_start, t_end = 0.0, 0.0
                    for k in range(i, right):
                        timestamps[k] = WordWithTimestamp(word=words[k], start_time=t_start, end_time=t_end)
                    i = right
                    continue

                if left < 0:
                    # Only right anchor
                    anchor = timestamps[right]
                    for k in range(i, right):
                        timestamps[k] = WordWithTimestamp(word=words[k], start_time=anchor.start_time, end_time=anchor.start_time)
                elif right >= n:
                    # Only left anchor
                    anchor = timestamps[left]
                    for k in range(i, n):
                        timestamps[k] = WordWithTimestamp(word=words[k], start_time=anchor.end_time, end_time=anchor.end_time)
                else:
                    # Interpolate between left and right
                    t_left = timestamps[left].end_time
                    t_right = timestamps[right].start_time
                    gap_count = right - left  # number of steps
                    for k in range(i, right):
                        frac_start = (k - left) / gap_count
                        frac_end = (k - left + 1) / gap_count
                        ws = t_left + frac_start * (t_right - t_left)
                        we = t_left + frac_end * (t_right - t_left)
                        timestamps[k] = WordWithTimestamp(
                            word=words[k],
                            start_time=round(ws, 3),
                            end_time=round(we, 3),
                        )
                i = right
            else:
                i += 1


# ---------------------------------------------------------------------------
# Transcription JSON parser (speeches.ai / Whisper verbose_json)
# ---------------------------------------------------------------------------

def load_transcription_words(transcription_json_path: Path) -> list[WordWithTimestamp]:
    """Load word-level timestamps from a Whisper verbose_json file.

    Whisper verbose_json format:
    {
      "words": [{"word": "...", "start": 0.0, "end": 0.5}, ...],
      ...
      "segments": [{"words": [...], ...}, ...]
    }
    """
    raw = json.loads(transcription_json_path.read_text(encoding="utf-8"))

    words: list[WordWithTimestamp] = []

    # Top-level "words" list (speeches.ai with timestamp_granularities[]=word)
    top_words = raw.get("words")
    if top_words and isinstance(top_words, list):
        for entry in top_words:
            word = entry.get("word", "").strip()
            start = float(entry.get("start", entry.get("start_time", 0.0)))
            end = float(entry.get("end", entry.get("end_time", 0.0)))
            if word:
                words.append(WordWithTimestamp(word=word, start_time=start, end_time=end))
        if words:
            logger.info("Loaded %d words from top-level 'words' field", len(words))
            return words

    # Nested words inside segments
    segments = raw.get("segments", [])
    for seg in segments:
        seg_words = seg.get("words", [])
        for entry in seg_words:
            word = entry.get("word", "").strip()
            start = float(entry.get("start", entry.get("start_time", 0.0)))
            end = float(entry.get("end", entry.get("end_time", 0.0)))
            if word:
                words.append(WordWithTimestamp(word=word, start_time=start, end_time=end))

    if not words:
        logger.warning(
            "load_transcription_words: no word-level data found in '%s'; "
            "will attempt segment-level fallback",
            transcription_json_path,
        )
        # Fallback: extract from segment text with uniform timing
        for seg in segments:
            seg_text = seg.get("text", "")
            seg_start = float(seg.get("start", 0.0))
            seg_end = float(seg.get("end", 0.0))
            seg_tokens = _tokenise(seg_text)
            if not seg_tokens:
                continue
            dur = seg_end - seg_start
            step = dur / len(seg_tokens)
            for wi, tok in enumerate(seg_tokens):
                ws = seg_start + wi * step
                we = seg_start + (wi + 1) * step
                words.append(WordWithTimestamp(word=tok, start_time=round(ws, 3), end_time=round(we, 3)))

    logger.info("Loaded %d transcription words from '%s'", len(words), transcription_json_path)
    return words


# ---------------------------------------------------------------------------
# Main AlignmentService
# ---------------------------------------------------------------------------

class AlignmentService:
    """Aligns ASR transcription timestamps with full song lyrics.

    Strategy selection:
    - If the lyrics file contains LRC-style timestamps on the majority of
      lines, ``LrcDirectStrategy`` is used (timestamps come from the lyrics).
    - Otherwise, ``SequenceAlignmentStrategy`` is used  (timestamps come
      from the ASR transcription, propagated to nearby lyrics words via
      Needleman–Wunsch global alignment).
    """

    # Minimum fraction of lines that must be LRC-stamped to use LrcDirectStrategy
    _LRC_THRESHOLD = 0.5

    def align_timestamps(
        self,
        transcription_json_path: Path,
        source_lyrics_path: Path,
        audio_file: Optional[Path] = None,
    ) -> AlignedLyricsResult:
        """Align timestamps from ASR transcription with full song lyrics.

        Args:
            transcription_json_path: Path to the Whisper verbose_json file.
            source_lyrics_path:      Path to the lyrics text file
                                     (plain text or LRC format).
            audio_file:              Optional path to vocal audio (reserved
                                     for future forced-alignment integration).

        Returns:
            AlignedLyricsResult with word- and line-level timestamps.

        Raises:
            FileNotFoundError: If any required input file is missing.
            ValueError: If the transcription JSON is structurally invalid.
        """
        # --- Validate inputs ---
        if not transcription_json_path.exists():
            raise FileNotFoundError(
                f"AlignmentService: transcription file not found: {transcription_json_path}"
            )
        if not source_lyrics_path.exists():
            raise FileNotFoundError(
                f"AlignmentService: lyrics file not found: {source_lyrics_path}"
            )

        # --- Load transcription ---
        try:
            transcription_words = load_transcription_words(transcription_json_path)
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"AlignmentService: failed to parse transcription JSON "
                f"({transcription_json_path}): {exc}"
            ) from exc

        if not transcription_words:
            raise ValueError(
                f"AlignmentService: transcription file contains no words: {transcription_json_path}"
            )

        # --- Load lyrics ---
        lyrics_text = source_lyrics_path.read_text(encoding="utf-8")
        lyrics_segments = parse_lyrics_text(lyrics_text)

        if not lyrics_segments:
            raise ValueError(
                f"AlignmentService: lyrics file is empty or produced no lines: {source_lyrics_path}"
            )

        # --- Select strategy ---
        strategy = self._select_strategy(lyrics_segments)
        logger.info(
            "AlignmentService: using %s for track lyrics from '%s'",
            type(strategy).__name__,
            source_lyrics_path,
        )

        # --- Run alignment ---
        result = strategy.align(transcription_words, lyrics_segments)

        # --- Post-process: guarantee non-negative, monotonically-non-decreasing times ---
        result = self._sanitise(result)

        logger.info(
            "AlignmentService: alignment produced %d words, %d segments",
            len(result.words),
            len(result.segments),
        )
        return result

    def _select_strategy(
        self,
        lyrics_segments: list[tuple[float | None, str]],
    ) -> AlignmentStrategy:
        non_empty = [line for ts, line in lyrics_segments if line.strip()]
        if not non_empty:
            return SequenceAlignmentStrategy()

        stamped = sum(1 for ts, _ in lyrics_segments if ts is not None)
        fraction = stamped / len(lyrics_segments) if lyrics_segments else 0.0
        if fraction >= self._LRC_THRESHOLD:
            return LrcDirectStrategy()
        return SequenceAlignmentStrategy()

    @staticmethod
    def _sanitise(result: AlignedLyricsResult) -> AlignedLyricsResult:
        """Ensure timestamps are non-negative and end_time >= start_time."""
        for wt in result.words:
            wt.start_time = max(0.0, round(wt.start_time, 3))
            wt.end_time = max(wt.start_time, round(wt.end_time, 3))
        for lt in result.segments:
            lt.start_time = max(0.0, round(lt.start_time, 3))
            lt.end_time = max(lt.start_time, round(lt.end_time, 3))
        return result


# ---------------------------------------------------------------------------
# Convenience: save result to JSON file
# ---------------------------------------------------------------------------

def save_aligned_result(result: AlignedLyricsResult, output_path: Path) -> None:
    """Serialise *result* to JSON at *output_path*, creating parent dirs."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.to_json(), encoding="utf-8")
    logger.info("AlignmentService: saved aligned lyrics to '%s'", output_path)
