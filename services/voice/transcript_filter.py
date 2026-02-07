"""
Post-ASR transcript filtering for voice pipeline.

Rejects noise-induced hallucinations and meaningless transcriptions
before they reach the LLM. Parakeet TDT, when fed non-speech audio,
commonly produces short filler words, repeated phrases, or stock phrases
like "Thank you." — this module catches those.
"""
import re
import logging

logger = logging.getLogger(__name__)

# Minimum meaningful characters (after stripping punctuation/whitespace)
MIN_MEANINGFUL_CHARS = 3

# Known hallucination patterns from Parakeet TDT on noise/non-speech audio
HALLUCINATION_PATTERNS = [
    re.compile(r'^\.+$'),                                              # Just periods/ellipses
    re.compile(r'^(the|a|an|and|or|but|is|in|it|to|of)\s*[.!?]?$',   # Single common words
               re.IGNORECASE),
    re.compile(r'^(you|yeah|yes|no|okay|ok|bye|hi|hey|hmm|um|uh|oh|ah)\s*[.!?]?$',
               re.IGNORECASE),                                        # Single filler/greeting
    re.compile(r'^thank you\.?\s*$', re.IGNORECASE),                  # Common Parakeet hallucination
    re.compile(r'^thanks\.?\s*$', re.IGNORECASE),
    re.compile(r'^bye[\s.!]*$', re.IGNORECASE),
    re.compile(r'^good(bye)?[\s.!]*$', re.IGNORECASE),
    re.compile(r'(.{2,20}?)\s*\1{2,}', re.IGNORECASE),              # Repeated phrases (3+ times)
]


def is_valid_transcript(text: str) -> tuple[bool, str]:
    """
    Check if ASR transcript is likely real speech vs noise/hallucination.

    Args:
        text: Raw transcription from ASR engine

    Returns:
        Tuple of (is_valid, reason) — reason explains rejection
    """
    stripped = text.strip()

    # Empty
    if not stripped:
        return False, "empty"

    # Strip punctuation for length check
    alphanum_only = re.sub(r'[^a-zA-Z0-9\s]', '', stripped).strip()
    if len(alphanum_only) < MIN_MEANINGFUL_CHARS:
        return False, f"too short ({len(alphanum_only)} alphanumeric chars)"

    # Check hallucination patterns
    for pattern in HALLUCINATION_PATTERNS:
        if pattern.match(stripped):
            return False, f"hallucination pattern: {pattern.pattern}"

    return True, "ok"
