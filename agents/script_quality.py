# agents/script_quality.py — Post-generation script quality filters
#
# Applied AFTER AI generation, BEFORE TTS.
# No imports from script_agent (prevents circular imports).
#
# Public API:
#   remove_filler_phrases(text)               -> str
#   deduplicate_paragraphs(text, threshold)   -> str
#   detect_quality_issues(text)               -> dict
#   post_translation_cleanup_arabic(ar_text)  -> str
#   filter_contaminated_facts(facts, topic, series) -> list[str]
#   apply_all_quality_filters(text)           -> str

import re
from typing import List

# ── Banned filler phrases ─────────────────────────────────────────────────────
# These are generic suspense templates that add no narrative value.
# They're matched case-insensitively at sentence start OR anywhere in sentence.
FILLER_PHRASES: List[str] = [
    # Generic revelation starters
    "the reality was darker than anyone knew",
    "the truth was far more disturbing",
    "the truth was far more sinister",
    "the reality was far darker",
    "what would later emerge changed everything",
    "what nobody expected was",
    "what came next shocked everyone",
    "what happened next shocked everyone",
    "little did anyone know",
    "little did they know",
    "in a shocking twist",
    "in a stunning twist",
    "nobody could have predicted",
    "no one could have predicted",
    # Generic opener starters
    "in the world of crime",
    "throughout history",
    "this is a story about",
    "this is the story of",
    "it all started when",
    "it all began when",
    "behind the scenes",
    # Generic closing starters
    "so that is what happened",
    "that is the story of",
    "and so the story ends",
    # Generic intensifiers
    "years later the truth finally emerged",
    "years later the full story",
    "the world would never be the same",
    "changed the world forever",
    # Show-specific filler
    "introduced millions of people to this incredible true story",
    "but the real events were even more extraordinary",
    "the real story is even more fascinating",
]

# Short (< 15 words) standalone sentences that are pure filler
_FILLER_STANDALONE: List[str] = [
    "but there was more to the story",
    "but the real story was even darker",
    "and that was just the beginning",
    "but that was only part of the story",
    "the truth was about to come out",
]


def _word_set(text: str) -> set:
    """Lowercase word set ignoring punctuation."""
    return set(re.findall(r"[a-z؀-ۿ]+", text.lower()))


def remove_filler_phrases(text: str) -> str:
    """
    Remove or neutralise known filler phrases from a script.

    Sentences that are entirely filler (> 50% of their words are from a
    filler phrase) are deleted.  Partial matches at sentence boundaries
    are stripped only when the sentence is short enough to be pure filler.
    """
    if not text:
        return text

    paragraphs = text.split("\n\n")
    cleaned_paragraphs: List[str] = []
    removed_count = 0

    for para in paragraphs:
        # Keep section markers and empty lines intact
        if not para.strip() or para.strip().startswith("[SECTION:"):
            cleaned_paragraphs.append(para)
            continue

        # Split paragraph into sentences on . ? ! ؟
        sentences = re.split(r"(?<=[.?!؟])\s+", para.strip())
        kept_sentences: List[str] = []

        for sent in sentences:
            sent_l = sent.lower().strip(" .,!?؟")
            is_filler = False

            # Check exact / substring filler
            for phrase in FILLER_PHRASES + _FILLER_STANDALONE:
                if phrase in sent_l:
                    # Only remove if the sentence is short (pure filler sentence)
                    words = sent_l.split()
                    if len(words) <= 20:
                        is_filler = True
                        break
                    # Long sentence: strip just the filler fragment if at start
                    if sent_l.startswith(phrase):
                        remainder = sent_l[len(phrase):].lstrip(" ,.:—–").strip()
                        if remainder:
                            sent = remainder[0].upper() + remainder[1:]
                        else:
                            is_filler = True
                        break

            if is_filler:
                removed_count += 1
            else:
                kept_sentences.append(sent)

        if kept_sentences:
            cleaned_paragraphs.append(" ".join(kept_sentences))

    if removed_count:
        print(f"[Quality] Removed {removed_count} filler sentence(s)")

    return "\n\n".join(cleaned_paragraphs)


def deduplicate_paragraphs(text: str, threshold: float = 0.55) -> str:
    """
    Remove paragraphs that are too similar to an earlier paragraph.

    Uses Jaccard similarity on word sets.  Preserves [SECTION:] markers.
    threshold=0.55 means 55% word overlap triggers removal.
    """
    if not text:
        return text

    paragraphs = text.split("\n\n")
    kept: List[str] = []
    seen_word_sets: List[set] = []
    removed_count = 0

    for para in paragraphs:
        stripped = para.strip()
        if not stripped or stripped.startswith("[SECTION:"):
            kept.append(para)
            continue

        words = _word_set(stripped)
        # Filter out very common stop words before Jaccard
        stop = {"the", "and", "was", "that", "had", "for", "with", "his",
                "her", "they", "were", "from", "but", "not", "who", "all",
                "one", "have", "this", "what", "when", "into", "than"}
        content_words = words - stop

        if len(content_words) < 8:
            kept.append(para)
            continue

        # Compare against all kept paragraphs
        is_dup = False
        for prev in seen_word_sets:
            prev_content = prev - stop
            if not prev_content:
                continue
            intersection = len(content_words & prev_content)
            union = len(content_words | prev_content)
            if union > 0 and intersection / union >= threshold:
                is_dup = True
                break

        if is_dup:
            removed_count += 1
        else:
            kept.append(para)
            seen_word_sets.append(words)

    if removed_count:
        print(f"[Quality] Removed {removed_count} near-duplicate paragraph(s)")

    return "\n\n".join(kept)


def detect_quality_issues(text: str) -> dict:
    """
    Scan script for quality problems. Returns a dict for logging.

    Keys: filler_count, duplicate_phrases, repetition_flags, word_count
    """
    if not text:
        return {}

    text_l = text.lower()
    filler_hits = [p for p in FILLER_PHRASES if p in text_l]

    # Detect repeated phrases (any 6-word window appearing 3+ times)
    words = re.findall(r"[a-z]+", text_l)
    window_size = 6
    phrase_counts: dict[str, int] = {}
    for i in range(len(words) - window_size):
        window = " ".join(words[i:i + window_size])
        phrase_counts[window] = phrase_counts.get(window, 0) + 1
    repeated = [p for p, c in phrase_counts.items() if c >= 3 and len(p) > 20]

    word_count = len([w for w in text.split() if w.strip()])

    return {
        "word_count":       word_count,
        "filler_count":     len(filler_hits),
        "filler_phrases":   filler_hits[:5],
        "repeated_phrases": repeated[:5],
    }


# ── Arabic translation cleanup ────────────────────────────────────────────────

# English words that should STAY in Arabic scripts (proper nouns, titles, brands)
_KEEP_EN_PATTERN = re.compile(
    r"\b(Dark Crime Decoded|Netflix|HBO|Amazon|BBC|CNN|FBI|CIA|DEA|"
    r"YouTube|TikTok|Instagram|[A-Z][a-z]+ [A-Z][a-z]+)\b"
)

# Match runs of Latin characters that are NOT in the keep list
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def post_translation_cleanup_arabic(ar_text: str) -> str:
    """
    Clean up Arabic text after machine translation.

    - Replaces Western punctuation with Arabic equivalents
    - Removes stray English fragment lines (not proper nouns / series names)
    - Normalises repeated blank lines
    - Removes lines that are >60% Latin characters (likely untranslated blocks)
    """
    if not ar_text:
        return ar_text

    # Punctuation normalisation
    ar_text = ar_text.replace("?", "؟").replace(";", "؛").replace(",", "،")

    lines: List[str] = []
    for line in ar_text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue

        # Skip section markers untouched
        if stripped.startswith("[SECTION:"):
            lines.append(line)
            continue

        # Detect Latin-heavy lines (> 60% of chars are ASCII letters)
        ascii_letters = sum(1 for c in stripped if "A" <= c <= "Z" or "a" <= c <= "z")
        total_alpha   = sum(1 for c in stripped if c.isalpha())
        if total_alpha > 0 and ascii_letters / total_alpha > 0.6 and len(stripped) > 20:
            print(f"[Quality-AR] Skipped Latin-heavy line: {stripped[:60]!r}")
            continue

        lines.append(line)

    result = "\n".join(lines)
    # Collapse 3+ consecutive blank lines to 2
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


# ── Entity contamination filter ───────────────────────────────────────────────

def filter_contaminated_facts(
    facts: List[str],
    topic: str,
    series: str = "",
) -> List[str]:
    """
    Remove facts that have zero keyword overlap with the topic or series.

    Uses a simple keyword-presence check.  Keeps facts whose content words
    include at least one keyword from topic or series.  Short facts (< 5 words)
    are always kept.

    This prevents DDG snippets about unrelated entities (e.g. TV show metadata
    about an actor when we want real-person facts) from contaminating the script.
    """
    if not facts:
        return facts

    def _keywords(text: str) -> set:
        return {w for w in re.findall(r"[a-z؀-ۿ]+", text.lower()) if len(w) > 3}

    topic_kw  = _keywords(topic)
    series_kw = _keywords(series)
    allowed   = topic_kw | series_kw

    kept: List[str] = []
    removed = 0
    for fact in facts:
        if not fact or not isinstance(fact, str):
            continue
        words = fact.split()
        if len(words) < 5:
            kept.append(fact)
            continue
        fact_kw = _keywords(fact)
        if fact_kw & allowed:
            kept.append(fact)
        else:
            print(f"[Quality] Filtered off-topic fact: {fact[:80]!r}")
            removed += 1

    if removed:
        print(f"[Quality] Entity filter removed {removed}/{len(facts)} off-topic facts")

    return kept if kept else facts  # never return empty — fall back to original


# ── Master filter ─────────────────────────────────────────────────────────────

def apply_all_quality_filters(text: str) -> str:
    """Run all post-generation quality filters in order."""
    if not text:
        return text
    text = remove_filler_phrases(text)
    text = deduplicate_paragraphs(text)
    return text
