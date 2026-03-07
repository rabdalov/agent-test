"""ASS subtitle generator for karaoke.

Pipeline step: GENERATE_ASS
Input:  aligned_lyrics_file  (JSON produced by AlignmentService)
Output: .ass subtitle file with karaoke word-highlight effect

The output JSON schema consumed here (from AlignmentService):
{
  "words":    [{"word": "...", "start_time": 1.23, "end_time": 1.78}, ...],
  "segments": [{"text": "full line of text", "start_time": 1.23, "end_time": 3.45}, ...]
}
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ASS time formatter
# ---------------------------------------------------------------------------

def _format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format: H:MM:SS.XX"""
    total_cs = int(round(seconds * 100))  # centiseconds
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean_for_search(text: str) -> str:
    """Lower-case, strip diacritics and punctuation for fuzzy word search."""
    nfd = unicodedata.normalize("NFD", text.lower())
    # Keep letters, digits and spaces; drop punctuation and accent marks
    cleaned = "".join(
        ch for ch in nfd
        if unicodedata.category(ch) not in ("Mn",) and (ch.isalnum() or ch == " ")
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _find_word_in_segment(segment_text: str, word: str, start_idx: int = 0) -> int:
    """Find the character position of *word* inside *segment_text*.

    Comparison is done after punctuation/diacritic stripping so that
    e.g. "world," matches "world". Returns -1 if not found.
    """
    clean_seg = _clean_for_search(segment_text)
    clean_word = _clean_for_search(word)
    if not clean_word:
        return -1

    pos = clean_seg.find(clean_word, start_idx)
    if pos != -1:
        # Map back to position in original (approximate — same character offset)
        return segment_text.lower().find(word.lower().strip(".,!?;:\"'"), start_idx)

    # Partial / prefix fallback
    tokens = clean_seg.split()
    accumulated = 0
    for tok in tokens:
        if tok.startswith(clean_word) or clean_word.startswith(tok):
            return segment_text.lower().find(word.lower().strip(".,!?;:\"'"), start_idx)
        accumulated += len(tok) + 1  # +1 for space

    return -1


# ---------------------------------------------------------------------------
# ASS header template
# ---------------------------------------------------------------------------

_ASS_HEADER_TEMPLATE = """\
[Script Info]
Title: {title}
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,  Arial,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,5,30,30,50,1
Style: Highlight,Arial,{font_size},&H00FF00FF,&H00FF00FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,5,30,30,50,1
Style: TextLine, Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,5,30,30,50,1
Style: Title,    Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,2,8,30,30,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ---------------------------------------------------------------------------
# AssGenerator
# ---------------------------------------------------------------------------

class AssGenerator:
    """Generates an ASS karaoke subtitle file from an aligned lyrics JSON.

    The generated file contains:
    - A title dialogue line shown for the full track duration.
    - Per-segment TextLine entries (whole line, always visible during segment).
    - Per-word Highlight entries that overlay the active word in a different colour.

    Usage::

        generator = AssGenerator(font_size=60)
        generator.generate(aligned_json_path, output_ass_path, track_title="My Song")
    """

    def __init__(self, font_size: int = 60) -> None:
        self.font_size = font_size

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(
        self,
        aligned_json_path: Path,
        output_ass_path: Path,
        track_title: str = "",
    ) -> None:
        """Generate ASS subtitle file.

        Args:
            aligned_json_path: Path to the aligned lyrics JSON produced by
                               AlignmentService (words + segments).
            output_ass_path:   Destination path for the .ass file.
            track_title:       Human-readable title shown in the ASS header and
                               as the first dialogue line.  Defaults to the stem
                               of *aligned_json_path*.

        Raises:
            FileNotFoundError: If *aligned_json_path* does not exist.
            ValueError:        If the JSON contains no segments or words.
        """
        if not aligned_json_path.exists():
            raise FileNotFoundError(
                f"AssGenerator: aligned lyrics file not found: {aligned_json_path}"
            )

        data = json.loads(aligned_json_path.read_text(encoding="utf-8"))

        segments: list[dict] = data.get("segments", [])
        words: list[dict] = data.get("words", [])

        if not segments:
            raise ValueError(
                f"AssGenerator: no segments found in '{aligned_json_path}'"
            )
        if not words:
            raise ValueError(
                f"AssGenerator: no words found in '{aligned_json_path}'"
            )

        title = track_title or aligned_json_path.stem.replace("_", " ")

        # Compute total duration from last word/segment end time
        total_duration = max(
            segments[-1].get("end_time", 0.0) if segments else 0.0,
            words[-1].get("end_time", 0.0) if words else 0.0,
        )

        # Build content
        lines: list[str] = [
            _ASS_HEADER_TEMPLATE.format(title=title, font_size=self.font_size)
        ]

        # Title line covering entire track
        lines.append(
            f"Dialogue: 0,"
            f"{_format_ass_time(0)},"
            f"{_format_ass_time(total_duration)},"
            f"Title,,0,0,0,,{title}\n"
        )

        # Group words into segments
        grouped = self._group_words_into_segments(segments, words)

        # Generate dialogue entries
        for seg_entry in grouped:
            lines.extend(self._build_segment_dialogues(seg_entry))

        ass_content = "".join(lines)
        output_ass_path.parent.mkdir(parents=True, exist_ok=True)
        output_ass_path.write_text(ass_content, encoding="utf-8")

        logger.info(
            "AssGenerator: wrote %d segments, %d total words → '%s'",
            len(grouped),
            len(words),
            output_ass_path,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _group_words_into_segments(
        segments: list[dict],
        words: list[dict],
    ) -> list[dict]:
        """Assign each word to its containing segment.

        A word belongs to segment *i* if ``word.start_time < segment.end_time``.
        This mirrors the logic in ``examples/json_to_seg_word_srt.py``.

        Returns a list of dicts::

            {
                "start": float,
                "end":   float,
                "text":  str,
                "words": [{"word": str, "start": float, "end": float}, ...]
            }
        """
        result: list[dict] = []
        word_idx = 0
        n_words = len(words)

        for seg in segments:
            seg_text: str = seg.get("text", "").strip()
            seg_start: float = seg.get("start_time", 0.0)
            seg_end: float = seg.get("end_time", 0.0)

            seg_words: list[dict] = []
            while word_idx < n_words and words[word_idx].get("start_time", 0.0) < seg_end:
                w = words[word_idx]
                seg_words.append({
                    "word": w.get("word", "").strip(),
                    "start": w.get("start_time", 0.0),
                    "end": w.get("end_time", 0.0),
                })
                word_idx += 1

            if seg_words:
                result.append({
                    "start": seg_start,
                    "end": seg_end,
                    "text": seg_text,
                    "words": seg_words,
                })

        return result

    def _build_segment_dialogues(self, seg: dict) -> list[str]:
        """Build all ASS Dialogue lines for a single segment.

        Returns a list of formatted dialogue strings (each ending with '\\n').
        """
        seg_start: float = seg["start"]
        seg_end: float = seg["end"]
        seg_text: str = seg["text"]
        seg_words: list[dict] = seg["words"]

        lines: list[str] = []

        # Static full-line entry (TextLine style — always visible during segment)
        lines.append(
            f"Dialogue: 0,"
            f"{_format_ass_time(seg_start)},"
            f"{_format_ass_time(seg_end)},"
            f"TextLine,,0,0,0,,{seg_text}\n"
        )

        # Per-word highlight entries
        for i, word_entry in enumerate(seg_words):
            word_start: float = word_entry["start"]
            word_end: float = word_entry["end"]
            word_text: str = word_entry["word"]

            highlighted = self._build_highlighted_text(seg_text, seg_words, i)
            lines.append(
                f"Dialogue: 1,"
                f"{_format_ass_time(word_start)},"
                f"{_format_ass_time(word_end)},"
                f"Default,,0,0,0,,{highlighted}\n"
            )

        return lines

    @staticmethod
    def _build_highlighted_text(
        seg_text: str,
        seg_words: list[dict],
        highlight_idx: int,
    ) -> str:
        """Return *seg_text* with word at *highlight_idx* wrapped in ASS style tags.

        Iterates through all words searching their positions in the segment text
        (left to right) to correctly track the search cursor.  The word at
        *highlight_idx* is wrapped with ``{\\rHighlight}…{\\rDefault}``.
        """
        highlighted_text = seg_text
        search_start = 0

        for j, w_entry in enumerate(seg_words):
            w_text = w_entry["word"]
            pos = _find_word_in_segment(highlighted_text, w_text, search_start)

            if pos == -1:
                continue

            if j == highlight_idx:
                # Length in the *modified* string so far — use actual bare word length
                bare = w_text.strip(".,!?;:\"'")
                before = highlighted_text[:pos]
                after = highlighted_text[pos + len(bare):]
                highlighted_text = f"{before}{{\\rHighlight}}{bare}{{\\rDefault}}{after}"
                # Advance by highlight tags + word length to keep future searches valid
                search_start = pos + len(bare) + len("{\\rHighlight}") + len("{\\rDefault}")
            else:
                search_start = pos + len(w_text.strip(".,!?;:\"'"))

        return highlighted_text
