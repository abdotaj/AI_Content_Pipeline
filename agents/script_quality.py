# agents/script_quality.py — Post-generation script quality filters
#
# Applied AFTER AI generation, BEFORE TTS.
# No imports from script_agent (prevents circular imports).
#
# Public API:
#   remove_filler_phrases(text)                              -> str
#   deduplicate_paragraphs(text, threshold)                  -> str
#   detect_quality_issues(text)                              -> dict
#   post_translation_cleanup_arabic(ar_text)                 -> str
#   normalize_arabic_documentary_text(ar_text)               -> str  (enhanced)
#   normalize_arabic_tts(ar_text)                            -> str  (TTS cadence)
#   validate_arabic_purity(ar_text)                          -> tuple[bool, list[str]]
#   enforce_arabic_purity(ar_text)                           -> str
#   filter_contaminated_facts(facts, topic, series)          -> list[str]
#   apply_all_quality_filters(text)                          -> str
#   score_fact_density(text, topic, series)                  -> dict
#   detect_fiction_bleed(text, fictional_names, section)     -> dict
#   validate_timeline_consistency(text, topic, expected_era) -> dict

import re
import unicodedata
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


# ── Enhanced Arabic normalization ─────────────────────────────────────────────

# Organization / entity name substitutions: English pattern → Arabic equivalent.
_ARABIC_ORG_SUBSTITUTIONS: List[tuple] = [
    # Crime organizations
    (r"['‘’]?[Nn]drangheta\b", "ندرانغيتا"),  # ندرانغيتا
    (r"\bndrangheta\b",                  "ندرانغيتا"),
    (r"\bCamorra\b",                     "كامورا"),                       # كامورا
    (r"\bcamorra\b",                     "كامورا"),
    (r"\bCosa Nostra\b",                 "كوزا نوسترا"),  # كوزا نوسترا
    (r"\bcosa nostra\b",                 "كوزا نوسترا"),
    (r"\bSicilian Mafia\b",              "المافيا الصقلية"),  # المافيا الصقلية
    (r"\bItalian Mafia\b",               "المافيا الإيطالية"),  # المافيا الإيطالية
    (r"\bCartel\b",                      "الكارتل"),                  # الكارتل
    (r"\bcartel\b",                      "الكارتل"),
    # Law enforcement
    (r"\bFBI\b",    "مكتب التحقيقات الفيدرالي"),  # مكتب التحقيقات الفيدرالي
    (r"\bDEA\b",    "وكالة مكافحة المخدرات"),  # وكالة مكافحة المخدرات
    (r"\bCIA\b",    "وكالة الاستخبارات المركزية"),  # وكالة الاستخبارات المركزية
    # Platforms
    (r"\bNetflix\b",     "نتفليكس"),   # نتفليكس
    (r"\bHBO\b",         "إتش بي أو"), # إتش بي أو
    (r"\bAmazon Prime\b","أمازون برايم"),  # أمازون برايم
    # Geographic proper nouns
    (r"\bVatican\b",   "الفاتيكان"),  # الفاتيكان
    (r"\bSicily\b",    "صقلية"),                           # صقلية
    (r"\bNaples\b",    "نابولي"),                     # نابولي
    (r"\bPalermo\b",   "باليرمو"),               # باليرمو
    (r"\bColombia\b",  "كولومبيا"),         # كولومبيا
    (r"\bMedellin\b",  "ميديلين"),               # ميديلين
    (r"\bBogota\b",    "بوغوتا"),                     # بوغوتا
    (r"\bChicago\b",   "شيكاغو"),                     # شيكاغو
    (r"\bNew York\b",  "نيويورك"),              # نيويورك
]

# Short Latin tokens always acceptable inside Arabic text (brands, well-known acronyms)
_ARABIC_KEEP_LATIN: frozenset = frozenset({
    "dark", "crime", "decoded", "youtube", "tiktok", "instagram",
    "facebook", "twitter", "ok", "vs",
})


def normalize_arabic_documentary_text(ar_text: str) -> str:
    """
    Enhanced Arabic documentary text normalization.

    Applied in sequence:
    1. Base cleanup (post_translation_cleanup_arabic): punctuation + Latin-heavy lines
    2. Organization / entity name substitutions (e.g. FBI → مكتب التحقيقات الفيدرالي)
    3. Stray short Latin word removal from majority-Arabic lines
    4. Whitespace / punctuation artifact cleanup
    """
    if not ar_text:
        return ar_text

    # Step 1: base cleanup
    result = post_translation_cleanup_arabic(ar_text)

    # Step 2: org name substitutions
    for pattern, replacement in _ARABIC_ORG_SUBSTITUTIONS:
        result = re.sub(pattern, replacement, result)

    # Step 3: remove stray short Latin words from majority-Arabic lines
    def _clean_stray_latin(line: str) -> str:
        ar_chars    = sum(1 for c in line if "؀" <= c <= "ۿ")
        total_alpha = sum(1 for c in line if c.isalpha())
        if total_alpha == 0 or ar_chars / total_alpha < 0.55:
            return line  # not majority Arabic — leave alone
        def _sub(m: re.Match) -> str:
            w = m.group(0)
            return w if w.lower() in _ARABIC_KEEP_LATIN else ""
        return re.sub(r"\b[A-Za-z]{1,6}\b", _sub, line)

    result = "\n".join(_clean_stray_latin(ln) for ln in result.split("\n"))

    # Step 4: artifact cleanup
    result = re.sub(r"  +", " ", result)
    result = re.sub(r" ([،؛؟])", r"\1", result)
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result.strip()


# ── Arabic TTS normalization ──────────────────────────────────────────────────

# Arabic YouTube-drama filler phrases to remove.
# These are the Arabic equivalents of the English banned phrases in FILLER_PHRASES.
_AR_FILLER_PHRASES: List[str] = [
    # YouTube-drama suspense spam
    "لكن ما حدث بعد ذلك صدم الجميع",
    "ولم يكن أحد يتوقع",
    "لكن الحقيقة كانت أكثر ظلامًا",
    "لكن الحقيقة كانت أكثر ظلاماً",
    "لكن الحقيقة كانت أشد هولاً",
    "ما اكتشفوه كان مرعبًا",
    "ما اكتشفوه كان مرعباً",
    "ما كشفه المحققون صدم الجميع",
    "في لحظة صادمة",
    "في منعطف صادم",
    "في منعطف مفاجئ",
    "في تحول مذهل",
    "كل شيء تغير إلى الأبد",
    "وهنا كانت الصدمة الكبرى التي غيرت كل شيء",
    "لم يكن أحد مستعداً لما سيحدث",
    "لم يكن أحد مستعدا لما سيحدث",
    "ولم يكن هذا نهاية المفاجآت",
    "لكن الأمر كان أسوأ مما تخيّل أي أحد",
    "لكن الأمر كان أسوأ مما تخيل أي أحد",
    "والباقي كان تاريخاً",
    "والباقي كان تاريخا",
    "هذه هي قصة",
    "هذه قصة",
    "عبر التاريخ",
    "في هذا العالم",
    "في عالم الجريمة",
    "كل شيء بدأ عندما",
    "ولم يتوقع أحد",
    "لكن ما لم يعلمه أحد",
    "ما لم يعلمه أحد",
    "ولم يكن أحد يدري",
    "ولكن الحقيقة كانت أشد إزعاجًا",
    "ولكن الحقيقة كانت أشد إزعاجا",
    "الحقيقة كانت أكثر رعبًا مما تخيّل أحد",
    "الحقيقة كانت أكثر رعباً مما تخيّل أحد",
    "في نهاية المطاف تبيّن",
    "وفي النهاية",
    "لكن الحقيقة كانت مختلفة تماماً",
]

# Maximum Arabic words per sentence for natural TTS flow.
_AR_MAX_SENTENCE_WORDS: int = 22

# Connectors where a long sentence can be cleanly split.
# Two groups: contrastive (keep connector) vs continuative (drop و prefix).
_AR_CONTRASTIVE_CONNECTORS: frozenset = frozenset({
    "لكن", "ولكن", "بينما", "غير أن", "إلا أن", "ثم", "حتى",
})
_AR_SPLIT_CONNECTOR_RE = re.compile(
    r"(?<!\s)([\s،.]+)\s*"
    r"(ثم|لكن|ولكن|وعندما|وبعد|بينما|حين|وحين|حتى|وقد|وكان|وكانت|"
    r"إذ|وإذ|إذا|وهنا|وهكذا|غير أن|إلا أن)\s+",
    re.UNICODE,
)


def _split_long_arabic_sentence(sentence: str) -> List[str]:
    """
    Split one long Arabic sentence into shorter ones at natural connectors.
    Returns the original in a one-element list if no split is possible.
    """
    words = sentence.split()
    if len(words) <= _AR_MAX_SENTENCE_WORDS:
        return [sentence]

    matches = list(_AR_SPLIT_CONNECTOR_RE.finditer(sentence))
    if not matches:
        # No connector found — split at midpoint word boundary
        mid = len(words) // 2
        first  = " ".join(words[:mid]).rstrip("،")
        second = " ".join(words[mid:])
        if first and first[-1] not in ".؟!":
            first += "."
        return [first, second] if second.strip() else [sentence]

    # Pick the connector closest to the midpoint that comes after at least 8 words
    mid_char = len(sentence) // 2
    best: re.Match | None = None
    min_dist = float("inf")
    for m in matches:
        prefix_wc = len(sentence[:m.start()].split())
        if prefix_wc < 8:
            continue
        dist = abs(m.start() - mid_char)
        if dist < min_dist:
            min_dist = dist
            best = m

    if best is None:
        best = matches[0]

    first      = sentence[:best.start()].rstrip("،").rstrip()
    connector  = best.group(2)
    after_text = sentence[best.end():]

    if first and first[-1] not in ".؟!":
        first += "."

    # Keep connector if it carries meaning; drop plain و prefixes
    if connector in _AR_CONTRASTIVE_CONNECTORS:
        second = connector + " " + after_text.lstrip()
    else:
        # Drop a leading و from the connector (وعندما → عندما, وبعد → بعد)
        bare = connector.lstrip("و")
        second = (bare + " " + after_text.lstrip()).lstrip()

    result = [first]
    result.extend(_split_long_arabic_sentence(second))  # recurse if still long
    return result


def normalize_arabic_tts(ar_text: str) -> str:
    """
    TTS cadence optimization for Arabic documentary narration.

    Applied AFTER script generation, BEFORE voiceover synthesis.

    Transformations (in order):
    1. Remove Arabic YouTube-drama filler phrases (_AR_FILLER_PHRASES)
    2. Split sentences longer than 22 words at natural Arabic connectors
    3. Normalize punctuation for clean TTS pauses
    4. Validate sentence cadence (warn on monotone length runs)
    5. Collapse excess blank lines

    Does NOT modify [SECTION:] markers.
    """
    if not ar_text:
        return ar_text

    print("[AR Narration] TTS optimization active")

    # ── Step 1: Remove Arabic filler phrases ─────────────────────────────────
    lines = ar_text.splitlines()
    cleaned: List[str] = []
    filler_removed = 0

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("[SECTION:"):
            cleaned.append(raw_line)
            continue

        line = raw_line
        for phrase in _AR_FILLER_PHRASES:
            if phrase in line:
                wc = len(line.split())
                if wc <= 18:
                    # Short sentence is pure filler — drop it
                    line = ""
                    filler_removed += 1
                    print(f"[AR Narration] Filler removed: {phrase[:50]}")
                    break
                # Long sentence — strip the filler phrase only
                line = line.replace(phrase, "").strip(" ،.")
                filler_removed += 1

        if line.strip():
            cleaned.append(line)

    if filler_removed:
        print(f"[AR Narration] {filler_removed} Arabic filler phrase(s) removed")

    result = "\n".join(cleaned)

    # ── Step 2: Split long sentences ─────────────────────────────────────────
    paragraphs = result.split("\n\n")
    split_count = 0
    new_paras: List[str] = []

    for para in paragraphs:
        stripped = para.strip()
        if not stripped or stripped.startswith("[SECTION:"):
            new_paras.append(para)
            continue

        sentences  = re.split(r"(?<=[.؟!])\s+", stripped)
        new_sents: List[str] = []

        for sent in sentences:
            wc = len(sent.split())
            if wc > _AR_MAX_SENTENCE_WORDS:
                parts = _split_long_arabic_sentence(sent)
                if len(parts) > 1:
                    split_count += 1
                    print(f"[AR Narration] Long sentence split: {wc}w → {len(parts)} parts")
                new_sents.extend(parts)
            else:
                new_sents.append(sent)

        new_paras.append(" ".join(s for s in new_sents if s.strip()))

    if split_count:
        print(f"[AR Narration] {split_count} long sentence(s) split for TTS flow")

    result = "\n\n".join(new_paras)

    # ── Step 3: Punctuation normalization ────────────────────────────────────
    result = result.replace("?", "؟")
    result = re.sub(r"\.{2,}", ".", result)          # collapse multiple periods
    result = re.sub(r"،{2,}", "،", result)           # collapse duplicate Arabic commas
    result = re.sub(r"\s([،؛؟.])", r"\1", result)   # no space before Arabic punctuation
    result = re.sub(r"([.؟!])\s*([.؟!])", r"\1", result)  # remove doubled end marks

    # ── Step 4: Cadence validation (log only — no forced edits) ──────────────
    all_sents = re.split(r"(?<=[.؟!])\s+", result)
    lengths   = [len(s.split()) for s in all_sents if len(s.split()) >= 4]
    if lengths:
        mono_runs = sum(
            1 for i in range(2, len(lengths))
            if abs(lengths[i] - lengths[i-1]) <= 2 and abs(lengths[i-1] - lengths[i-2]) <= 2
        )
        if mono_runs > 3:
            print(f"[AR Narration] Sentence cadence normalized — {mono_runs} monotone run(s) detected")
        else:
            print("[AR Narration] Sentence cadence normalized")

    # ── Step 5: Clean up ──────────────────────────────────────────────────────
    result = re.sub(r"\n{3,}", "\n\n", result)
    print("[AR Narration] Native rewrite active")
    return result.strip()


# ── Arabic purity validation ──────────────────────────────────────────────────

# Max ratio of Latin-word tokens allowed in Arabic documentary text.
_AR_LATIN_RATIO_THRESHOLD: float = 0.08  # >8% → impure

# English words that commonly leak into Arabic LLM output untranslated.
_AR_COMMON_LEAKAGE_WORDS: frozenset = frozenset({
    "archaeological", "archaeologist", "archaeology", "excavation", "artifact",
    "civilization", "dissent", "technology", "unwavering", "determination",
    "unprecedented", "sustainability", "innovation", "transformation",
    "implementation", "documentation", "investigation", "organization",
    "administration", "communication", "integration", "foundation",
    "operation", "resolution", "declaration", "constitution", "infrastructure",
    "rehabilitation", "reconstruction", "interpretation", "collaboration",
    "participation", "motivation", "assassination", "appreciation",
    "nevertheless", "furthermore", "consequently", "simultaneously",
    "additionally", "unfortunately", "predominantly", "significantly",
})


def validate_arabic_purity(text: str) -> tuple[bool, List[str]]:
    """
    Detect Latin/English leakage in Arabic documentary narration.

    Checks:
    - Latin word ratio (>8% of words → impure)
    - Known English leakage words (untranslated terms)
    - Hybrid Arabic-Latin tokens (e.g. "تتblur", "wordsكلام")

    Returns (is_pure, issues_list).
    """
    if not text:
        return True, []

    issues: List[str] = []
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("[SECTION:")]
    if not lines:
        return True, []

    total_words = 0
    total_latin  = 0

    for line in lines:
        for word in line.split():
            clean = re.sub(r'[^\w]', '', word, flags=re.UNICODE)
            if not clean:
                continue
            total_words += 1
            latin_chars  = len(re.findall(r'[a-zA-Z]', clean))
            arabic_chars = len(re.findall(r'[؀-ۿ]', clean))
            if latin_chars >= 3 and latin_chars > arabic_chars:
                total_latin += 1
                clow = clean.lower()
                if clow in _AR_COMMON_LEAKAGE_WORDS:
                    issues.append(f"Known leakage word: '{clean}'")

    if total_words > 0:
        ratio = total_latin / total_words
        if ratio > _AR_LATIN_RATIO_THRESHOLD:
            issues.insert(0, f"Latin word ratio {ratio:.1%} > {_AR_LATIN_RATIO_THRESHOLD:.0%} ({total_latin}/{total_words} words)")

    # Hybrid token check (Arabic chars merged with Latin chars in one token)
    hybrids = re.findall(r'[؀-ۿ]+[a-zA-Z]{2,}|[a-zA-Z]{2,}[؀-ۿ]+', text)
    for h in hybrids[:5]:
        issues.append(f"Hybrid Arabic-Latin token: '{h}'")

    return len(issues) == 0, issues


def enforce_arabic_purity(ar_text: str) -> str:
    """
    Remove Latin/English leakage from Arabic documentary narration.

    Cleans:
    - Standalone Latin words (≥3 alphabetic chars, >50% Latin)
    - Hybrid Arabic-Latin tokens (e.g. "تتblur")
    - Known English leakage words

    Preserves: [SECTION:] markers, numbers, Arabic text, punctuation.
    Applied AFTER normalize_arabic_tts().
    """
    if not ar_text:
        return ar_text

    is_pure, issues = validate_arabic_purity(ar_text)
    if is_pure:
        return ar_text

    print(f"[AR Purity] {len(issues)} issue(s) detected — enforcing Arabic purity")
    for issue in issues[:6]:
        print(f"[AR Purity]   {issue}")

    lines = ar_text.splitlines()
    cleaned: List[str] = []

    for line in lines:
        if not line.strip() or line.strip().startswith("[SECTION:"):
            cleaned.append(line)
            continue

        # Remove hybrid tokens: strip trailing Latin from Arabic, leading Latin before Arabic
        line = re.sub(
            r'([؀-ۿ]+)[a-zA-Z]{2,}',
            r'\1',
            line,
        )
        line = re.sub(
            r'[a-zA-Z]{2,}([؀-ۿ]+)',
            r'\1',
            line,
        )

        # Remove standalone Latin words (≥3 alpha chars, predominantly Latin)
        def _drop_latin(m: re.Match) -> str:
            w = m.group()
            core = re.sub(r'[^\w]', '', w, flags=re.UNICODE)
            if not core:
                return w
            lat = len(re.findall(r'[a-zA-Z]', core))
            arb = len(re.findall(r'[؀-ۿ]', core))
            if lat >= 3 and lat > arb:
                return ""
            return w

        line = re.sub(r'\S+', _drop_latin, line)
        line = re.sub(r'[ \t]{2,}', ' ', line).strip()
        if line:
            cleaned.append(line)

    result = "\n".join(cleaned)
    result = re.sub(r'\n{3,}', '\n\n', result).strip()

    removed = len(ar_text.split()) - len(result.split())
    if removed > 0:
        print(f"[AR Purity] Removed {removed} Latin/hybrid token(s)")
    print("[AR Purity] Arabic purity enforced")
    return result


# ── Timeline & entity consistency validation ─────────────────────────────────

def enforce_arabic_purity(ar_text: str) -> str:
    """
    Final Arabic sanitizer: strip non-Arabic Unicode, invisible glyphs,
    hybrid tokens, and unapproved Latin while preserving vetted entities.
    """
    if not ar_text:
        return ar_text

    allowed_latin = {
        "Jordan Belfort",
        "Stratton Oakmont",
        "FBI",
        "SEC",
        "Leonardo DiCaprio",
    }
    placeholders: dict[str, str] = {}
    protected = ar_text
    for idx, name in enumerate(sorted(allowed_latin, key=len, reverse=True)):
        token = f"__{idx}__"
        protected = re.sub(re.escape(name), token, protected, flags=re.IGNORECASE)
        placeholders[token] = name

    cleaned: List[str] = []
    removed_unicode = 0
    removed_hybrid = 0
    removed_latin = 0

    for line in protected.splitlines():
        if not line.strip() or line.strip().startswith("[SECTION:"):
            cleaned.append(line)
            continue

        before = line
        line = re.sub(r'[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]', '', line)
        line = re.sub(r'[\u0600-\u06ff]+[A-Za-z]{2,}|[A-Za-z]{2,}[\u0600-\u06ff]+', '', line)
        if line != before:
            removed_hybrid += 1

        chars: list[str] = []
        for ch in line:
            if ch == "_" or ch.isascii():
                chars.append(ch)
                continue
            code = ord(ch)
            cat = unicodedata.category(ch)
            allowed = (
                0x0600 <= code <= 0x06FF
                or 0x0750 <= code <= 0x077F
                or 0x08A0 <= code <= 0x08FF
                or 0xFB50 <= code <= 0xFDFF
                or 0xFE70 <= code <= 0xFEFF
                or cat[0] in {"P", "N", "Z"}
            )
            if allowed:
                chars.append(ch)
            else:
                removed_unicode += 1
        line = "".join(chars)

        def _drop_unapproved_latin(m: re.Match) -> str:
            nonlocal removed_latin
            token = m.group()
            if token in placeholders:
                return token
            removed_latin += 1
            return ""

        line = re.sub(r'\b[A-Za-z][A-Za-z.\'-]*\b', _drop_unapproved_latin, line)
        line = re.sub(r'[ \t]{2,}', ' ', line).strip()
        if line:
            cleaned.append(line)

    result = "\n".join(cleaned)
    for token, name in placeholders.items():
        result = result.replace(token, name)
    result = re.sub(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff]', '', result)
    result = re.sub(r'\n{3,}', '\n\n', result).strip()

    if removed_unicode:
        print(f"[AR PURITY] Removed Unicode contamination: {removed_unicode} char(s)")
    if removed_hybrid:
        print("[AR PURITY] Removed hybrid token")
    if removed_latin:
        print(f"[AR PURITY] Removed unapproved Latin token(s): {removed_latin}")
    print("[AR PURITY] Final validation passed")
    return result


# Fictional characters from crime/drama shows that should NEVER appear in the
# historical narration sections (only allowed in "Show vs Reality" chapters).
_KNOWN_FICTIONAL_CHARACTERS: List[str] = [
    "lucius vorenus", "vorenus", "titus pullo", "pullo",
    "tony soprano", "christopher moltisanti",
    "walter white", "heisenberg", "jesse pinkman",
    "nucky thompson",
    "saul goodman", "jimmy mcgill", "gus fring", "mike ehrmantraut",
    "tommy shelby", "arthur shelby", "polly gray",
    "marty byrde", "omar little", "stringer bell", "avon barksdale",
    "don corleone", "michael corleone", "vito corleone",
    "frank castle", "tony montana",
    "suburra character", "suburra fictional",
]

# Pairs that signal cross-topic contamination:
# (keyword_in_topic, keyword_that_should_NOT_appear_in_script)
_CROSS_TOPIC_PAIRS: List[tuple] = [
    ("rome",        "escobar"),
    ("rome",        "ndrangheta"),
    ("rome",        "camorra"),
    ("rome",        "pablo"),
    ("escobar",     "vorenus"),
    ("escobar",     "pullo"),
    ("escobar",     "tesla"),
    ("narcos",      "vorenus"),
    ("narcos",      "tesla"),
    ("narcos",      "roman empire"),
    ("tesla",       "mafia"),
    ("tesla",       "cartel"),
    ("tesla",       "pablo"),
    ("godfather",   "tesla"),
    ("godfather",   "roman empire"),
    ("suburra",     "escobar"),
    ("suburra",     "narcos"),
    ("kuklinski",   "tesla"),
    ("dahmer",      "narcos"),
    ("legend",      "tesla"),
    ("legend",      "roman empire"),
    ("gacy",        "escobar"),
    ("bundy",       "escobar"),
]


def validate_timeline_consistency(
    text: str,
    topic: str = "",
    expected_era: str = "",
) -> dict:
    """
    Detect entity/timeline contamination in a generated script.

    Checks:
    1. Known fictional character names in non-Show-vs-Reality sections
    2. Year references far outside the expected era
    3. Named criminals (from entity_guard) not related to the active topic
    4. Known cross-topic pairs (e.g. 'Escobar' in a 'Rome' script)

    Returns:
        consistent:      bool — True if zero violations found
        fiction_bleed:   list of fictional names found in wrong sections
        era_violations:  list of years outside expected range
        contamination:   list of blocked entity names found
        cross_topic:     list of cross-topic pair violations
        violation_count: total violation count
    """
    if not text:
        return {
            "consistent": True, "fiction_bleed": [], "era_violations": [],
            "contamination": [], "cross_topic": [], "violation_count": 0,
        }

    topic_lower = topic.lower()

    # ── 1. Split text into Show-vs-Reality vs everything else ────────────────
    _sec_re = re.compile(r"\[SECTION:\s*([^\]]+)\]", re.IGNORECASE)
    parts   = _sec_re.split(text)
    # parts layout (capturing group): [pre, label1, body1, label2, body2, ...]
    other_text = parts[0]
    for i in range(1, len(parts), 2):
        label   = parts[i].lower() if i < len(parts) else ""
        body    = parts[i + 1] if i + 1 < len(parts) else ""
        # Allow fictional names ONLY in Show vs Reality / Real vs Screen sections
        if "show vs" in label or "real vs screen" in label or "show vs reality" in label:
            continue
        other_text += " " + body

    other_lower = other_text.lower()

    # ── 2. Fiction bleed ─────────────────────────────────────────────────────
    fiction_bleed: List[str] = [
        fc for fc in _KNOWN_FICTIONAL_CHARACTERS if fc in other_lower
    ]

    # ── 3. Cross-topic contamination ─────────────────────────────────────────
    cross_topic: List[str] = []
    for (topic_kw, forbidden_kw) in _CROSS_TOPIC_PAIRS:
        if topic_kw in topic_lower and forbidden_kw in text.lower():
            cross_topic.append(f"'{forbidden_kw}' in a '{topic_kw}' script")

    # ── 4. Era year violations ────────────────────────────────────────────────
    era_violations: List[str] = []
    if expected_era:
        year_refs = [int(y) for y in re.findall(r"\b(1[0-9]{3}|20[0-2][0-9])\b", expected_era)]
        if year_refs:
            era_min = min(year_refs) - 60
            era_max = max(year_refs) + 40
            script_years = re.findall(r"\b(1[0-9]{3}|20[0-2][0-9])\b", text)
            era_violations = list({y for y in script_years if not (era_min <= int(y) <= era_max)})

    # ── 5. Named-criminal contamination (entity_guard) ────────────────────────
    contamination: List[str] = []
    if topic:
        try:
            import sys, os
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _root not in sys.path:
                sys.path.insert(0, _root)
            try:
                from agents.entity_guard import build_active_entity, is_single_subject
            except ImportError:
                from agent.entity_guard import build_active_entity, is_single_subject
            if is_single_subject(topic):
                entity  = build_active_entity(topic)
                blocked = entity.get("blocked_entities", [])
                t_lower = text.lower()
                contamination = [n for n in blocked if len(n) > 4 and n.lower() in t_lower]
        except Exception:
            pass

    total = len(fiction_bleed) + len(cross_topic) + len(era_violations) + len(contamination)
    return {
        "consistent":      total == 0,
        "fiction_bleed":   fiction_bleed[:5],
        "era_violations":  era_violations[:5],
        "contamination":   contamination[:5],
        "cross_topic":     cross_topic[:5],
        "violation_count": total,
    }


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


# ── Fact density scoring ──────────────────────────────────────────────────────

def score_fact_density(text: str, topic: str = "", series: str = "") -> dict:
    """
    Score the factual density of a script.

    Returns a dict with:
        fact_count          — sentences containing a year, number, or proper noun
        total_sentences     — total sentence count
        density_pct         — fact_count / total_sentences * 100
        topic_mentions      — how often the topic name appears
        series_mentions     — how often the series name appears
        verdict             — "HIGH" / "MEDIUM" / "LOW"
    """
    if not text:
        return {}

    sentences = re.split(r"(?<=[.?!؟])\s+", text.strip())
    total = len([s for s in sentences if len(s.split()) >= 5])
    if total == 0:
        return {"verdict": "EMPTY"}

    # Sentence has a year (1900-2099), a number, or starts with a capitalized proper noun
    _year_re     = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
    _number_re   = re.compile(r"\b\d+\b")
    _proper_re   = re.compile(r"\b[A-Z][a-z]{2,}")

    fact_count = 0
    for sent in sentences:
        if len(sent.split()) < 5:
            continue
        if _year_re.search(sent) or _number_re.search(sent) or len(_proper_re.findall(sent)) >= 2:
            fact_count += 1

    density = (fact_count / total) * 100 if total else 0
    topic_kw    = topic.lower().split() if topic else []
    series_kw   = series.lower().split() if series else []
    text_lower  = text.lower()
    t_mentions  = sum(text_lower.count(w) for w in topic_kw if len(w) > 3)
    s_mentions  = sum(text_lower.count(w) for w in series_kw if len(w) > 3)

    verdict = "HIGH" if density >= 60 else "MEDIUM" if density >= 35 else "LOW"
    return {
        "fact_count":      fact_count,
        "total_sentences": total,
        "density_pct":     round(density, 1),
        "topic_mentions":  t_mentions,
        "series_mentions": s_mentions,
        "verdict":         verdict,
    }


# ── Fiction bleed detection ───────────────────────────────────────────────────

def detect_fiction_bleed(
    text: str,
    fictional_names: List[str],
    allowed_section: str = "show vs reality",
) -> dict:
    """
    Detect fictional character names appearing OUTSIDE the allowed section.

    Returns dict with:
        bleed_count   — number of contaminated paragraphs
        offenders     — list of (paragraph_excerpt, fictional_name) pairs
    """
    if not text or not fictional_names:
        return {"bleed_count": 0, "offenders": []}

    # Split into labelled sections if markers present
    section_pattern = re.compile(r"\[SECTION:\s*([^\]]+)\]", re.IGNORECASE)
    parts = section_pattern.split(text)

    offenders = []
    current_section = "unknown"

    for chunk in parts:
        # section label — update tracker
        if section_pattern.fullmatch(chunk.strip()):
            current_section = chunk.strip().lower()
            continue
        # Check if this is the allowed section
        if allowed_section.lower() in current_section:
            continue
        # Look for fictional names in non-allowed sections
        chunk_lower = chunk.lower()
        for name in fictional_names:
            if len(name) < 4:
                continue
            if name.lower() in chunk_lower:
                excerpt = chunk.strip()[:80]
                offenders.append((excerpt, name))

    return {
        "bleed_count": len(offenders),
        "offenders":   offenders[:5],
    }


# ── Master filter ─────────────────────────────────────────────────────────────

def apply_all_quality_filters(text: str) -> str:
    """Run all post-generation quality filters in order."""
    if not text:
        return text
    text = remove_filler_phrases(text)
    text = deduplicate_paragraphs(text)
    return text
