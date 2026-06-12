# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  English for YouTube, Arabic is a direct translation
# ============================================================
import json
import os
import re
import groq as groq_lib
from groq import Groq
from config import GROQ_API_KEY, LONG_VIDEO_DURATION
from agents.entity_guard import (
    build_active_entity,
    entity_lock_instruction,
    is_single_subject,
    sanitize_script,
    validate_entity_consistency,
)
from agents.json_utils import safe_json_parse, is_valid_json_response, normalize_ai_json_response

_groq = Groq(api_key=GROQ_API_KEY)

_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",   # primary
    "llama-3.1-8b-instant",      # fallback
]


def safe_lower(v) -> str:
    """Return v.lower() or '' — never raises AttributeError on None."""
    return (v or "").lower() if isinstance(v, str) else "" if v is None else str(v).lower()


def _groq_call(**kwargs):
    """Try each model with one 40-second retry on rate limit before moving to fallback."""
    import time
    global _GROQ_RATE_LIMITED_UNTIL

    if time.time() < _GROQ_RATE_LIMITED_UNTIL:
        _remaining = int(_GROQ_RATE_LIMITED_UNTIL - time.time())
        print(f"[Groq] Rate-limited — skipping (cooldown {_remaining}s remaining)")
        raise Exception(f"Groq rate-limited (cooldown: {_remaining}s)")

    last_err = None
    for model in _FALLBACK_MODELS:
        # llama-3.1-8b-instant has a 6000 TPM limit — cap max_tokens so
        # prompt + response stays under 6000 total tokens
        call_kwargs = dict(kwargs)
        if model == "llama-3.1-8b-instant":
            call_kwargs["max_tokens"] = min(call_kwargs.get("max_tokens", 2000), 4000)

        for attempt in range(2):
            try:
                time.sleep(3)
                return _groq.chat.completions.create(model=model, **call_kwargs)
            except groq_lib.RateLimitError as e:
                last_err = e
                _GROQ_RATE_LIMITED_UNTIL = time.time() + 60
                if attempt == 0:
                    print(f"[Groq] Rate limit hit — waiting 40 seconds...")
                    time.sleep(40)
                else:
                    print(f"[Groq] Rate limit again on {model}, trying next model...")
                    break
            except groq_lib.BadRequestError as e:
                print(f"[Groq] BadRequestError on {model}, trying next model...")
                last_err = e
                break
    raise last_err



_SCRIPT_SYSTEM_PROMPT = """You are a cinematic longform storyteller for a Netflix-style true crime channel. Your job is NOT to write documentary summaries. Your job is to write SCENE-DRIVEN SOCIAL STORYTELLING that keeps viewers locked in for 15-90 minutes.

TARGET STYLE: Netflix true crime + MrBallen rhythm + cinematic investigative storytelling.
NOT: TV educational documentary narration. NOT: Wikipedia article narration.

NARRATIVE MOMENTUM ENGINE — THE SINGLE MOST IMPORTANT RULE:
Runtime must come from STORY MOVEMENT, not word inflation.

ABSOLUTE FAILURE MODES — if you write any of these, the script fails:
- Long static explanatory paragraphs that summarise what happened
- Repeated biographical facts restated in different words
- Psychological analysis repeated across sections
- Atmosphere narration with no story progression
- Timeline recaps that restate what was already told

REQUIRED: Every 50-80 words (~20-40 seconds of audio), the viewer MUST experience ONE of:
  • New clue or evidence discovery (specific name, date, or item)
  • Contradiction: what investigators believed vs what they found
  • Emotional shift: a decision that changes the direction of events
  • Investigation turn: unexpected development or dead end
  • Danger escalation or consequence
  • Reveal: hidden information surfaces for the first time
  • Setback: something goes wrong, a plan collapses
  • New character entering with a specific action or secret

SCENE-DRIVEN WRITING (mandatory):
Replace summary narration with cinematic scene progression.
BAD:  "Bundy manipulated investigators for years."
GOOD: "The detective believed the witness had finally identified him.
      Then the phone rang.
      Another victim had disappeared."

BAD:  "He built a criminal empire over the next decade."
GOOD: "By 1975, the operation covered three cities.
      The FBI knew something was happening.
      They had no idea how big."

Each section must move through a story arc:
  event → reaction → investigation → discovery → escalation → setback → revelation → consequence

CINEMATIC VOICE — NOT DOCUMENTARY:
- This is SPOKEN narration, not text to be read on a screen. Every sentence must sound natural when read aloud.
- Never open with generic lines: "In the world of crime...", "Throughout history...", "This story is about..."
- Never write like a Wikipedia article, a blog post, or an essay. No thesis statements.
- Write the SCENE, not the summary. Put the viewer inside the moment.
- Every paragraph must EARN its place — no padding, no restatements of what was just said.

SENTENCE STRUCTURE FOR TTS:
- Mix short punches (5–10 words) with medium narrative sentences (15–22 words). Vary the rhythm.
- Maximum 25 words per sentence — anything longer breaks spoken flow.
- No mid-sentence dashes or parentheses. No ellipses — end sentences with a period.
- No acronyms without spelling them out first. Numbers under one hundred written as words.

NARRATION FLOW:
- Minimum 3 sentences per paragraph, maximum 5. No single-sentence paragraphs.
- No bullet points. No numbered lists. No standalone facts. Prose only.
- Show cause and effect: decisions lead to consequences, actions reveal character.
- Always write COMPLETE sentences. Never end mid-thought.

BANNED PHRASES — never write any of these under any circumstances:
- "the reality was darker than anyone knew"
- "the truth was far more disturbing"
- "the truth was far more sinister"
- "the reality was far darker"
- "what would later emerge changed everything"
- "what nobody expected was"
- "what came next shocked everyone"
- "what happened next shocked everyone"
- "little did anyone know"
- "little did they know"
- "in a shocking twist"
- "in a stunning twist"
- "nobody could have predicted"
- "no one could have predicted"
- "behind the scenes"
- "throughout history"
- "this is a story about"
- "it all started when"
- "it all began when"
- "years later the truth finally emerged"
- "the world would never be the same"
- "changed the world forever"
- "introduced millions of people to this incredible true story"
- "but the real events were even more extraordinary"
- "the real story is even more fascinating"
- "and that was just the beginning"
- "but there was more to the story"
- "the truth was about to come out"
Instead, name the exact fact, person, or date that carries the tension. Make the fact do the work.

NO REPETITION RULES — absolute:
- Never repeat a fact, name, date, or event that appeared in an earlier chapter.
- Never restate a chapter's opening sentence or theme in a later chapter.
- Never use the same adjective or descriptor twice in a script (e.g., "ruthless" — use it once, never again).
- If you find yourself writing something that was already said, stop and write something new instead.

CHARACTER COVERAGE:
- Cover ALL main characters — never focus on just one person.
- Every key person gets their own dedicated paragraph: full name, actual role, what they did, their fate.
- Women, minorities, and supporting figures get EQUAL coverage — never relegate them to a passing mention.

VOICE TONE:
- Calm. Investigative. Slightly unsettling.
- The narrator knows more than they are saying — and the viewer senses it.
- Not academic. Not casual. Not excited. Never sensationalist.
- 85% dark and controlled. 15% dry understatement for criminal mistakes and ironic twists.
- Example of dry understatement: "He planned the perfect crime. He forgot that cameras exist."
- Never mock victims. Dry humor only at criminals or corrupt officials. One line maximum per chapter.

DOCUMENTARY DENSITY ENGINE — non-negotiable for every chapter:
Every 50–75 words (≈ 20–30 seconds of narration) MUST introduce at least ONE of:
  • New clue, lead, or discovery with a specific name or date
  • Named person with a specific action, decision, or consequence
  • Timeline shift: a documented year, event, or location change
  • Forensic or physical evidence item
  • Witness statement, court testimony, or documented quote
  • Psychological revelation about a person's motive or mental state
  • Investigative development: arrest, raid, interrogation, or court ruling
  • Contradiction or reversal: what was believed vs. what was proven
  • Hidden information the public never learned about the case

ATMOSPHERIC LANGUAGE LIMITS — strict enforcement:
Words like "shadows", "darkness", "silence", "fear", "mystery" are acceptable at most ONCE per 400 words.
Isolated short dramatic lines (3–6 words) are BANNED as standalone sentences.
BAD: "The fear grew." / "Silence fell." / "Nobody spoke."
GOOD: "Her fear grew when she recognised his handwriting on the second note."
Every sentence must either advance the narrative, reveal specific information, or build character psychology through documented facts.

NARRATIVE TENSION — sustained across every chapter:
- Each chapter must introduce a new unresolved question, hidden conflict, or suppressed truth.
- Do NOT fully resolve tension within the same chapter — leave something open that pulls into the next.
- The viewer must always feel: there is something I still do not know.

HOOK QUALITY — first 1-2 sentences of every chapter:
- Must contain ONE of: a contradiction, a hidden truth, a shocking omission, or an open question.
- Strong examples: "Everyone knew his name. Nobody knew his real one." / "The police had the evidence. They buried it."
- NEVER open with background, dates, or scene-setting. Start with the tension, not the context.

OPENING INTENSITY — first 3 sentences of Chapter 1 only:
- Sentence 1 MUST introduce: a conflict, a secret, or a rule being broken. No context. No background.
- Sentences 2 and 3 MUST escalate — not explain. Each sentence adds pressure, not information.
- Maximum 16 words per sentence in the opening. Shorter is stronger.
- Create an open loop: leave a question unanswered that forces the viewer to keep watching.
- Example style: "The FBI sat across from a killer. They were smiling. Nobody in that room was telling the truth."
- BANNED in first 3 sentences: character backstory, dates, place names as openers, general context.

MICRO-TENSION — sustained within every chapter:
- Every 2-3 sentences must introduce new tension, mystery, or escalation.
- Use: unanswered questions, contradictions, reveals, or unexpected reversals.
- Avoid: long explanations, flat narration, lists of facts without emotional weight.
- The viewer must feel momentum — as if the story is accelerating toward something.
- NEVER write more than 4 consecutive sentences without a story beat (clue, twist, reveal, or consequence).

BINGE-STORYTELLING CONTRACT — the viewer must feel:
"I am trapped inside a cinematic crime story."
NOT: "I am listening to a narrated article."
Each section must feel like a MINI-THRILLER SEQUENCE, not a factual explanation block.

FACT PRIORITIZATION:
- Lead with controversial, unknown, or psychologically revealing facts.
- Skip generic filler: "He was born in...", "The show premiered in...", "This is a story about..."
- Every fact chosen must answer: what does this reveal that the viewer did not expect?

ENDING STRENGTH — final 1-2 sentences of every chapter:
- Must leave an impact, raise a disturbing question, or reveal a deeper implication.
- Never close with a summary: "So that is what happened..." / "That is the story of..."
- The last line of every chapter should make the viewer need to continue.

SHOW vs REALITY — for biopics, true crime series, and historical dramas:
- Chapter 4 MUST contain:
  Part A starting with EXACTLY: "Here is what [show] got RIGHT:"
  Part B starting with EXACTLY: "Here is what they completely changed or left out:"
- Each part covers at least 3 specific comparisons with real names, scenes, or dates.
- This structure is MANDATORY for any topic based on true events.

══════════════════════════════════════
YOUTUBE COMMUNITY GUIDELINES COMPLIANCE — MANDATORY
══════════════════════════════════════
This script will be published on YouTube. Violating these rules causes age-restriction or removal.
YouTube's AI scans every sentence. Clinical, journalistic, consequence-focused language passes.
Graphic physical descriptions of death or harm — even for real historical events — trigger age-restriction.

VIOLENCE + DEATH — NEVER write:
- Wounds, injuries, bleeding, body parts, or the physical mechanics of killing
- Graphic descriptions of dying, suffering, or corpses
- "Bodies were torn apart", "blood covered", "limbs scattered", "skull", "entrails"
BAD: "The explosion ripped through the building, killing dozens — their bodies thrown across the street."
GOOD: "The explosion killed 257 people across thirteen locations in under two hours."
Rule: State the death/event occurred + casualty count. Move immediately to investigation and consequences.

SUICIDE + SELF-HARM — NEVER write:
- Methods, descriptions, or physical details of suicide or self-harm
- Romanticized or graphic accounts of someone dying by suicide
- Descriptions of the physical experience of dying
BAD: "He hanged himself in his cell, his body found in the morning."
GOOD: "He was found dead in his cell — authorities ruled it a suicide."

BOMBINGS + MASS CASUALTY EVENTS — NEVER write:
- Descriptions of victim suffering, injury, or death scenes
- How the bomb worked, where it was placed for maximum harm, or its physical effect on victims
BAD: "The car bomb tore apart the market, burning survivors fleeing in every direction."
GOOD: "The bomb killed 54 people and wounded hundreds more. Investigators traced the explosives to a Pakistan-based cell."

GENERAL GRAPHIC CONTENT — ALWAYS avoid:
- Gore, torture, mutilation in descriptive language
- Step-by-step criminal methods (how to build a bomb, poison someone, etc.)
- Any language a parent would not want a teenager to hear on YouTube

SAFE FRAMING — use these constructions:
- "killed X people" / "injured X" / "resulted in X deaths"
- "was found dead" / "died in custody" / "was executed by the state"
- "investigators discovered evidence of" / "court records showed" / "forensic analysis revealed"
- "the attack caused mass casualties" / "victims included civilians"

Remember: consequences, investigations, legal proceedings, and human impact are always safe.
Physical descriptions of violence or death are never safe for YouTube."""


def clean_word_count(text: str) -> int:
    """Count only real vocabulary words — strips punctuation, ellipses, line breaks."""
    import re
    cleaned = re.sub(r'[^\w\s]', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return len([w for w in cleaned.split() if w.strip()])


# ── Strict minimum word floors by mode + language ─────────────────────────────
# GLOBAL RULE: NO long video under 15 minutes — automatic failure.
# Word counts are SAFETY FLOORS only. Real runtime authority is measured TTS audio:
#   script → draft TTS → real duration → expand (new scenes only) → lock contract.
# Runtime targets (actual rendered audio — not WPM estimates):
#   Arabic: FAST 30-75 min | ANIMATION 35-60 min | FULL 30-75 min
#   English: FAST 10-15 min | ANIMATION 12-18 min | FULL 15-20 min
# WPM calibration (measured from production, OpenAI TTS Nova at speed=1.1):
#   Arabic Nova speed=1.1:  ~185 WPM  (range 170-190, Nova voice at 1.1× speed)
#   English Alloy 1.0: ~160 WPM (range 155-165, very consistent)
_WORD_FLOORS = {
    "fast":      {"english": 2_325, "arabic": 5_500},   # 15 min EN × 155 | 30 min AR × 185 (hard minimum)
    "full":      {"english": 4_000, "arabic": 5_500},   # 15 min EN × 160 | 30 min AR × 185
    "animation": {"english": 2_500, "arabic": 6_500},   # 12 min EN × 160 | 35 min AR × 185
    "short":     {"english": 0,     "arabic": 0},
}
_WORD_CEILINGS = {
    "fast":      {"english": 3_000,  "arabic": 13_750},  # 18 min EN × 160 | 75 min AR × 185
    "full":      {"english": 6_500,  "arabic": 13_750},  # 20 min EN × 160 | 74 min AR × 185
    "animation": {"english": 4_000,  "arabic": 11_000},  # 18 min EN × 160 | 60 min AR × 185
    "short":     {"english": 500,    "arabic": 500},
}
# Legacy aliases — write_long_script_split and callers use fast/full English by default.
LONG_SCRIPT_MIN_WORDS: int = _WORD_FLOORS["fast"]["english"]    # 2,325 (15 min × 155 WPM)
LONG_SCRIPT_MAX_WORDS: int = _WORD_CEILINGS["fast"]["english"]  # 3,000 (18 min × 160 WPM)

# ── Story variation profiles — prevents formula fatigue ──────────────────────
# Each profile shifts the narrative APPROACH without removing the 5-act structure.
# Selection is deterministic per topic (MD5 hash) → consistent across re-runs.
_STORY_VARIATION_PROFILES: dict[str, dict] = {
    "slow_burn": {
        "description": "Build dread gradually. Linger on normalcy before the crack appears.",
        "opening_style": "Open at a quiet moment — then pull back to reveal what's wrong beneath the surface.",
        "act_emphasis": "Act 1: stretch the atmosphere of normalcy. Act 3: layered slow reveals, not rapid fire.",
    },
    "courtroom_heavy": {
        "description": "Legal proceedings drive the revelation. Testimony is the turning point.",
        "opening_style": "Open with a single piece of testimony — then rewind to show how we got here.",
        "act_emphasis": "Act 4: expand courtroom moments — each witness stand is a story beat, not a summary.",
    },
    "panic_escalation": {
        "description": "Events accelerate rapidly. Each scene shorter and sharper than the last.",
        "opening_style": "Open mid-crisis — chaos already happening. Then rewind to show what lit the fuse.",
        "act_emphasis": "Act 3: rapid succession of short punchy scenes. No breathing room between events.",
    },
    "psychological_collapse": {
        "description": "Internal unraveling. Behavioral tells and contradictions drive the story.",
        "opening_style": "Open with the moment the facade cracked — a small detail nobody recognised at the time.",
        "act_emphasis": "Act 2: focus on psychological evidence and behavioral investigation. Show the mind, not just the acts.",
    },
    "mystery_first": {
        "description": "Begin with the unsolved question. Viewer discovers alongside investigators.",
        "opening_style": "Open with the anomaly — the detail that didn't fit. What started the investigation.",
        "act_emphasis": "Act 1: mystery framing — what's missing, what doesn't add up. Hold back the answer.",
    },
    "manhunt_first": {
        "description": "Pursuit drives the narrative. Each act tightens the net.",
        "opening_style": "Open at a moment of near-capture — then reveal how far the hunt had to go.",
        "act_emphasis": "Act 2: manhunt mechanics — surveillance, informants, near-misses. The mechanics of pursuit.",
    },
}


def _pick_variation_profile(topic_name: str, series_name: str = "") -> dict:
    """Select a narrative variation profile deterministically per topic (MD5 hash)."""
    import hashlib as _hl
    keys = list(_STORY_VARIATION_PROFILES.keys())
    idx  = int(_hl.md5(f"{topic_name}{series_name}".encode()).hexdigest(), 16) % len(keys)
    key  = keys[idx]
    profile = _STORY_VARIATION_PROFILES[key].copy()
    profile["name"] = key
    return profile


def _cap_script_max_words(script_text: str, max_words: int = LONG_SCRIPT_MAX_WORDS) -> str:
    """
    Hard-cap spoken script length by word count while preserving section marker lines.
    Keeps long videos safely under publishing limits.
    """
    import re

    if clean_word_count(script_text) <= max_words:
        return script_text

    kept: list[str] = []
    used = 0

    for raw_line in (script_text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            kept.append("")
            continue

        if stripped.startswith("[SECTION:"):
            kept.append(line)
            continue

        words = re.findall(r"[A-Za-z0-9\u0600-\u06FF']+", line)
        if not words:
            kept.append(line)
            continue

        remaining = max_words - used
        if remaining <= 0:
            break

        if len(words) <= remaining:
            kept.append(line)
            used += len(words)
            continue

        trimmed = " ".join(words[:remaining]).strip()
        if trimmed:
            if trimmed[-1] not in ".!?؟":
                trimmed += "."
            kept.append(trimmed)
            used += remaining
        break

    result = "\n".join(kept).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    print(f"[Script] Length cap applied: {clean_word_count(result)} words (max {max_words})")
    return result


_TTS_WPM = {"english": 160, "arabic": 185}   # Calibrated: Arabic Nova speed=1.1 ~185 WPM (range 170-190) | English Alloy 1.0 ~160 WPM

# Runtime floors (minutes) by mode — ABSOLUTE MINIMUMS.
# Any long video below 15 minutes is an automatic failure.
_RUNTIME_FLOORS = {
    "full":  15,   # abort if EN script < 15 min — ABSOLUTE RULE
    "fast":  15,   # abort if EN script < 15 min — ABSOLUTE RULE
    "short": 0,    # shorts have no minimum (governed by word targets)
}

# Legacy alias — kept for any callers that still reference _RUNTIME_CAPS.
# Max values are set to 999 (never triggered) to preserve the dict shape
# without silently breaking anything that reads .get("max").
_RUNTIME_CAPS = {
    "full":  {"min": 15, "max": 999},
    "fast":  {"min": 15, "max": 999},
    "short": {"min": 1,  "max": 1.5},
}

# ── Single source of truth for all runtime contracts ────────────────────────
# ALL validators MUST reference get_runtime_contract() — no hardcoded seconds.
# Enforcement uses REAL rendered audio duration (ffprobe), not WPM estimates.
RUNTIME_CONTRACTS: dict[str, dict] = {
    "fast": {
        "english": {"min_minutes": 10.0, "max_minutes": 15.0},
        "arabic":  {"min_minutes": 30.0, "max_minutes": 75.0},
    },
    "full": {
        "english": {"min_minutes": 15.0, "max_minutes": 20.0},
        "arabic":  {"min_minutes": 30.0, "max_minutes": 75.0},
    },
    "animation": {
        "english": {"min_minutes": 12.0, "max_minutes": 18.0},
        "arabic":  {"min_minutes": 35.0, "max_minutes": 60.0},
    },
    "short": {
        "english": {"min_minutes": 1.0,  "max_minutes": 1.5},
        "arabic":  {"min_minutes": 1.0,  "max_minutes": 1.5},
    },
}


def get_runtime_contract(mode: str, language: str = "english") -> dict:
    """Return runtime contract for the given pipeline mode and language."""
    mode_data = RUNTIME_CONTRACTS.get(mode, RUNTIME_CONTRACTS["fast"])
    lang_key  = language.lower()
    # Support language-nested format (new) and flat format (legacy callers)
    first_val = next(iter(mode_data.values()))
    if isinstance(first_val, dict):
        contract = mode_data.get(lang_key, mode_data.get("english", {}))
    else:
        contract = mode_data  # flat fallback (short mode keeps flat shape)
    return {
        "min_minutes": contract["min_minutes"],
        "max_minutes": contract["max_minutes"],
        "min_seconds": contract["min_minutes"] * 60.0,
        "max_seconds": contract["max_minutes"] * 60.0,
    }


def estimate_runtime_minutes(word_count: int, language: str = "english") -> float:
    """Estimate spoken duration in minutes using language-specific WPM."""
    wpm = _TTS_WPM.get(language.lower(), 145)
    return round(word_count / wpm, 2)


def trim_to_runtime_budget(script_text: str, max_minutes: float,
                           language: str = "english") -> str:
    """
    Safety-valve trim — only use when script would genuinely exceed an
    extreme threshold (e.g. > 60 min).  Do NOT call this to enforce a
    "preferred" target; quality and storytelling take priority over runtime.
    Preserves [SECTION:] markers; removes excess content from the end.
    """
    wpm = _TTS_WPM.get(language.lower(), 145)
    max_words = int(max_minutes * wpm)
    current = clean_word_count(script_text)
    if current <= max_words:
        return script_text
    print(f"[Runtime] {estimate_runtime_minutes(current, language):.1f}min estimated "
          f"({current}w) exceeds {max_minutes}min cap — trimming to {max_words}w")
    return _cap_script_max_words(script_text, max_words)


def _trim_plain_text_to_words(text: str, max_words: int) -> str:
    """Trim plain text to at most max_words while preserving original punctuation."""
    import re
    src = (text or "").strip()
    matches = list(re.finditer(r"[A-Za-z0-9\u0600-\u06FF']+", src))
    if len(matches) <= max_words:
        return src

    cut_idx = matches[max_words - 1].end()
    trimmed = src[:cut_idx].rstrip()

    # Prefer ending at the next sentence boundary if it is close.
    tail = src[cut_idx:cut_idx + 140]
    m = re.search(r"[.!?؟]", tail)
    if m:
        trimmed = (src[:cut_idx + m.start() + 1]).rstrip()
    elif trimmed and trimmed[-1] not in ".!?؟":
        trimmed += "."
    return trimmed


_GROQ_RATE_LIMITED_UNTIL:     float = 0.0   # epoch seconds; Groq is skipped until this time
_OPENAI_QUOTA_EXCEEDED_UNTIL: float = 0.0  # epoch seconds; OpenAI is skipped until this time
_GEMINI_QUOTA_EXCEEDED_UNTIL: float = 0.0  # epoch seconds; Gemini is skipped until this time
_CLAUDE_QUOTA_EXCEEDED_UNTIL: float = 0.0  # epoch seconds; Claude is skipped until this time

# Content-refusal signals — short phrases OpenAI returns when it refuses to write
# sensitive/lengthy Arabic content. Checked inside _openai_call so Gemini is tried next.
_AR_REFUSAL_SIGNALS: tuple = (
    "عذراً، لا يمكنني", "عذرًا، لا يمكنني",
    "عذرًا، لا أستطيع", "عذراً، لا أستطيع",
    "لا يمكنني تلبية",  "لا أستطيع تلبية",
    "لا أستطيع المساعدة", "لا يمكنني المساعدة",
    "لا أستطيع أن أكتب", "لا يمكنني أن أكتب",
    "I'm sorry, but I can't", "I'm sorry, I can't",
    "I cannot assist", "I can't assist",
)


def _groq_fallback(prompt: str, max_tokens: int, json_mode: bool,
                   system_prompt: str | None = None) -> str:
    """Groq primary call. Detects 429 immediately and sets session flag to skip Groq."""
    import os
    import time
    global _GROQ_RATE_LIMITED_UNTIL

    if time.time() < _GROQ_RATE_LIMITED_UNTIL:
        _remaining = int(_GROQ_RATE_LIMITED_UNTIL - time.time())
        print(f"[Groq] Rate-limited — retrying in {_remaining}s")
        return ""

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key:
        print("[Script] No Groq key available")
        return ""

    from groq import Groq
    groq_client = Groq(api_key=groq_key)

    if json_mode and "valid JSON" not in prompt:
        prompt = prompt + "\n\nRespond with valid JSON only, no markdown, no explanation."

    max_chars = 3000
    if len(prompt) > max_chars:
        half   = max_chars // 2
        prompt = prompt[:half] + "\n...\n" + prompt[-half:]
        print(f"[Script] Prompt truncated to {max_chars} chars for Groq")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # Only try the primary model; do NOT fall back to a weaker Groq model on rate limit
    for model, model_max in [
        ("llama-3.3-70b-versatile", 2000),
        ("llama-3.1-8b-instant",    1000),
    ]:
        try:
            resp = groq_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=min(max_tokens, model_max),
                temperature=0.7,
            )
            print(f"[Script] Groq {model} success")
            return resp.choices[0].message.content or ""
        except Exception as e:
            err = str(e).lower()
            is_rate_limit = (
                "rate_limit_exceeded" in err
                or "rate limit" in err
                or "429" in err
                or "too many requests" in err
            )
            if is_rate_limit:
                _GROQ_RATE_LIMITED_UNTIL = time.time() + 60
                print(f"[Groq] Rate limit hit — switching to OpenAI fallback (retry in 60s)")
                return ""   # caller will use OpenAI; do NOT try next Groq model
            print(f"[Script] Groq {model} failed: {e}")
            time.sleep(5)

    return ""


def _gemini_call(prompt: str, max_tokens: int,
                 system_prompt: str | None = None) -> str:
    """Gemini 2.0 Flash — tertiary fallback when Groq and OpenAI are both exhausted."""
    import os, time
    import requests as _greq
    global _GEMINI_QUOTA_EXCEEDED_UNTIL

    if time.time() < _GEMINI_QUOTA_EXCEEDED_UNTIL:
        _rem = int(_GEMINI_QUOTA_EXCEEDED_UNTIL - time.time())
        print(f"[Gemini] Quota exceeded — skipping (cooldown {_rem}s remaining)")
        return ""

    api_key = os.getenv('GEMINI_API_KEY', '').strip()
    if not api_key:
        return ""

    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": min(max_tokens, 8192), "temperature": 0.7},
    }
    if system_prompt:
        payload["system_instruction"] = {"parts": [{"text": system_prompt}]}

    try:
        r = _greq.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json=payload,
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            if text:
                print("[Script] Gemini gemini-2.0-flash ✅")
                return text.strip()
            print("[Script] Gemini 200 but empty response")
        elif r.status_code == 429:
            _GEMINI_QUOTA_EXCEEDED_UNTIL = time.time() + 300
            print("[Script] Gemini quota exceeded (retry in 300s)")
        else:
            print(f"[Script] Gemini HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[Script] Gemini failed: {e}")

    return ""


def _ai_script_call(prompt: str, max_tokens: int = 1000,
                    json_mode: bool = False, temperature: float = 0.7,
                    system_prompt: str | None = None,
                    premium: bool = False) -> str:
    """Route script calls by quality tier.

    premium=True  → OpenAI gpt-4o (primary) → Groq → Gemini  — long-form sections
    premium=False → Groq (primary) → OpenAI → Gemini           — cheap helpers
    """
    import requests as _req
    import time
    global _OPENAI_QUOTA_EXCEEDED_UNTIL

    _now = time.time()
    _g_wait_s   = max(0, int(_GROQ_RATE_LIMITED_UNTIL    - _now))
    _o_wait_s   = max(0, int(_OPENAI_QUOTA_EXCEEDED_UNTIL - _now))
    _gem_wait_s = max(0, int(_GEMINI_QUOTA_EXCEEDED_UNTIL - _now))
    print(
        f"[Provider Status] Groq={'✅' if _g_wait_s == 0 else f'❌ {_g_wait_s}s'} | "
        f"OpenAI={'✅' if _o_wait_s == 0 else f'❌ {_o_wait_s}s'} | "
        f"Gemini={'✅' if _gem_wait_s == 0 else f'❌ {_gem_wait_s}s'}"
    )

    def _openai_call(model: str) -> str:
        """Single OpenAI call with strict per-status-code error handling."""
        global _OPENAI_QUOTA_EXCEEDED_UNTIL
        api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if not api_key:
            print('[Script] OpenAI: OPENAI_API_KEY not configured in environment')
            return ''
        if time.time() < _OPENAI_QUOTA_EXCEEDED_UNTIL:
            _rem = int(_OPENAI_QUOTA_EXCEEDED_UNTIL - time.time())
            print(f'[Script] OpenAI: cooldown {_rem}s remaining — skipping')
            return ''
        msgs = []
        if system_prompt:
            msgs.append({'role': 'system', 'content': system_prompt})
        msgs.append({'role': 'user', 'content': prompt})
        try:
            _t0 = time.time()
            r = _req.post(
                'https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': model, 'messages': msgs,
                      'max_tokens': max_tokens, 'temperature': temperature},
                timeout=120,
            )
            _elapsed = time.time() - _t0
            if r.status_code == 200:
                try:
                    content = r.json()['choices'][0]['message']['content']
                    if content and content.strip():
                        _stripped = content.strip()
                        # Detect content-policy refusals before returning — treat them as
                        # failures so the caller falls through to Groq/Gemini.
                        if len(_stripped) < 300 and any(sig in _stripped for sig in _AR_REFUSAL_SIGNALS):
                            print(
                                f'[Script] OpenAI {model} CONTENT REFUSAL ({len(_stripped)}chars) — '
                                f'skipping to next provider'
                            )
                            return ''
                        print(f'[Script] OpenAI {model} ✅ ({len(content)}chars, {_elapsed:.1f}s)')
                        return _stripped
                    print(f'[Script] OpenAI {model}: 200 OK but empty content — skipping')
                except (KeyError, IndexError, TypeError) as _pe:
                    print(f'[Script] OpenAI {model}: 200 malformed response ({_pe}): {r.text[:200]}')
            elif r.status_code == 401:
                print(
                    f'[Script] OpenAI 401 UNAUTHORIZED — verify OPENAI_API_KEY in GitHub Secrets '
                    f'(wrong key, revoked, or wrong project). Body: {r.text[:300]}'
                )
            elif r.status_code == 403:
                print(
                    f'[Script] OpenAI 403 FORBIDDEN — check OPENAI_ORG_ID / OPENAI_PROJECT_ID. '
                    f'Body: {r.text[:300]}'
                )
            elif r.status_code == 429:
                body = r.text[:400]
                retry_after = r.headers.get('Retry-After', 'not-set')
                is_billing = any(kw in body for kw in (
                    'insufficient_quota', 'exceeded your current quota', 'billing',
                ))
                cooldown = 3600 if is_billing else 300
                label = 'BILLING QUOTA EXHAUSTED' if is_billing else 'RATE LIMIT (RPM/TPM)'
                _OPENAI_QUOTA_EXCEEDED_UNTIL = time.time() + cooldown
                print(
                    f'[Script] OpenAI 429 {label} — cooldown {cooldown}s. '
                    f'Retry-After: {retry_after}. Body: {body}'
                )
            elif r.status_code >= 500:
                print(f'[Script] OpenAI {model} HTTP {r.status_code} SERVER ERROR. Body: {r.text[:300]}')
            else:
                print(f'[Script] OpenAI {model} HTTP {r.status_code}. Body: {r.text[:300]}')
        except Exception as _e:
            print(f'[Script] OpenAI {model} exception: {_e}')
        return ''

    if premium:
        # ── Premium path: Groq → OpenAI gpt-4o (fallback) → Gemini ──────────
        # OpenAI is reserved as fallback only — Groq handles most content well.
        result = _groq_fallback(prompt, max_tokens, json_mode, system_prompt=system_prompt)
        if result:
            return result
        print('[Script] Groq failed (premium path) — OpenAI gpt-4o fallback')
        result = _openai_call('gpt-4o')
        if result:
            return result
        result = _gemini_call(prompt, max_tokens, system_prompt=system_prompt)
        if result:
            return result
        print('[Script] ❌ All providers exhausted (premium path) — returning empty')
        return ''

    # ── Standard path: Groq primary → OpenAI mini fallback → Gemini ─────────
    result = _groq_fallback(prompt, max_tokens, json_mode, system_prompt=system_prompt)
    if result:
        return result
    result = _openai_call('gpt-4o-mini')
    if result:
        return result
    result = _gemini_call(prompt, max_tokens, system_prompt=system_prompt)
    if result:
        return result
    print('[Script] ❌ All providers exhausted (standard path) — returning empty')
    return ''


def expand_section(existing_text: str, missing_words: int,
                   system_prompt: str | None = None) -> str:
    """
    Append targeted new content to an under-length section.

    Cheaper than a full section regeneration: sends only the existing text +
    a short instruction, requests ~missing_words of new prose, and appends it.
    Replaces the three-attempt retry + continuation loop for undersized sections.

    Returns the original text unchanged if the expansion produces nothing useful.
    """
    target = max(missing_words, 80)
    prompt = (
        f"Continue the following cinematic storytelling section with approximately {target} new words.\n\n"
        f"MANDATORY — the continuation MUST add NEW STORY MOVEMENT:\n"
        f"- A new scene, investigation beat, or narrative turn NOT already in the section\n"
        f"- Move through: event → reaction → discovery → escalation → consequence\n"
        f"- Every 50-80 words must contain a story beat: clue, twist, reveal, consequence, or turning point\n"
        f"- At least one new VERIFIABLE FACT per 50 words: real name, date, location, or documented event\n\n"
        f"FORBIDDEN — the continuation must NOT:\n"
        f"- Repeat, restate, or paraphrase anything already written\n"
        f"- Inflate with repeated biographical facts or psychological analysis\n"
        f"- Add atmospheric filler: 'The fear grew', 'Silence fell', 'The shadows deepened'\n"
        f"- Summarise what was just said — only NEW story scenes count\n\n"
        f"Write ONLY the new continuation text — do not include the original section.\n\n"
        f"EXISTING SECTION:\n{existing_text}"
    )
    try:
        continuation = _ai_script_call(
            prompt,
            max_tokens=min(target * 2 + 100, 900),
            temperature=0.75,
            system_prompt=system_prompt or _SCRIPT_SYSTEM_PROMPT,
            premium=True,
        )
        if not continuation or clean_word_count(continuation) < 30:
            print("[Script] expand_section: expansion too short — keeping original")
            return existing_text
        # Strip filler from the expansion before merging
        try:
            from agents.script_quality import remove_filler_phrases
            continuation = remove_filler_phrases(continuation)
        except Exception:
            pass
        if not continuation or clean_word_count(continuation) < 20:
            print("[Script] expand_section: expansion was pure filler — keeping original")
            return existing_text
        result  = existing_text.rstrip() + "\n\n" + continuation.strip()
        added   = clean_word_count(continuation)
        total   = clean_word_count(result)
        print(f"[Script] expand_section: +{added} words added (total {total})")
        return result
    except Exception as e:
        print(f"[Script] expand_section failed: {e} — keeping original")
        return existing_text


def expand_script_runtime(script_text: str, missing_words: int,
                          topic: str = "") -> str:
    """
    Lightweight expansion for scripts that are slightly under the word-count floor.

    Strategy:
    1. Parse [SECTION: ...] markers; fall back to paragraph chunks if absent.
    2. Sort sections by word count ascending — expand the shortest first.
    3. Distribute missing_words across the bottom 2 sections using expand_section().
    4. Skip sections already at or above average length.
    5. Reconstruct and return the full script with expanded sections.

    Never regenerates the full script. Never redoes research, hooks, or images.
    Returns original unchanged if expansion produces no net gain.
    """
    if missing_words <= 0:
        return script_text

    # Use the same section parser as translate_script
    sections = _split_english_sectioned_script(script_text)

    if not sections:
        # No markers — expand the whole text as a single block
        print(f"[FAST] No section markers — single-block expansion (+{missing_words} words needed)")
        expanded = expand_section(script_text, missing_words)
        added = clean_word_count(expanded) - clean_word_count(script_text)
        if added > 0:
            print(f"[FAST] Single-block expansion: +{added} words")
        return expanded

    # Word count per section
    section_wcs = [(name, body, clean_word_count(body)) for name, body in sections]
    total_wc    = sum(wc for _, _, wc in section_wcs)
    avg_wc      = total_wc / max(len(section_wcs), 1)

    # Expand at most 2 shortest sections (enough to close a small gap cheaply)
    sorted_by_len   = sorted(section_wcs, key=lambda x: x[2])
    n_to_expand     = min(2, len(sorted_by_len))
    per_section     = max(80, missing_words // n_to_expand + 30)

    topic_hint    = f"This is about: {topic}. " if topic else ""
    doc_sys_prompt = (
        f"You are a cinematic crime storyteller. {topic_hint}"
        "Add NEW STORY MOVEMENT to the section — new scenes, investigation beats, or narrative turns. "
        "Runtime must come from STORY PROGRESSION, not repeated facts or inflated descriptions. "
        "Every added 50-80 words must contain a story beat: clue, twist, reveal, consequence, or turning point. "
        "Do NOT repeat biographical facts, psychological analysis, or atmosphere already written. "
        "Do NOT write sentences like 'The fear grew' or 'Silence fell' — these are banned filler."
    )

    expanded_map: dict[str, str] = {}
    for name, body, wc in sorted_by_len[:n_to_expand]:
        if wc >= avg_wc * 1.2:
            # Section already well above average — no need to touch it
            continue
        expanded = expand_section(body, per_section, system_prompt=doc_sys_prompt)
        if clean_word_count(expanded) > wc:
            expanded_map[name] = expanded

    if not expanded_map:
        return script_text

    # Reconstruct script preserving section order and markers
    result_parts: list[str] = []
    for name, body in sections:
        new_body = expanded_map.get(name, body)
        result_parts.append(f"[SECTION: {name}]\n{new_body.strip()}")
    return "\n\n".join(result_parts)


def compress_english_script(script_text: str, target_words: int, topic: str = "") -> str:
    """
    Trim English script to target_words when rendered audio exceeds the max runtime.
    Uses proportional section trimming — preserves section markers and story structure.
    Only compresses narration text; never touches metadata or image markers.
    """
    current_wc = clean_word_count(script_text)
    if current_wc <= target_words:
        return script_text
    print(f"[EN COMPRESS] {current_wc}w → target {target_words}w (removing ~{current_wc - target_words}w)")
    compressed = _cap_script_max_words(script_text, target_words)
    new_wc = clean_word_count(compressed)
    print(f"[EN COMPRESS] Result: {new_wc}w (removed {current_wc - new_wc}w)")
    return compressed


# ============================================================
# SCRIPT SCORING SYSTEM
# ============================================================

_SCORING_PROMPT = """You are a YouTube retention expert.

Evaluate this script for short-form video performance.

Score (0-10):
- Hook strength
- Curiosity gap
- Clarity
- Emotional pull
- Retention flow

Return EXACT format:

SCORE: X/10

IF score < 7:
IMPROVED:
[Rewrite the script to be more engaging, natural spoken voice, no labels]

SCRIPT:
{{SCRIPT}}"""


def _extract_score(text: str) -> int:
    import re
    m = re.search(r"SCORE:\s*(\d+)", text)
    return int(m.group(1)) if m else 10


def _extract_improved(text: str) -> str:
    if "IMPROVED:" not in text:
        return ""
    return text.split("IMPROVED:")[-1].strip()


def evaluate_and_fix_script(script: str) -> str:
    try:
        sentences = [s.strip() for s in script.split(".") if s.strip()]
        hook = ". ".join(sentences[:3])
        prompt = _SCORING_PROMPT.replace("{{SCRIPT}}", hook)
        result = _ai_script_call(prompt, max_tokens=800, temperature=0.7, premium=False)
        score = _extract_score(result)
        print(f"[Script Score] {score}/10")
        if score >= 7:
            return script
        improved_hook = _extract_improved(result)
        if improved_hook:
            print("[Script] Using improved hook")
            rest = ". ".join(sentences[3:])
            if rest:
                return improved_hook.rstrip(".") + ". " + rest
            return improved_hook
    except Exception as e:
        print(f"[Script Score] Failed: {e}")
    return script


# ============================================================
# MULTI-HOOK GENERATION + SCORING SYSTEM
# ============================================================

_HOOK_GEN_PROMPT = """You are a true crime YouTube scriptwriter. Write 3 highly specific, shocking opening hooks for a documentary short.

MANDATORY — every hook MUST contain at least ONE of:
- A real person's name (e.g. "Ed Kemper told the FBI...", "Pablo Escobar paid...")
- A concrete crime detail (a specific act, weapon, date, location, or number)
- A shocking contradiction or disturbing fact that names what actually happened

BANNED — reject any hook that contains:
- Generic phrases: "what drove", "a mother's fear", "someone", "a killer", "one man", "one woman", "a person"
- Vague openers: "In an era...", "This is the story of...", "You won't believe...", "Once upon a time..."
- Abstract questions with no named subject

Write exactly 3 hooks:
HOOK 1 (Revelation): Reveal a specific fact or decision that changed everything. Name the person or event.
HOOK 2 (Contradiction): Something that should not have happened but did. Name the act or the person who did it.
HOOK 3 (Consequence): One named person, one specific result. Make the viewer feel dread from a concrete detail.

RULES:
- 1-2 sentences only. Never more.
- Maximum 16 words per sentence. Spoken aloud — natural narrator voice.
- Each hook must feel like a headline exposing something disturbing or unexpected.
- No two hooks may share the same framing or subject.

Return EXACTLY this format (no extra text):
HOOK 1: [revelation hook here]
HOOK 2: [contradiction hook here]
HOOK 3: [consequence hook here]

SCRIPT EXCERPT:
{script_excerpt}"""

_HOOK_SCORE_PROMPT = """Score this true crime documentary hook for YouTube retention.

Score 0-10 based on:
- Curiosity gap: does it force the viewer to stay and find out? (0-3 points)
- Contradiction / rule-breaking: does it violate expectations or reveal a secret? (0-3 points)
- Emotional intensity: does it create immediate tension, dread, or shock? (0-2 points)
- Clarity: is it instantly clear something serious happened? (0-2 points)

BONUS: Award +1 if the hook contains secrecy, betrayal, or a hidden truth.
PENALTY: Deduct 2 if the hook sounds like a generic documentary intro.

Return EXACTLY:
SCORE: X/10

HOOK:
{hook}"""


def _parse_hooks(text: str) -> list[str]:
    import re
    hooks = []
    for m in re.finditer(r"HOOK\s*\d+:\s*(.+?)(?=HOOK\s*\d+:|$)", text, re.IGNORECASE | re.DOTALL):
        h = m.group(1).strip()
        if h:
            hooks.append(h)
    return hooks[:3]


def _score_hook(hook: str) -> int:
    try:
        prompt = _HOOK_SCORE_PROMPT.replace("{hook}", hook)
        try:
            from agents.ai_cache import cached_ai_call as _cac
            result = _cac(
                prompt, "groq", "hook_score",
                fn=lambda: _ai_script_call(prompt, max_tokens=50, temperature=0.3, premium=False),
                ttl_days=30,
            )
        except ImportError:
            result = _ai_script_call(prompt, max_tokens=50, temperature=0.3, premium=False)
        return _extract_score(result)
    except Exception:
        return 0


_HOOK_GENERIC_PHRASES = [
    "what drove", "a mother's fear", "someone", "a killer", "one man", "one woman",
    "a person", "in an era", "this is the story", "you won't believe", "once upon",
    "throughout history", "it all began", "nobody knew", "little did", "the world",
    "dark secret", "shocking truth", "untold story",
]


def _hook_is_generic(hook: str) -> bool:
    """Return True if hook contains banned generic phrases or lacks specifics."""
    if not hook:
        return True
    h = hook.lower()
    return any(phrase in h for phrase in _HOOK_GENERIC_PHRASES)


def _validate_hook_on_topic(hook: str, topic: str, series: str = "") -> bool:
    """Return True if hook is clearly about the active topic, not a random entity.

    For multi-word topics, requires ≥ 2 keyword hits (topic + optional series).
    Rejects hooks that name blocked entities unrelated to the active topic.
    """
    if not hook:
        return False
    if not topic:
        return True
    h_lower = hook.lower()

    # Build keyword pool: topic words + series words (all > 3 chars)
    topic_words  = [w for w in topic.lower().split() if len(w) > 3]
    series_words = [w for w in (series or "").lower().split() if len(w) > 3]
    all_kw       = topic_words + series_words

    if all_kw:
        matches = sum(1 for w in all_kw if w in h_lower)
        # Require at least 2 matches for multi-word topics; 1 for single-word
        min_required = 2 if len(topic_words) >= 2 else 1
        if matches < min_required:
            print(f"[Hook] REJECTED (only {matches}/{min_required} keyword(s) matched "
                  f"from '{topic}'): {hook[:70]}")
            return False

    # Reject hooks containing blocked criminal names not related to this topic
    try:
        from agents.entity_guard import build_active_entity
        entity  = build_active_entity(topic)
        blocked = entity.get("blocked_entities", [])
        for name in blocked:
            if len(name) > 4 and name.lower() in h_lower:
                print(f"[Hook] REJECTED (forbidden entity '{name}'): {hook[:70]}")
                return False
    except Exception:
        pass

    return True


def pick_best_hook(script: str, topic: str = "", series: str = "") -> str:
    try:
        import re as _re
        excerpt = _re.sub(r'\[SECTION:[^\]]*\]', '', script).strip()[:500]

        # Inject topic/series grounding so the hook generator can't hallucinate
        # random crimes that have nothing to do with the active subject
        _topic_lock = ""
        if topic or series:
            _subject = topic or series
            _topic_lock = (
                f"TOPIC LOCK — this hook is ONLY about:\n"
                f"  Real person / subject: {topic or '(see script)'}\n"
                f"  Related show / film:   {series or '(see script)'}\n"
                f"Every hook MUST directly reference {_subject}.\n"
                f"Do NOT reference any other crime, criminal, or unrelated person.\n\n"
            )
        base_prompt = _topic_lock + _HOOK_GEN_PROMPT.replace("{script_excerpt}", excerpt)

        best_hook, best_score = "", 0
        final_attempt = 1

        # Max 2 rounds — stop early if score >= 8
        for attempt in range(1, 3):
            final_attempt = attempt
            if attempt == 1:
                raw = _ai_script_call(base_prompt, max_tokens=350, temperature=0.85, premium=False)
            else:
                _feedback_prompt = (
                    base_prompt
                    + f"\n\nPREVIOUS BEST HOOK scored {best_score}/10:\n{best_hook}\n\n"
                    + f"Write 3 completely different hooks that score higher. "
                    + f"Each MUST name '{topic or series}' directly. "
                    + "Include a real name (person or place) or a concrete crime detail. "
                    + "Make them more disturbing and more specific. "
                    + "Do NOT use: 'what drove', 'someone', 'a killer', 'one man', 'a person'."
                )
                raw = _ai_script_call(_feedback_prompt, max_tokens=350, temperature=0.9, premium=False)

            hooks = _parse_hooks(raw)
            if not hooks:
                print(f"[Hook] Attempt {attempt}: no hooks parsed")
                continue

            print(f"[Hook] Attempt {attempt}: {len(hooks)} candidates")
            for h in hooks:
                # Hard filter: generic phrasing + off-topic entity bleed
                if _hook_is_generic(h):
                    print(f"[Hook] REJECTED (generic): {h[:70]}")
                    continue
                if topic and not _validate_hook_on_topic(h, topic, series=series):
                    continue
                s = _score_hook(h)
                print(f"[Hook] {s}/10: {h[:70]}")
                if s > best_score:
                    best_score, best_hook = s, h

            print(f"[Hook] Attempt {attempt} best so far: {best_score}/10")
            if best_score >= 8:
                print(f"[Hook] Score {best_score}/10 reached — stopping after {attempt} attempt(s)")
                break

        if not best_hook:
            print("[Hook] No hooks passed filter — keeping original")
            return script

        print(f"[Hook] Final: {best_score}/10 after {final_attempt} attempt(s)")

        # Replace only first 2 sentences; preserve all [SECTION:] markers and rest of script
        lines = script.splitlines()
        rebuilt = []
        replaced = False
        for line in lines:
            if not replaced:
                stripped = line.strip()
                if stripped.startswith("[SECTION:") or not stripped:
                    rebuilt.append(line)
                    continue
                sentences = [s.strip() for s in stripped.split(".") if s.strip()]
                rest = ". ".join(sentences[2:])
                rebuilt.append(best_hook + (". " + rest + "." if rest else ""))
                replaced = True
            else:
                rebuilt.append(line)
        return "\n".join(rebuilt)
    except Exception as e:
        print(f"[Hook] Failed: {e}")
        return script


# ============================================================
# SCRIPT UPGRADE SYSTEM (RETENTION ENHANCEMENT)
# ============================================================

_UPGRADE_PROMPT = """You are a true crime documentary script editor. Make targeted line-level edits only.

PRESERVE:
- Every fact, name, date, and number — unchanged
- The original sequence of events — do not reorder
- Core wording where it is already strong — do not touch it
- All [SECTION:] markers — exactly as written
- Overall script length — do not add or remove large blocks

MAKE ONLY THESE THREE TYPES OF EDITS:

1. SENTENCE STRUCTURE — fix weak openings only:
   - If the first sentence of the script starts with "In an era", "This is the story of", "He was born", or "Throughout history" — rewrite that sentence only to open with tension or action
   - Do not touch sentences that already open with tension or specificity

2. EXPLANATION TO ACTION — convert flat "state" sentences only:
   - If a sentence says "He was [adjective]" or "They were known for [noun]" with no action — rewrite it as what they DID
   - Only change sentences that are purely descriptive with no verb of action
   - Keep all surrounding sentences unchanged

3. MICRO-TENSION — add one tension beat per flat paragraph only:
   - If a paragraph has 3+ sentences with no question, contradiction, or escalation — add a single short sentence at the end that raises a question or reveals an implication
   - Do not restructure the paragraph

Return the FULL script with only these targeted edits applied. Do not rewrite, summarize, or restructure.

ORIGINAL SCRIPT:
{script}"""


_UPGRADE_ARABIC_PROMPT = """أنت محرر لغوي عربي متخصص، وكاتب نصوص وثائقية سينمائية، ومخرج تعليق صوتي احترافي.

مهمتك الوحيدة: تحويل النص التالي إلى أفضل نسخة ممكنة للسرد الصوتي الوثائقي وإنتاج الفيديو.

قاعدة بنيوية إلزامية: احتفظ بجميع علامات [SECTION:] كما هي تمامًا — لا تحذفها ولا تغيّرها أبدًا.

━━━━━━━━━━━━━━━
[1] التصحيح اللغوي العربي
━━━━━━━━━━━━━━━

قم بتصحيح:
- الأخطاء النحوية والصرفية
- الأخطاء الإملائية
- علامات الترقيم وضبطها
- الصياغة الركيكة أو غير الطبيعية
- بناء الجمل الضعيف

واجعل اللغة:
- عربية فصحى حديثة (MSA)
- احترافية ووثائقية
- طبيعية وسهلة الاستماع
- محافظة على جميع المعلومات والحقائق

━━━━━━━━━━━━━━━
[2] تحسين السرد الوثائقي السينمائي
━━━━━━━━━━━━━━━

أعد كتابة الجمل لتصبح طبيعية عند الاستماع إليها بصوت عالٍ.
حسّن الإيقاع والتدفق السردي.
أنشئ انتقالات سلسة بين الفقرات.
أضف التأثير الدرامي حيثما يناسب المحتوى.
حافظ على النبرة الوثائقية الاحترافية.

النمط المستهدف: Netflix Crime Documentary — BBC Arabic — Al Jazeera Investigative.

محظورات السرد — إلزامية:
- محظور تامًا: "يتم" بأي صيغة — الفاعل يجب أن يكون صريحًا في كل جملة
  BAD: "يتم القبض عليه" — GOOD: "ألقى المحققون القبض عليه"
  BAD: "يتم التحقيق في القضية" — GOOD: "فتح المحققون تحقيقًا في القضية"
- محظور تامًا: بدء الجملة بـ "كانت" أو "كان" إذا كانت الجملتان المتتاليتان تبدآن بنفس الكلمة
  النمط المحظور: كانت X. كانت Y. كانت Z. — فشل فوري، أعد صياغة الجمل بفاعل صريح
- لا تفتح بعبارات جوفاء: "هذه قصة..."، "في عالم الجريمة..."، "عبر التاريخ..."

━━━━━━━━━━━━━━━
[3] تحسين النص للـ TTS — قواعد صارمة
━━━━━━━━━━━━━━━

حد الجملة: لا تتجاوز أي جملة 25 كلمة منطوقة — إذا تجاوزت، قسّمها إلى جملتين.
الإيقاع: مزج بين الجمل القصيرة (5-10 كلمات) والمتوسطة (15-22 كلمة).
الوضوح: أعد صياغة الجمل الملتوية إلى جمل مباشرة وواضحة.
التدفق: لا تضع عبارتين فاصلتين متتاليتين بدون جملة سردية بينهما.

━━━━━━━━━━━━━━━
[4] تحويل الأرقام إلى صيغة منطوقة — إلزامي
━━━━━━━━━━━━━━━

حوّل جميع الأرقام إلى كلمات منطوقة باللغة العربية. لا يجوز أن يبقى أي رقم بالأرقام.

أمثلة:
- 2007  → عام ألفين وسبعة
- 1989  → عام ألفٍ وتسعمائةٍ وتسعةٍ وثمانين
- 1984  → عام ألفٍ وتسعمائةٍ وأربعةٍ وثمانين
- 2022  → عام ألفين واثنين وعشرين
- 15    → خمسة عشر (أو "الخامس عشر" إذا كان يومًا)
- 25%   → خمسة وعشرون بالمائة
- $10M  → عشرة ملايين دولار
- 4.5B  → أربعة مليارات ونصف
- 3 فبراير → الثالث من فبراير

━━━━━━━━━━━━━━━
[5] تحسين نطق الأسماء الأجنبية
━━━━━━━━━━━━━━━

تأكد من الكتابة الصوتية العربية الصحيحة للأسماء الأجنبية.
اجعل النطق واضحًا لأنظمة TTS.

أمثلة:
- Jeffrey Epstein    → جيفري إبستين
- Richard Ramirez   → ريتشارد راميريز
- Walgreens         → وولغرينز
- Stanford          → ستانفورد
- Wagner Group      → مجموعة فاغنر

━━━━━━━━━━━━━━━
[6] التشكيل الجزئي الذكي
━━━━━━━━━━━━━━━

أضف تشكيلًا جزئيًا فقط عند الضرورة لمنع أخطاء النطق.

ركّز على:
- أسماء الأشخاص والأماكن
- الكلمات متعددة النطق: عَلَم / عِلْم — مَلِك / مَلَك
- الأفعال الغامضة التي قد يخطئ فيها الـ TTS
- المصطلحات العسكرية والتقنية الحساسة

أمثلة: مُحَمَّد — الدَّعم السَّريع — حُمَيْدتي — رَفيع المستوى

لا تستخدم تشكيلًا كاملًا — فقط الكلمات التي تحتاج إليه فعلًا.

━━━━━━━━━━━━━━━
[7] تحسين الإيقاع والتوقيت
━━━━━━━━━━━━━━━

استخدم علامات الترقيم كأدوات إخراج صوتي:
- ، (فاصلة عربية) = وقفة طبيعية قصيرة
- .  (نقطة)        = وقفة كاملة بين الجمل
- ... (نقاط)       = وقفة درامية — أبطئ قبلها
- —  (شرطة طويلة)  = وقفة متوسطة مع تأكيد
- فراغ بين الفقرات = انتقال مشهدي

السرد النهائي يجب أن يكون: هادئ — موثوق — سينمائي.

━━━━━━━━━━━━━━━
[8] قواعد الإخراج النهائي
━━━━━━━━━━━━━━━

- احتفظ بجميع الحقائق والأسماء والتواريخ كما هي
- لا تختصر المحتوى ولا تحذف فقرات
- لا تضف معلومات خيالية أو تفسيرات خارجية
- لا تغيّر الترتيب الزمني للأحداث
- لا تكتب ملاحظات أو تعليقات خارج النص
- الناتج النهائي جاهز مباشرة للـ TTS بدون أي تحرير إضافي

النص:
{script}"""


def deduplicate_script_facts(script: str) -> str:
    """Remove exact-duplicate sentences that repeat the same fact.
    A sentence is a duplicate when its normalized form (punctuation stripped,
    lowercased, whitespace collapsed) matches one already seen earlier in the
    script.  Only exact-normalized duplicates are removed — near-similar but
    distinct sentences are kept.  [SECTION:] markers and empty lines are always
    preserved."""
    import re as _re

    if not script or not script.strip():
        return script

    lines = script.splitlines()
    seen: set[str] = set()
    kept: list[str] = []
    removed = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("[SECTION:"):
            kept.append(line)
            continue

        # Split on sentence-ending punctuation (Arabic + Latin)
        parts = _re.split(r'(?<=[.!?؟])\s+', stripped)
        kept_parts: list[str] = []
        for sent in parts:
            sent = sent.strip()
            if not sent or len(sent) < 20:
                kept_parts.append(sent)
                continue
            # Normalize: keep Arabic + Latin alphanum only, lowercase, collapse whitespace
            norm = _re.sub(r'[^؀-ۿa-z0-9\s]', '', sent.lower())
            norm = _re.sub(r'\s+', ' ', norm).strip()
            if not norm:
                kept_parts.append(sent)
                continue
            if norm in seen:
                removed += 1
                print(f"[Dedup] Duplicate removed: {sent[:70]}")
            else:
                seen.add(norm)
                kept_parts.append(sent)

        rebuilt = " ".join(p for p in kept_parts if p)
        if rebuilt.strip():
            kept.append(rebuilt)

    result = "\n".join(kept).strip()
    if removed:
        print(f"[Dedup] {removed} duplicate sentence(s) removed from script")
    return result


def upgrade_script_for_retention(script: str) -> str:
    try:
        prompt = _UPGRADE_PROMPT.replace("{script}", script)
        improved = _ai_script_call(prompt, max_tokens=4000, temperature=0.75, premium=True)
        if not improved or len(improved.split()) < 200:
            print("[Upgrade] Result too short — keeping original")
            return script
        import re as _re
        orig_sections = _re.findall(r'\[SECTION:[^\]]+\]', script)
        new_sections  = _re.findall(r'\[SECTION:[^\]]+\]', improved)
        if orig_sections and not new_sections:
            print("[Upgrade] Section markers lost — keeping original")
            return script
        _upgraded_wc = clean_word_count(improved)
        _original_wc = clean_word_count(script)
        if _upgraded_wc < _original_wc * 0.90:
            print(f"[Upgrade] English rejected — compression: {_upgraded_wc}w < {_original_wc}w×90%")
            return script
        improved = deduplicate_script_facts(improved)
        print(f"[Upgrade] English script upgraded ({_upgraded_wc} words)")
        return improved
    except Exception as e:
        print(f"[Upgrade] Failed: {e}")
        return script


def upgrade_arabic_script(script: str) -> str:
    _orig_wc  = clean_word_count(script)
    _orig_min = _orig_wc / _TTS_WPM["arabic"]
    try:
        prompt = _UPGRADE_ARABIC_PROMPT.replace("{script}", script)
        improved = _ai_script_call(prompt, max_tokens=4000, temperature=0.7, premium=True)
        if not improved or len(improved.split()) < 100:
            print("[Upgrade] Arabic result too short — keeping original")
            return script
        import re as _re
        orig_sections = _re.findall(r'\[SECTION:[^\]]+\]', script)
        new_sections  = _re.findall(r'\[SECTION:[^\]]+\]', improved)
        if orig_sections and not new_sections:
            print("[Upgrade] Arabic section markers lost — keeping original")
            return script
        _new_wc       = clean_word_count(improved)
        _new_min      = _new_wc / _TTS_WPM["arabic"]
        _contract_min = get_runtime_contract("fast")["min_minutes"]
        if _new_wc < _orig_wc * 0.70:
            print(
                f"[RUNTIME GUARD] Arabic upgrade rejected: {_new_wc}w < {_orig_wc}w×70% "
                f"({_new_min:.1f}min vs {_orig_min:.1f}min) — keeping original"
            )
            return script
        if _new_min < _contract_min:
            print(
                f"[RUNTIME GUARD] Arabic upgrade rejected: estimated {_new_min:.1f}min < "
                f"{_contract_min:.0f}min contract minimum — keeping original"
            )
            return script
        improved = deduplicate_script_facts(improved)
        print(f"[Upgrade] Arabic script upgraded ({_orig_wc}w → {_new_wc}w | {_orig_min:.1f}min → {_new_min:.1f}min)")
        return improved
    except Exception as e:
        print(f"[Upgrade] Arabic failed: {e}")
        return script


_mishkal_vocalizer = None
_mishkal_available = None


def apply_mishkal_tashkeel(script: str) -> str:
    """Add Arabic diacritics (tashkeel) via Mishkal. Preserves [SECTION:] markers.
    Silent no-op if mishkal is not installed."""
    global _mishkal_vocalizer, _mishkal_available

    if _mishkal_available is False:
        return script

    try:
        if _mishkal_vocalizer is None:
            import mishkal.tashkeel as _msh
            _mishkal_vocalizer = _msh.TashkeelClass()
            _mishkal_available = True
    except ImportError:
        _mishkal_available = False
        print("[Mishkal] Not installed — skipping tashkeel (pip install mishkal to enable)")
        return script
    except Exception as e:
        _mishkal_available = False
        print(f"[Mishkal] Init failed: {e} — skipping")
        return script

    import re as _re_msh

    # Arabic diacritical marks (harakat)
    _HARAKAAT = 'ًٌٍَُِّْ'

    def _smart_tashkeel_block(block: str) -> str:
        """Apply tashkeel to a block, word by word.
        - Skips words already diacritized (have harakat)
        - Skips long words (>8 Arabic chars) — likely proper nouns / compounds
        - Skips non-Arabic tokens (numbers, Latin, punctuation)
        Safety: if mishkal expands the block by >35%, return original.
        """
        if not block.strip():
            return block
        words = block.split(' ')
        result_words = []
        _AR_RE = _re_msh.compile(r'^[؀-ۿ]+$')
        needs_tashkeel = []
        for i, w in enumerate(words):
            arabic_only = _AR_RE.match(w)
            already_diacritized = any(c in w for c in _HARAKAAT)
            long_word = len(w) > 8  # proper nouns / compound words
            if arabic_only and not already_diacritized and not long_word:
                needs_tashkeel.append((i, w))
            result_words.append(w)
        if not needs_tashkeel:
            return block
        # Batch: apply tashkeel to only the candidate words
        for idx, word in needs_tashkeel:
            try:
                vocalized = _mishkal_vocalizer.tashkeel(word)
                # Reject if output grew >50% per word (over-tashkeel signal)
                if len(vocalized) <= len(word) * 1.5 + 3:
                    result_words[idx] = vocalized
            except Exception:
                pass
        rebuilt = ' '.join(result_words)
        # Block-level safety: if result > 30% larger, return original
        if len(rebuilt) > len(block) * 1.30:
            return block
        return rebuilt

    parts = _re_msh.split(r'(\[SECTION:[^\]]*\])', script)
    out = []
    for part in parts:
        if _re_msh.match(r'\[SECTION:[^\]]*\]', part):
            out.append(part)
        elif part.strip():
            out.append(_smart_tashkeel_block(part))
        else:
            out.append(part)

    result = "".join(out)
    print(f"[Mishkal] Smart tashkeel applied ({len(script)} → {len(result)} chars)")
    return result


title_format = "Dark Crime Decoded: {person} & {series} — {curiosity_hook}"

PERSON_TO_SERIES: dict[str, tuple[str, str]] = {
    "pablo escobar":   ("Narcos",                "Series"),
    "escobar":         ("Narcos",                "Series"),
    "al capone":       ("Boardwalk Empire",       "Series"),
    "capone":          ("Boardwalk Empire",       "Series"),
    "jeffrey dahmer":  ("Monster",               "Series"),
    "dahmer":          ("Monster",               "Series"),
    "el chapo":        ("Narcos Mexico",          "Series"),
    "griselda blanco": ("Griselda",              "Series"),
    "jordan belfort":  ("Wolf of Wall Street",   "Movie"),
    "john gotti":      ("Gotti",                 "Movie"),
    "dennis rader":    ("BTK Documentary (A&E)", "Documentary"),
    "btk killer":      ("BTK Documentary (A&E)", "Documentary"),
    "btk":             ("BTK Documentary (A&E)", "Documentary"),
    "ted bundy":       ("Extremely Wicked",      "Movie"),
    "ed gein":         ("Psycho",                "Movie"),
    "lucky luciano":   ("The Godfather",         "Movie"),
    "frank lucas":     ("American Gangster",     "Movie"),
    "henry hill":      ("Goodfellas",            "Movie"),
    "whitey bulger":   ("Black Mass",            "Movie"),
    "dexter morgan":   ("Dexter",                "Series"),
    "dexter":          ("Dexter",                "Series"),
    "night stalker":   ("Night Stalker",         "Series"),
    "richard ramirez": ("Night Stalker",         "Series"),
    "charles manson":  ("Helter Skelter",         "Movie"),
    "manson":          ("Helter Skelter",         "Movie"),
    "amanda knox":     ("Stillwater",            "Movie"),
    "leopold":         ("Rope",                  "Movie"),
    "loeb":            ("Rope",                  "Movie"),
    "kitty genovese":  ("Kitty",                 "Movie"),
    "wm3":             ("Devil's Knot",          "Movie"),
    "west memphis":    ("Devil's Knot",          "Movie"),

    # ARABIC / MIDDLE EAST
    "رأفت الهجان":              ("Agent Ramzy",       "Series"),
    "rafat al hagan":           ("Agent Ramzy",       "Series"),
    "el hagan":                 ("Rafat El Hagan",    "Series"),
    "يحيى العلمي":              ("Al Hayba",          "Series"),
    "al hayba":                 ("Al Hayba",          "Series"),
    "نمس":                      ("Al Nemr",           "Series"),

    # EGYPT
    "احمد عرابي":               ("Urabi Revolt",      "Documentary"),
    "ملك فاروق":                ("King Farouk",       "Series"),
    "king farouk":              ("King Farouk",       "Series"),
    "انور السادات":             ("Sadat",             "Movie"),
    "sadat":                    ("Sadat",             "Movie"),

    # SAUDI / GULF
    "juhayman":                 ("Juhayman",          "Series"),
    "جهيمان":                   ("Juhayman",          "Series"),
    "grand mosque seizure":     ("Juhayman",          "Series"),

    # IRAQ
    "saddam hussein":           ("House of Saddam",   "Series"),
    "سدام حسين":                ("House of Saddam",   "Series"),
    "house of saddam":          ("House of Saddam",   "Series"),
    "uday saddam":              ("House of Saddam",   "Series"),

    # SUDAN
    "hemedti":                  ("Sudan War Crimes",  "Documentary"),
    "محمد حمدان دقلو":           ("جرائم حرب السودان", "Documentary"),
    "dagalo":                   ("Sudan War Crimes",  "Documentary"),
    "حميدتي":                   ("RSF Sudan",         "Documentary"),
    "rsf sudan":                ("Sudan War Crimes",  "Documentary"),
    "rapid support forces":     ("Sudan War Crimes",  "Documentary"),
    "البشير":                   ("House of Bashir",   "Documentary"),
    "omar bashir":              ("Dictator Files",    "Documentary"),

    # INTERNATIONAL
    "kim jong un":              ("The Interview",     "Movie"),
    "chapo guzman":             ("El Chapo",          "Series"),

    # UK
    "reggie kray":              ("Legend",            "Movie"),
    "ronnie kray":              ("Legend",            "Movie"),
    "kray twins":               ("Legend",            "Movie"),
    "ronnie biggs":             ("Great Train Robbery", "Movie"),

    # COLOMBIA
    "carlos lehder":            ("Narcos",            "Series"),

    # MEXICO
    "miguel angel felix gallardo": ("Narcos Mexico",  "Series"),
    "felix gallardo":           ("Narcos Mexico",     "Series"),
    "amado carrillo":           ("Narcos Mexico",     "Series"),

    # ITALY
    "giovanni falcone":         ("The Traitor",       "Movie"),
    "falcone":                  ("The Traitor",       "Movie"),

    # RUSSIA
    "semion mogilevich":        ("McMafia",           "Series"),
    "mcmafia":                  ("McMafia",           "Series"),
    "bratva":                   ("McMafia",           "Series"),

    # JAPAN
    "yakuza":                   ("Tokyo Vice",        "Series"),
    "tokyo vice":               ("Tokyo Vice",        "Series"),

    # BRAZIL
    "fernandinho beira mar":    ("City of God",       "Movie"),
    "comando vermelho":         ("City of God",       "Movie"),
}


def get_series_for_person(topic_text: str) -> tuple[str, str] | None:
    """Return (series_name, type) tuple or None if no match.

    Matches longest keys first so "btk killer" is checked before "btk",
    preventing shorter substrings from shadowing more specific entries.
    """
    topic_lower = topic_text.lower()
    for person, info in sorted(PERSON_TO_SERIES.items(), key=lambda x: len(x[0]), reverse=True):
        if person in topic_lower:
            return info
    return None


_DARKCRIMED_BASE_HASHTAGS = [
    "#DarkCrimeDecoded", "#TrueCrime", "#RealStory", "#CrimeDocumentary",
]
_DARKCRIMED_BASE_AR_HASHTAGS = [
    "#جريمة_حقيقية", "#وثائقي_جريمة", "#دارك_كرايم_ديكودد",
]

# Arabic series names + content type (فيلم / مسلسل)
SERIES_ARABIC: dict[str, tuple[str, str]] = {
    "Narcos":              ("ناركوس",            "مسلسل"),
    "Narcos Mexico":       ("ناركوس المكسيك",    "مسلسل"),
    "Boardwalk Empire":    ("بوردووك إمباير",     "مسلسل"),
    "American Gangster":   ("أمريكان غانغستر",   "فيلم"),
    "Goodfellas":          ("غودفيلاز",          "فيلم"),
    "The Godfather":       ("العراب",            "فيلم"),
    "Scarface":            ("سكارفيس",           "فيلم"),
    "Casino":              ("كازينو",            "فيلم"),
    "Griselda":            ("غريزيلدا",          "مسلسل"),
    "Monster":             ("مونستر",            "مسلسل"),
    "Wolf of Wall Street": ("ذئب وول ستريت",     "فيلم"),
    "Black Mass":          ("بلاك ماس",          "فيلم"),
    "Donnie Brasco":       ("دوني براسكو",        "فيلم"),
    "City of God":         ("مدينة الله",         "فيلم"),
    "Mindhunter":          ("مايندهانتر",         "مسلسل"),
    "Night Stalker":       ("نايت ستوكر",         "مسلسل"),
    "Extremely Wicked":    ("إكستريملي ويكد",     "فيلم"),
    "Gotti":               ("غوتي",              "فيلم"),
    "Blow":                ("بلو",               "فيلم"),
    "Peaky Blinders":      ("بيكي بلايندرز",      "مسلسل"),
    # Global additions
    "House of Saddam":     ("بيت صدام",          "مسلسل"),
    "Juhayman":            ("جهيمان",            "مسلسل"),
    "Agent Ramzy":         ("رأفت الهجان",        "مسلسل"),
    "Al Hayba":            ("الهيبة",            "مسلسل"),
    "Legend":              ("الأسطورة",          "فيلم"),
    "McMafia":             ("ماك مافيا",          "مسلسل"),
    "Tokyo Vice":          ("طوكيو فايس",         "مسلسل"),
    "Baghdad Central":     ("بغداد سنترال",       "مسلسل"),
    "Fauda":               ("فاودا",             "مسلسل"),
    "Gomorrah":            ("غومورا",            "مسلسل"),
    "ZeroZeroZero":        ("زيرو زيرو زيرو",    "مسلسل"),
    "Suburra":             ("سوبورا",            "مسلسل"),
    "The Traitor":         ("الخائن",            "فيلم"),
    "King Farouk":         ("الملك فاروق",        "مسلسل"),
    "Sadat":               ("السادات",           "فيلم"),
    "The Interview":       ("المقابلة",          "فيلم"),
    "Great Train Robbery": ("السطو على القطار",   "فيلم"),
}


def validate_script(text: str) -> str:
    """Remove false comparisons where the same number appears on both sides of 'actually'."""
    import re
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        m = re.search(
            r'(\d+)\s*(?:years?|months?|days?)?[^.—]*[—-]+\s*actually[^.]*?(\d+)',
            line, re.IGNORECASE
        )
        if m and m.group(1) == m.group(2):
            # Same number on both sides — strip the "— actually ..." part
            before = re.split(r'\s*[—-]+\s*actually', line, flags=re.IGNORECASE)[0]
            cleaned.append(before.rstrip('.').strip() + '.')
        else:
            cleaned.append(line)
    return '\n'.join(cleaned)


_AR_TITLE_NOISE = re.compile(
    r'\b(netflix|show|true story|series|movie|film|documentary|decoded|dark crime)\b',
    re.IGNORECASE,
)


def _clean_arabic_title(raw: str) -> str:
    """Remove English noise words that leak into Arabic titles from translation."""
    import re as _re
    # Strip trailing English channel suffix — will be re-added
    raw = _re.sub(r'\s*\|\s*Dark Crime Decoded\s*$', '', raw, flags=_re.IGNORECASE).strip()
    # Remove known English noise tokens
    raw = _AR_TITLE_NOISE.sub('', raw)
    # Remove stray ASCII words (2+ chars) that shouldn't be in an Arabic title,
    # but preserve short Latin abbreviations that are part of proper nouns
    raw = _re.sub(r'\b[A-Za-z]{4,}\b', '', raw)
    raw = _re.sub(r'\s+', ' ', raw).strip().strip('|').strip()
    return f"{raw} | Dark Crime Decoded" if raw else "Dark Crime Decoded"


# Keywords that signal a historical/biblical/archaeological topic — bypass series framing
_HISTORICAL_TOPIC_SIGNALS: frozenset = frozenset({
    "archaeological", "archaeology", "ancient city", "ancient history",
    "biblical", "bible", "old testament", "new testament", "scripture",
    "excavation", "ruins of", "destruction of", "dead sea", "holy land",
    "bronze age", "iron age", "antiquity", "sodom", "mesopotamia",
    "historical evidence", "historical truth", "historical record",
    "genesis ", "exodus ", "revelation ", "artifact", "ancient civilization",
})


def _is_historical_topic(text: str) -> bool:
    """Return True if text signals a historical/biblical/archaeological topic, not a series."""
    t = text.lower()
    return any(sig in t for sig in _HISTORICAL_TOPIC_SIGNALS)


def _build_arabic_title(en_title: str, series_name: str | None, series_type: str | None) -> str:
    """Return clean Arabic title."""
    # Historical/biblical/archaeological topics bypass series lookup entirely
    if _is_historical_topic(en_title) or _is_historical_topic(series_name or ""):
        series_name = None

    # Series-based topics: "القصة الحقيقية وراء فيلم/مسلسل {series}"
    ar_entry = SERIES_ARABIC.get(series_name or "")
    if ar_entry:
        ar_series, ar_type = ar_entry
        return f"القصة الحقيقية وراء {ar_type} {ar_series} | Dark Crime Decoded"
    if series_name:
        ar_type = "فيلم" if series_type == "Movie" else "مسلسل" if series_type == "Series" else ""
        if ar_type:
            _ar_series = translate_to_arabic(series_name) or ""
            _ar_series = re.sub(r'[A-Za-z]+', '', _ar_series).strip()
            _ar_series = re.sub(r'\s+', ' ', _ar_series).strip()
            if _ar_series:
                return f"القصة الحقيقية وراء {ar_type} {_ar_series} | Dark Crime Decoded"

    # Historical topic: use "الحقيقة التاريخية لـ..." for better search relevance
    _historical = _is_historical_topic(en_title)
    topic_part = re.split(r'[:—–\-]', en_title, maxsplit=1)[0].strip()
    ar_topic = translate_to_arabic(topic_part) if topic_part else ""
    ar_topic = re.sub(r'[A-Za-z]+', '', ar_topic or "").strip()
    ar_topic = re.sub(r'\s+', ' ', ar_topic).strip()
    if not ar_topic:
        return "Dark Crime Decoded"
    prefix = "الحقيقة التاريخية لـ" if _historical else "القصة الحقيقية لـ"
    return f"{prefix}{ar_topic} | Dark Crime Decoded"


def _generate_arabic_title_llm(
    topic_str: str,
    angle_title: str = "",
    series_name: str | None = None,
    series_type: str | None = None,
) -> str:
    """Generate a natural Arabic documentary title via LLM. Falls back to _build_arabic_title."""
    if not topic_str:
        return "Dark Crime Decoded"

    _series_ctx = ""
    if series_name and not _is_historical_topic(topic_str):
        _ar_type = "فيلم" if (series_type or "").lower() == "movie" else "مسلسل"
        _series_ctx = (
            f"\nالعمل ذو الصلة (للسياق فقط — ليس موضوع الفيديو): "
            f"{_ar_type} مستوحى من {series_name}"
        )
    _angle_ctx = f"\nزاوية الفيديو: {angle_title}" if angle_title else ""

    prompt = (
        f"أنت خبير في صياغة عناوين الوثائقيات العربية.\n\n"
        f"الموضوع: {topic_str}{_angle_ctx}{_series_ctx}\n\n"
        f"اكتب عنواناً وثائقياً عربياً قوياً وطبيعياً. القواعد:\n"
        f"- العنوان يأتي مباشرة من القصة والزاوية — ليس من قوالب جاهزة\n"
        f"- لا تستخدم «القصة الحقيقية وراء فيلم/مسلسل...» إلا إذا كان العمل هو صلب الموضوع\n"
        f"- لا تخترع أعمالاً غير موجودة أو تفرض إطاراً درامياً\n"
        f"- اجعله طبيعياً كعنوان وثائقي حقيقي\n\n"
        f"أمثلة ممتازة:\n"
        f"- داخل إمبراطورية بابلو إسكوبار\n"
        f"- الليلة الأخيرة لجيفري إيبستين\n"
        f"- كيف سقط تيد بندي؟\n"
        f"- هل اكتشف العلماء آثار سدوم؟\n"
        f"- الملفات السرية لقضية داهمر\n"
        f"- ماذا حدث لمدينة قوم لوط؟\n\n"
        f"أعطني العنوان فقط (بدون | Dark Crime Decoded)، لا يتجاوز 55 حرفاً."
    )
    try:
        raw = _ai_script_call(prompt, max_tokens=80, temperature=0.4).strip()
        raw = raw.strip("\"'«»").strip()
        if raw and 5 < len(raw) < 120:
            raw = re.sub(r'\b[A-Za-z]{4,}\b', '', raw).strip()
            raw = re.sub(r'\s+', ' ', raw).strip().strip('|').strip()
            if raw:
                return f"{raw} | Dark Crime Decoded"
    except Exception as _e:
        print(f"[AR Title] LLM generation failed ({_e}) — using template fallback")
    return _build_arabic_title(topic_str, series_name, series_type)


# 5-chapter proportions for new structure
_CHAPTER_PROPORTIONS_5 = [0.0, 0.20, 0.42, 0.65, 0.85]

# Legacy 7-chapter labels (kept for backward compat with documentary angle)
CHAPTER_LABELS_EN = [
    "🎬 Introduction",
    "📖 Background & Origins",
    "⚡ Rise to Power",
    "😱 The Real Story",
    "💀 Shocking Revelations",
    "⚖️ Evidence & Investigation",
    "🎯 Conclusion",
]

CHAPTER_LABELS_AR = [
    "🎬 مقدمة",
    "📖 الخلفية والأصول",
    "⚡ الصعود إلى السلطة",
    "😱 القصة الحقيقية",
    "💀 الحقائق الصادمة",
    "⚖️ الأدلة والتحقيق",
    "🎯 الخاتمة",
]


def generate_chapters(total_words: int, language: str = "english",
                      angle_title: str = "") -> str:
    """Generate YouTube chapter timestamps for 5-chapter structure."""
    words_per_minute = 185 if language == "arabic" else 160
    total_seconds = (total_words / words_per_minute) * 60

    if language == "arabic":
        angle_label = angle_title or "الحقيقة المخفية"
        labels = [
            "البداية",
            angle_label,
            "القصة كاملة",
            "الكشف",
            "الخاتمة",
        ]
    else:
        angle_label = angle_title or "The Hidden Truth"
        labels = [
            "The Beginning",
            angle_label,
            "The Real Story",
            "The Revelation",
            "The Aftermath",
        ]

    chapters = []
    for ratio, title in zip(_CHAPTER_PROPORTIONS_5, labels):
        seconds = int(total_seconds * ratio)
        mins = seconds // 60
        secs = seconds % 60
        chapters.append(f"{mins:02d}:{secs:02d} {_sanitize_chapter_title(title)}")

    return "\n".join(chapters)


def _sanitize_chapter_title(text: str) -> str:
    """Strip supplementary-plane emoji (4-byte sequences) that corrupt in non-UTF-8 contexts.

    Keeps Arabic, Latin, and BMP punctuation. Prevents garbled output like â±ï¸ or 🎬.
    """
    import unicodedata as _ud
    nfc = _ud.normalize("NFC", text)
    # Drop codepoints above U+FFFF (supplementary plane — 4-byte emoji like 🎬 😱 📖)
    clean = "".join(c for c in nfc if ord(c) < 0x10000)
    return clean.strip(" \t\n-–—|")


def _generate_chapter_titles_llm(
    script_text: str,
    topic: str,
    language: str = "english",
    n: int = 5,
) -> list | None:
    """Ask LLM to generate n cinematic chapter titles from actual script content.

    Returns list of n clean strings, or None on failure.
    """
    words  = script_text.split()
    # Use first 600 words to keep token cost low
    excerpt = " ".join(words[:600]) if len(words) > 600 else script_text

    if language == "arabic":
        examples = (
            "البداية التي لم يفهمها أحد | أول الضحايا | داخل شبكة النفوذ | "
            "الليلة الأخيرة | ما كشفته الملفات | بداية التحقيق | "
            "الحقيقة التي حاولوا إخفاءها | سقوط الإمبراطورية"
        )
        lang_note = "Arabic (فصحى). 3-5 Arabic words each. No emoji."
    else:
        examples = (
            "The Night Everything Changed | First Victim | Inside the Network | "
            "What the Files Revealed | The Fall | Before the Evidence | The Last Move"
        )
        lang_note = "English. 3-5 words each. No emoji."

    prompt = (
        f"You are a documentary editor. Based on this script about \"{topic}\", "
        f"generate exactly {n} cinematic chapter titles.\n\n"
        f"Script excerpt:\n{excerpt}\n\n"
        f"Requirements:\n"
        f"- Titles must reflect actual story beats from THIS script\n"
        f"- Feel like Netflix documentary chapter names\n"
        f"- Language: {lang_note}\n"
        f"- NO generic titles like Introduction / Conclusion / Background\n"
        f"- NO emoji — plain text only\n"
        f"- Max 6 words per title\n"
        f"- ALL {n} titles must be UNIQUE — no two chapters can share the same name\n"
        f"- The LAST title must feel like a final ending, aftermath, or revelation\n\n"
        f"Good style examples: {examples}\n\n"
        f"Return ONLY this JSON:\n"
        f'{{"chapters": ["Title1", "Title2", "Title3", "Title4", "Title5"]}}'
    )
    try:
        raw = _ai_script_call(prompt, max_tokens=200, temperature=0.5, json_mode=True)
        parsed = normalize_ai_json_response(
            raw, required_keys=["chapters"], list_keys=["chapters"],
        )
        titles = parsed.get("chapters", [])
        if (
            isinstance(titles, list)
            and len(titles) == n
            and all(isinstance(t, str) and t.strip() for t in titles)
        ):
            return [_sanitize_chapter_title(t) for t in titles]
    except Exception as _e:
        print(f"[Chapters] LLM title generation failed ({_e})")
    return None


def generate_chapters_from_script(
    script_text: str,
    topic: str,
    language: str = "english",
    angle_title: str = "",
) -> str:
    """Generate YouTube chapter timestamps with cinematic titles from actual script content.

    Calls LLM to extract real story beats. Falls back to improved generic labels.
    Timestamps computed from word count using _CHAPTER_PROPORTIONS_5.
    """
    total_words = clean_word_count(script_text)
    wpm = 185 if language == "arabic" else 156
    total_seconds = (total_words / max(wpm, 1)) * 60

    llm_titles = None
    if script_text.strip():
        llm_titles = _generate_chapter_titles_llm(script_text, topic, language)

    if llm_titles and len(llm_titles) == 5:
        labels = llm_titles
    else:
        # Improved fallback — more descriptive than pure placeholders
        if language == "arabic":
            _angle = _sanitize_chapter_title(angle_title) or "الحقيقة المخفية"
            labels = ["البداية", _angle, "القصة كاملة", "الكشف", "الخاتمة"]
        else:
            _angle = _sanitize_chapter_title(angle_title) or "The Hidden Truth"
            labels = ["The Beginning", _angle, "The Real Story", "The Revelation", "The Aftermath"]

    # Guard: ensure no empty title slips through
    labels = [
        _sanitize_chapter_title(l) or f"Part {i + 1}"
        for i, l in enumerate(labels)
    ]

    # Deduplicate: if LLM returned duplicate titles, append index to distinguish
    _seen_titles: dict[str, int] = {}
    deduped: list[str] = []
    for _lbl in labels:
        _key = _lbl.strip().lower()
        if _key in _seen_titles:
            _seen_titles[_key] += 1
            deduped.append(f"{_lbl} {_seen_titles[_key]}")
        else:
            _seen_titles[_key] = 1
            deduped.append(_lbl)
    labels = deduped

    chapters = []
    for ratio, title in zip(_CHAPTER_PROPORTIONS_5, labels):
        seconds = int(total_seconds * ratio)
        chapters.append(f"{seconds // 60:02d}:{seconds % 60:02d} {title}")

    return "\n".join(chapters)


def add_short_title(script_data: dict) -> str:
    """Generate a clickable short video title with emoji via Groq."""
    topic = script_data.get("topic", "")
    _si   = get_series_for_person(topic)
    series = _si[0] if _si else script_data.get("niche", "")
    series_tag = f"#{series.replace(' ', '')}" if series else ""

    prompt = f"""Generate ONE punchy YouTube Shorts / TikTok title for a true crime short video.

Topic: {topic}
Related series/movie: {series}

RULES:
- Max 60 characters total
- CAPITALISE one exciting word: REAL, INCREDIBLE, MORE, BEFORE, INSPIRED, TRUE, NEVER
- End with ONE relevant emoji chosen from: 🎬 😱 🔍 💀 🔴
- Add the series hashtag ({series_tag}) if a series is known
- NO "Dark Crime Decoded:" prefix — this is for Shorts/TikTok
- Celebrate both the real story AND the show — informative, not accusatory tone

EXAMPLES:
"The REAL Al Capone was more incredible than Boardwalk Empire showed 🎬"
"The TRUE story that inspired Narcos is even wilder #Narcos 😱"
"What REALLY happened before Breaking Bad 🔍"
"The REAL Pablo Escobar was more extraordinary than Narcos showed 🎬"

Output ONLY the title text, nothing else."""

    return _ai_script_call(prompt, max_tokens=80, temperature=0.85).strip().strip('"\'')


def _build_darkcrimed_hashtags(raw: str, series_info: tuple[str, str] | None) -> str:
    """
    Prepend series/movie tags and guarantee base tags are present.
    raw: space-separated hashtag string from Groq (may include Arabic tags).
    """
    tags = raw.split() if raw else []

    prefix: list[str] = []
    if series_info:
        series_name, series_type = series_info
        series_tag = "#" + series_name.replace(" ", "")   # e.g. #Narcos
        type_tag   = "#" + series_type                     # e.g. #Series
        if series_tag not in tags:
            prefix.append(series_tag)
        if type_tag not in tags:
            prefix.append(type_tag)

    for tag in _DARKCRIMED_BASE_HASHTAGS + _DARKCRIMED_BASE_AR_HASHTAGS:
        if tag not in tags:
            tags.append(tag)

    return " ".join(prefix + tags)


def _is_shopmart() -> bool:
    """Return True when the pipeline is running for Shopmart Global."""
    try:
        import config as _cfg
        return "shopmart" in getattr(_cfg, "CHANNEL", "").lower()
    except Exception:
        return False


def _validate_on_topic(script: str, topic_name: str, series_label: str) -> bool:
    """Return True if script mentions both the real person and the show/movie."""
    t = safe_lower(script)
    t_words = [w for w in safe_lower(topic_name).split() if len(w) > 3]
    s_words = [w for w in safe_lower(series_label).split() if len(w) > 3]
    topic_ok  = any(w in t for w in t_words)  if t_words  else True
    series_ok = any(w in t for w in s_words)  if s_words  else True
    return topic_ok and series_ok


def balance_entity_mentions(script: str, entities: list[str] | None = None) -> str:
    """
    Reduce over-anchoring by inserting adjacent documentary context when one
    entity dominates the narration. This adds new angles, not duplicate facts.
    """
    if not script:
        return script
    entities = entities or [
        "Jordan Belfort",
        "Danny Porush",
        "Stratton Oakmont",
    ]
    words = max(clean_word_count(script), 1)
    counts = {
        ent: len(re.findall(re.escape(ent), script, flags=re.IGNORECASE))
        for ent in entities
        if ent
    }
    if not counts:
        return script
    dominant, hits = max(counts.items(), key=lambda kv: kv[1])
    if hits / words < 0.012 and hits < 18:
        print("[SCRIPT BALANCE] Entity density normalized")
        return script

    context = (
        "\n\n[SECTION: Wider Investigation]\n"
        "The story also belongs to the investors who were pressured into risky trades, "
        "to the SEC investigators tracing patterns across brokerage records, and to the "
        "FBI agents turning sales-floor bravado into evidence. Offshore transfers, market "
        "manipulation tactics, investor psychology, courtroom consequences, and the media "
        "myth that followed all widen the frame beyond any single name."
    )
    print("[SCRIPT BALANCE] Over-anchoring reduced")
    print("[SCRIPT BALANCE] Entity density normalized")
    return script.rstrip() + context


def write_script(topic: dict, language: str = "english") -> dict:
    if _is_shopmart():
        return _write_shopmart_script(topic)
    return _write_darkcrimed_script(topic)


# ============================================================
# ANIMATION MODE QUALITY GUARDS
# — fact-anchor system, duplication detection, repetition memory,
#   timeline validation. Called inside write_animation_script().
# ============================================================

def _build_fact_anchor_map(research: dict) -> str:
    """Build a structured fact injection block for animation script prompts."""
    facts    = research.get("research_facts") or []
    shocking = research.get("research_shocking") or []
    rvf      = research.get("real_vs_fiction") or {}
    timeline = rvf.get("time_period", "")
    locs     = rvf.get("real_locations", [])

    lines = ["FACT ANCHOR MAP — every section MUST reference at least one entry:"]
    if timeline:
        lines.append(f"DOCUMENTED ERA:      {timeline}")
    if locs:
        lines.append(f"REAL LOCATIONS:      {', '.join(str(l) for l in locs[:5])}")
    for i, f in enumerate(facts[:14], 1):
        lines.append(f"FACT {i:02d}:            {f}")
    for i, s in enumerate(shocking[:6], 1):
        lines.append(f"KEY EVENT {i:02d}:       {s}")
    return "\n".join(lines)


def section_similarity_check(script_text: str) -> dict:
    """
    Detect duplicate or over-similar sections in a script.
    Returns {section_name: overlap_pct, "has_duplicates": bool}.
    Threshold: >30% sentence overlap with any prior section = duplicate.
    """
    import re as _re

    parts = _re.split(r'(\[SECTION:[^\]]+\])', script_text)
    pairs: list[tuple[str, str]] = []
    i = 0
    if parts and not _re.match(r'\[SECTION:', parts[0].strip()):
        i = 1   # skip preamble
    while i < len(parts) - 1:
        if _re.match(r'\[SECTION:[^\]]+\]', parts[i]):
            pairs.append((parts[i].strip(), parts[i + 1].strip() if i + 1 < len(parts) else ""))
            i += 2
        else:
            i += 1

    if len(pairs) < 2:
        return {"has_duplicates": False}

    def _sents(text: str) -> set:
        return {s.strip().lower() for s in _re.split(r'[.!?؟\n]', text) if len(s.strip()) > 25}

    result: dict = {}
    prior_pool: set = set()
    for label, body in pairs:
        curr = _sents(body)
        if curr:
            overlap = len(curr & prior_pool) / len(curr)
            if overlap > 0.30:
                result[label] = f"{overlap:.0%} overlap with prior sections"
        prior_pool |= curr

    result["has_duplicates"] = bool({k: v for k, v in result.items() if k != "has_duplicates"})
    return result


def _animation_repetition_check(script_text: str) -> dict:
    """
    Track overused atmospheric phrases, internal-monologue templates,
    and suspense sentence patterns in animation scripts.
    Returns {"overused_phrases": {pattern: count}, "has_repetition": bool}.
    Limit: any pattern appearing >= 3 times is flagged.
    """
    import re as _re

    _WATCH_PATTERNS = [
        # Arabic internal monologue — الإشكالية السابعة
        r"كان يعرف", r"كان يشعر", r"كان يدرك", r"كانت تعرف", r"كانت تشعر",
        # English internal monologue
        r"\bhe knew\b", r"\bhe felt\b", r"\bhe realized\b",
        r"\bshe knew\b", r"\bshe felt\b", r"\bshe realized\b",
        # Atmosphere loops
        r"\bthe silence\b", r"\bthe darkness\b", r"\bthe shadows\b",
        r"\bfear grew\b", r"\bdread\b", r"\bterror\b",
        # Generic suspense wrappers
        r"\bnobody knew\b", r"\bno one knew\b", r"\bnothing was\b",
        r"الصمت", r"الظلام", r"الخوف يتصاعد",
    ]

    hits = {}
    for pat in _WATCH_PATTERNS:
        count = len(_re.findall(pat, script_text, _re.IGNORECASE))
        if count >= 3:
            hits[pat] = count

    return {"overused_phrases": hits, "has_repetition": bool(hits)}


def timeline_checkpoint_validation(script_text: str, research_facts: list) -> dict:
    """
    Validate that the script's years/dates stay within the documented range.
    Flags years that appear in the script but are far outside the research window.
    Returns {"consistent": bool, "issues": list[str], "script_years": list, "fact_years": list}.
    """
    import re as _re

    sy = [int(y) for y in _re.findall(r'\b(1[6-9]\d{2}|20\d{2})\b', script_text)]
    fy = [int(y) for y in _re.findall(r'\b(1[6-9]\d{2}|20\d{2})\b', " ".join(str(f) for f in research_facts))]

    issues: list[str] = []
    if sy and fy:
        fmin, fmax = min(fy), max(fy)
        for yr in set(sy):
            if yr < fmin - 30 or yr > fmax + 5:
                issues.append(f"Year {yr} is far outside documented range {fmin}–{fmax}")

    return {
        "consistent": len(issues) == 0,
        "issues": issues,
        "script_years": sorted(set(sy)),
        "fact_years":   sorted(set(fy)),
    }


def scene_progression_validator(script_text: str) -> dict:
    """
    Detect story stalls — 250-word chunks where narrative state doesn't evolve.
    Returns {"stalled_zones": list[str], "has_stalls": bool, "progression_score": float}.
    Called after script assembly to flag momentum collapse before video generation.
    """
    import re as _re

    _PROGRESS = [
        r'\b(found|discovered|revealed|uncovered|confirmed|identified|arrested|charged|convicted)\b',
        r'\b(investigators?|detectives?|police|FBI|court|trial|testimony|confession|evidence)\b',
        r'\b(witness|document|report|record|forensic|interrogat|indicted)\b',
        r'\b(19\d{2}|20\d{2})\b',
        r'\b(then|next|meanwhile|later|finally|suddenly|unexpectedly|instead|however)\b',
        r'\b(but|yet|despite|although|turned out|revealed that|showed that)\b',
    ]

    _STALL = [
        r'\b(was known for|had a reputation|was described as|people said he|people said she)\b',
        r'\b(according to|it was reported that|sources say|experts believe)\b',
        r'\b(had always|had never|had been known|was always|was never)\b',
        r'\b(throughout his|throughout her|all his life|all her life)\b',
        r'\b(it is worth noting|it should be noted|as we mentioned|as noted earlier)\b',
    ]

    words = script_text.split()
    chunk_size = 250
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]

    if len(chunks) < 2:
        return {"stalled_zones": [], "has_stalls": False, "progression_score": 1.0}

    stalled: list[str] = []
    stalled_chunks: list[str] = []
    for idx, chunk in enumerate(chunks):
        prog  = sum(1 for p in _PROGRESS if _re.search(p, chunk, _re.IGNORECASE))
        stall = sum(1 for p in _STALL    if _re.search(p, chunk, _re.IGNORECASE))
        if prog < 2 or stall >= 3:
            stalled.append(
                f"Zone {idx + 1} (~words {idx * chunk_size}–{(idx + 1) * chunk_size}): "
                f"progress_signals={prog}, stall_signals={stall}"
            )
            stalled_chunks.append(chunk)

    score = round(max(0.0, 1.0 - len(stalled) / max(len(chunks), 1)), 2)
    return {
        "stalled_zones": stalled,
        "stalled_chunks": stalled_chunks,
        "has_stalls": bool(stalled),
        "progression_score": score,
    }


def rewrite_dead_zone(
    segment: str,
    topic_name: str = "",
    act_label: str = "",
) -> str:
    """
    Soft-rewrite a 150–700 word stalled segment.
    Only replaces low-momentum ranges — does NOT regenerate the full script.
    Returns the original segment unchanged if the rewrite fails or word count drifts.
    """
    wc = len(segment.split())
    if wc < 150 or wc > 700:
        return segment  # outside safe rewrite window — leave untouched

    label_hint = f" in {act_label}" if act_label else ""
    prompt = (
        f"You are editing a crime documentary script{label_hint} about {topic_name}.\n\n"
        "The following segment has been flagged as low-momentum — it repeats emotional beats, "
        "uses atmosphere loops, or rephrases already-stated facts without advancing the story.\n\n"
        f"ORIGINAL SEGMENT ({wc} words):\n{segment}\n\n"
        "REWRITE RULES:\n"
        "1. Keep the same approximate word count (±60 words)\n"
        "2. Preserve ALL specific names, dates, and documented facts\n"
        "3. Replace atmosphere loops with a NEW documented detail, witness account, or investigation step\n"
        "4. Replace rephrased discoveries with a forward-moving story beat\n"
        "5. Do NOT add fictional events or unverified claims\n"
        "6. End on a beat that pulls the story forward\n\n"
        "Output ONLY the rewritten segment — no preamble, no explanation."
    )
    try:
        rewritten = _ai_script_call(prompt, max_tokens=900, temperature=0.65)
        if not rewritten:
            return segment
        new_wc = len(rewritten.split())
        if new_wc < wc * 0.60 or new_wc > wc * 1.55:
            return segment  # word count drifted — not safe to use
        return rewritten
    except Exception:
        return segment  # always fail safe — original is better than broken


def write_animation_script(topic: dict) -> dict:
    """
    Fact-anchored cinematic animation script — 4,000+ EN words (15–60 min).
    Scene-driven storytelling grounded in documented facts — NOT documentary summary.
    """
    research    = topic.get("research", {})
    real_person = research.get("real_person") or topic.get("topic", "")
    topic_text  = topic.get("topic", real_person)
    facts       = research.get("research_facts") or []
    shocking    = research.get("research_shocking") or []

    # Build the fact anchor map — injected into the prompt so every section
    # stays connected to documented reality, not fictional reconstruction.
    fact_map = _build_fact_anchor_map(research)

    script_prompt = f"""You are writing a FACT-ANCHORED cinematic documentary script.
This is cinematic storytelling grounded in documented reality — NOT thriller fiction.

SUBJECT: {real_person}
TOPIC: {topic_text}

{fact_map}

━━━ CORE RULE — FACT ANCHOR SYSTEM ━━━
Every section MUST contain ALL THREE of:
1. At least ONE verified item from the FACT ANCHOR MAP (date, name, location, documented action)
2. ONE timeline anchor connecting to the documented sequence of events
3. ONE investigation/consequence progression — what happened next, who found out, what changed

CINEMATIC RECREATION vs INVENTION:
- Write documented facts AS SCENES — but stay within what is documented
- Reconstruct moments from known police reports, court testimony, confessions, news records
- NEVER invent: daily routines, wandering, gas-station life, survival sequences,
  conversations with no documented basis, emotional inner journeys not in any source

INVESTIGATION MOMENTUM — mandatory per section:
  event → reaction → investigation → discovery → escalation → consequence
Every section must ADVANCE the story. Ask: "What NEW documented fact or progression is here?"
If a section answers only "atmosphere" — rewrite it with a documented fact.

BANNED INVENTIONS — never write any of these:
- Invented gas-station life, city wandering, survival sequences
- Fictional daily routines ("He woke up, made coffee...")
- Imaginary conversations with no documented basis
- Unsupported emotional arcs ("He began to feel the weight of...")
- Fake locations or actions not in any documented source

INTERNAL MONOLOGUE LIMIT:
Phrases like "he knew", "he felt", "he realized", "she knew", "she felt" are
acceptable once per section maximum. Replace with: action, reaction, investigation move,
documented dialogue, or physical evidence.

SENTENCE STYLE:
- Mix short punches (8-12 words) with medium narrative sentences (15-20 words)
- Ground scenes in documented facts: "On [date], investigators found..."
- NO Wikipedia narration: never "born on", "according to", "it was reported that"
- Every paragraph ends with a documented progression hook

ATMOSPHERE LIMIT: Maximum ONE atmospheric sentence per 300 words.
BAD: "The apartment was quiet. Too quiet." (pure atmosphere)
GOOD: "The apartment was quiet. Officers found the second note under the kitchen table."

BANNED PHRASES:
"in a shocking twist", "little did they know", "nobody could have predicted",
"what happened next", "throughout history", "this is a story about",
"the truth was darker", "changed the world forever", "behind the scenes"

PROSE RULES:
- No bullet points. No numbered lists. Pure prose.
- Minimum 4 sentences per paragraph, maximum 6.
- Specific documented facts carry the tension — not atmospheric language.

━━━ STRUCTURE ━━━

NARRATIVE MOMENTUM ENGINE (applies to every section):
Every 50-80 words must contain ONE story beat: clue, investigation turn, twist, reveal,
consequence, or escalation. NEVER pad with repeated biography or atmosphere.

[SECTION: hook]
BEGIN AT A DOCUMENTED CRITICAL MOMENT — arrest, discovery, confrontation, or court moment.
NOT invented atmosphere. A real event that anchors the story immediately.
Build immediate cinematic tension: who, what, where, what it meant.
5-6 paragraphs. ~400 words.

[SECTION: background]
DOCUMENTED HISTORY of this person. Real biography from known sources.
Real family, known locations, documented associations, early warning signs.
Each paragraph must advance time or reveal something new — not restate what came before.
6-7 paragraphs. ~550 words.

[SECTION: childhood]
DOCUMENTED EARLY HISTORY. Specific known facts about formative years.
First documented warning signs or incidents from real records.
Scene by scene — each paragraph = one documented moment, not a summary.
5-6 paragraphs. ~400 words.

[SECTION: story]
THE DOCUMENTED CRIME OR EVENT — scene by scene from real records.
Specific dates and locations from documented sources.
Police reports, witness accounts, court records reconstructed as narrative scenes.
Every paragraph advances: event → reaction → investigation → consequence.
8-10 paragraphs. ~750 words.

[SECTION: evidence]
THE DOCUMENTED INVESTIGATION — what investigators found and recorded.
Specific forensic evidence, documented items, official reports.
Investigation momentum: discovery → analysis → new lead → new discovery.
5-6 paragraphs. ~400 words.

[SECTION: confession]
THE DOCUMENTED BREAK — arrest, interrogation, known turning point.
What is on record about the suspect's statements.
Real documented quotes or paraphrased known testimony.
Scene: location, who was there, what was said, what changed.
5-6 paragraphs. ~350 words.

[SECTION: trial]
DOCUMENTED COURTROOM PROCEEDINGS.
Prosecutor's documented arguments. Key documented testimony. Defense moves.
Known facts about verdict process. Scene-by-scene courtroom momentum.
5-6 paragraphs. ~400 words.

[SECTION: verdict]
THE DOCUMENTED OUTCOME — exactly what the record shows.
Known sentence, documented reactions, confirmed final facts.
Scene: the moment of verdict, who reacted, what it meant.
4-5 paragraphs. ~280 words.

[SECTION: aftermath]
DOCUMENTED AFTERMATH — what happened to known parties per verified sources.
Changes to law, policy, or investigation methods from documented record.
Unanswered questions from documented case files.
5-6 paragraphs. ~350 words.

[SECTION: conclusion]
THE DOCUMENTED LEGACY — real impact grounded in verifiable outcomes.
Final thought from documented facts. Close with weight — not a summary.
3-4 paragraphs. ~200 words.

━━━ TOTAL TARGET: 4,000-5,000 words ━━━
Write ONLY the script with section markers. No meta-commentary. No word count labels.
Every sentence must reference a documented fact or serve direct narrative progression.
Pure atmosphere sentences with no documented basis must not appear."""

    script_text = _ai_script_call(
        script_prompt,
        max_tokens=9000,
        temperature=0.75,
        system_prompt=(
            "You are a cinematic crime storyteller for animation mode — fact-anchored, scene-driven, "
            "and binge-worthy. NARRATIVE MOMENTUM ENGINE: every 50-80 words must contain a story beat "
            "(clue, investigation turn, twist, reveal, consequence, or escalation). "
            "FORBIDDEN: invented daily routines, fictional wandering, atmosphere without documented facts, "
            "repeated biography, psychological analysis loops, Wikipedia summary narration. "
            "EVERY sentence must be grounded in documented reality. "
            "Cinematic means: real facts reconstructed as scenes — not narrated as articles."
        ),
    ).strip()

    # Ensure section markers present — fall back if generation failed badly
    if "[SECTION:" not in script_text or clean_word_count(script_text) < 1500:
        print("[Script] Animation script fallback — output too short or missing markers, using documentary style")
        return _write_darkcrimed_script(topic)

    # Word count floor enforcement — animation needs 4,000+ EN words
    _anim_floor = _WORD_FLOORS["animation"]["english"]
    _wc_anim = clean_word_count(script_text)
    if _wc_anim < _anim_floor:
        _anim_missing = _anim_floor - _wc_anim
        print(f"[Script][ANIM] ⚠️ Below {_anim_floor:,}w floor: {_wc_anim}w — "
              f"expanding with {_anim_missing}+ new scene words")
        script_text = expand_script_runtime(script_text, _anim_missing, topic=topic_text)
        _wc_anim = clean_word_count(script_text)
        print(f"[Script][ANIM] After expansion: {_wc_anim}w (~{_wc_anim/145:.0f}min)")

    script_text = check_hallucination(script_text)

    # ── Quality gate: duplication, repetition, timeline ──────────────────────
    _dup = section_similarity_check(script_text)
    if _dup.get("has_duplicates"):
        print(f"[Script][ANIM] Duplicate sections detected: "
              f"{[k for k in _dup if k != 'has_duplicates']}")

    _rep = _animation_repetition_check(script_text)
    if _rep.get("has_repetition"):
        print(f"[Script][ANIM] Overused phrases: {list(_rep['overused_phrases'].keys())}")

    _tl = timeline_checkpoint_validation(script_text, facts + shocking)
    if not _tl.get("consistent"):
        print(f"[Script][ANIM] Timeline issues: {_tl['issues']}")

    # Metadata generation
    meta_prompt = f"""Generate YouTube metadata for this animation crime documentary.

SUBJECT: {real_person}
Script opening (first 300 chars): {script_text[:300]}

Return ONLY valid JSON — no other text:
{{
  "title": "YouTube title — 55-70 chars, factual and compelling, no clickbait superlatives",
  "hook": "Opening 1-2 sentences from the script (direct quote)",
  "caption": "2-3 sentence YouTube description",
  "hashtags": "#truecrime #documentary #crime #darkcrimedeocded #realcrime #animation",
  "thumbnail_text": "3-4 word thumbnail label"
}}"""

    meta = normalize_ai_json_response(
        _ai_script_call(meta_prompt, max_tokens=500, temperature=0.3, json_mode=True),
        required_keys=["title", "hook", "caption", "hashtags", "thumbnail_text"],
        list_keys=[],
    )

    _wc = clean_word_count(script_text)
    return {
        "title":           meta.get("title") or f"{real_person}: The Real Story | Dark Crime Decoded",
        "hook":            meta.get("hook") or script_text[:120],
        "script":          script_text,
        "on_screen_texts": [],
        "caption":         meta.get("caption", f"The real story of {real_person}. Follow Dark Crime Decoded."),
        "hashtags":        meta.get("hashtags", "#truecrime #documentary #darkcrimedeocded"),
        "thumbnail_text":  meta.get("thumbnail_text") or real_person[:30],
        "chapters":        generate_chapters(_wc),
        "topic":           topic_text,
        "niche":           topic.get("niche", topic_text),
        "search_query":    topic.get("search_query", ""),
        "keywords":        topic.get("keywords", [topic_text]),
        "language":        "english",
        "series_name":     research.get("series_name", ""),
        "series_type":     research.get("series_type", "Documentary"),
        "anim_mode":       True,
    }


def _write_shopmart_script(topic: dict) -> dict:
    """Product review / top-list style script for Shopmart Global."""
    word_count = 130  # ~55-second short video

    part1_prompt = f"""You are a product review content creator for YouTube Shorts and TikTok.
Write a punchy {word_count}-word voiceover script for the topic below.

Topic: {topic['topic']}
Niche: {topic['niche']}

REQUIREMENTS:
- Write EXACTLY {word_count} words — count every word before finishing
- Opening: one attention-grabbing hook that stops the scroll (1-2 sentences)
- Middle: 3-5 short punchy product benefits or reasons to buy — one per line
- Closing: strong call to action ("Link in bio", "Buy now before it sells out", "Check the link below")
- NO documentary tone, NO crime references, NO headers, NO bullet points
- Write like an enthusiastic product reviewer speaking to camera
- Short sentences, maximum 12 words each
- Use '...' for natural spoken pauses

Output ONLY the script text, nothing else."""

    script_text = _ai_script_call(part1_prompt, max_tokens=400, temperature=0.85).strip()

    part2_prompt = f"""You are a content packaging assistant for an ecommerce channel called Shopmart.
Based on this product review script, generate metadata.

Topic: {topic['topic']}
Script (first 200 chars): {script_text[:200]}...

Return ONLY this JSON with no extra text:
{{
  "title": "Shopmart: [product/topic] — [short hook] (max 80 chars)",
  "hook": "First spoken hook sentence (max 15 words)",
  "on_screen_texts": [
    "Bold text for second 0",
    "Bold text for second 10",
    "Bold text for second 25",
    "Bold text for second 45"
  ],
  "caption": "2-3 sentence caption with product benefits and a buy link CTA",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "4-word thumbnail text"
}}"""

    meta = normalize_ai_json_response(
        _ai_script_call(part2_prompt, max_tokens=600, temperature=0.3, json_mode=True),
        required_keys=["title", "hook", "on_screen_texts", "caption", "hashtags", "thumbnail_text"],
        list_keys=["on_screen_texts"],
    )
    script_data = {
        "title":           meta.get("title") or f"Shopmart: {topic['topic']}",
        "hook":            meta.get("hook", ""),
        "script":          script_text,
        "on_screen_texts": meta.get("on_screen_texts", []),
        "caption":         meta.get("caption", ""),
        "hashtags":        meta.get("hashtags", ""),
        "thumbnail_text":  meta.get("thumbnail_text", ""),
        "topic":           topic["topic"],
        "niche":           topic["niche"],
        "search_query":    topic.get("search_query", ""),
        "keywords":        topic.get("keywords", []),
        "language":        "english",
    }
    print(f"[Script] Written (shopmart english): '{script_data['title']}'")
    return script_data


DOCUMENTARY_ONLY_TOPICS = [
    "hemedti",
    "حميدتي",
    "dagalo",
    "محمد حمدان دقلو",
    "omar bashir",
    "البشير",
    "rsf sudan",
    "rapid support forces",
]


def get_script_angle(topic_text: str, series_info: tuple | None) -> str:
    """Return 'documentary' when topic has no confirmed adaptation, else 'series'."""
    topic_lower = safe_lower(topic_text)
    for doc_topic in DOCUMENTARY_ONLY_TOPICS:
        if doc_topic in topic_lower:
            return "documentary"
    # Historical/biblical/archaeological topics never need series framing
    if _is_historical_topic(topic_text):
        return "documentary"
    # No confirmed PERSON_TO_SERIES match → investigative documentary angle
    if series_info is None:
        return "documentary"
    return "series"


def detect_part_number(user_note: str) -> int | None:
    """Return 1 or 2 if user_note contains a part marker, else None."""
    if not user_note:
        return None
    note_lower = user_note.lower()
    if "part 1" in note_lower or "الجزء الأول" in note_lower:
        return 1
    if "part 2" in note_lower or "الجزء الثاني" in note_lower:
        return 2
    return None


_PART2_QUEUE_PATH = "output/pending_part2.json"


def queue_part2_topic(topic: dict) -> None:
    """Save topic to a queue file so the next run can pick it up as Part 2."""
    import datetime
    from pathlib import Path as _Path
    queue_path = _Path(_PART2_QUEUE_PATH)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "topic":        topic.get("topic", ""),
        "niche":        topic.get("niche", ""),
        "search_query": topic.get("search_query", ""),
        "keywords":     topic.get("keywords", []),
        "user_note":    f"Part 2 — {topic.get('topic', '')}",
        "queued_at":    datetime.date.today().isoformat(),
        "part":         2,
    }
    queue_path.write_text(
        json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Script] Part 2 queued for tomorrow: {entry['topic']}")


def load_queued_part2() -> dict | None:
    """Load and clear a pending Part 2 topic if one exists."""
    from pathlib import Path as _Path
    queue_path = _Path(_PART2_QUEUE_PATH)
    if not queue_path.exists():
        return None
    try:
        entry = safe_json_parse(queue_path.read_text(encoding="utf-8"), fallback=None)
        if not entry:
            print("[Script] Part 2 queue file is empty or invalid — skipping")
            return None
        queue_path.unlink()
        print(f"[Script] Loaded queued Part 2: {entry.get('topic', '')}")
        return entry
    except Exception as e:
        print(f"[Script] Failed to load Part 2 queue: {e}")
        return None


def _is_hemedti_topic(topic_text: str) -> bool:
    """Return True if the topic is about Hemedti / RSF Sudan."""
    t = safe_lower(topic_text)
    return any(k in t for k in ["hemedti", "حميدتي", "dagalo", "محمد حمدان", "rsf sudan"])


def _write_hemedti_part1(research: dict) -> str:
    """Hemedti Part 1 — Origins through Darfur crimes."""
    facts = "\n".join(f"- {f}" for f in (
        research.get("research_facts") or []
    )[:5]) or "(research the documented background)"

    prompt = f"""You are an investigative documentary writer.
Write a 1800-word Part 1 script about Mohamed Hamdan Dagalo (Hemedti).

VERIFIED FACTS AVAILABLE:
{facts}

Use this EXACT structure (spoken words only — no section labels):

HOOK (100 words):
Open with: "In 2023 he launched the deadliest war in African history.
But in 1980 he was just a camel trader on the Chad-Sudan border
with no education and no future..."
Why this story matters now.

ORIGINS (400 words):
The Chad/Sudan border geography and its open-border history.
The Dagalo family roots across both countries.
Camel trading background — specific routes, specific years.
First connection to armed groups and how it happened.
How poverty and geography shaped his ambition.

RISE TO POWER (500 words):
Janjaweed militia — what it was, when it started, who ran it.
Darfur 2003 — Bashir's decision to use Janjaweed as a weapon.
Hemedti's role: specific operations, specific years.
How he built personal wealth from conflict — gold, livestock, land.
The transformation from militia commander to RSF general.

DARFUR CRIMES (400 words):
Documented war crimes with specific dates.
ICC warrant — what it covers, when issued.
Number of victims — villages burned with documented dates.
International response and why it failed.
How he escaped accountability.

MYSTERY (200 words):
How a camel trader became a billionaire.
Gold mines in Darfur — the documented connection.
UAE gold trade deals — confirmed reports.
Estimated personal wealth from investigative reports.

CONCLUSION + PART 2 TEASER (100 words):
"This is only the beginning of Hemedti's story.
In Part 2, we reveal how he overthrew Sudan's dictator,
massacred protesters in Khartoum, and started a full civil war
with UAE backing and Colombian mercenaries.
Follow Dark Crime Decoded — Part 2 coming soon."

TERMINOLOGY — USE EXACTLY AS WRITTEN:
- First mention: "Rapid Support Forces (RSF)" — then "RSF" alone after that
- First mention of Janjaweed: "Janjaweed militia" — then "Janjaweed" alone
- First mention of SAF: "Sudan Armed Forces (SAF)" — then "SAF" alone
- First mention of ICC: "International Criminal Court (ICC)" — then "ICC" alone
- NO documentary exists about RSF — NEVER reference a film or documentary
- This is based on investigative journalism and documented evidence only
- NEVER write "The RSF Documentary" or "The documentary shows" — say "Evidence confirms" or "Reports show"

RULES:
- 1800 words total
- Specific dates, numbers, names — every sentence
- Never state as confirmed what is only alleged
- Write like a serious Al Jazeera / BBC documentary narrator
- No section labels — spoken words only

Start immediately with the HOOK."""

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75, premium=True)
    words = clean_word_count(result) if result else 0
    print(f"[Script] Hemedti Part 1: {words} real words")
    return result or ""


def _write_hemedti_part2(research: dict) -> str:
    """Hemedti Part 2 — Revolution, massacre, UAE, mercenaries, current war."""
    facts = "\n".join(f"- {f}" for f in (
        research.get("research_facts") or []
    )[:5]) or "(research the documented events)"

    prompt = f"""You are an investigative documentary writer.
Write a 1800-word Part 2 script about Mohamed Hamdan Dagalo (Hemedti).
This is a continuation — viewers already know Part 1 (his origins and Darfur).

VERIFIED FACTS AVAILABLE:
{facts}

Use this EXACT structure (spoken words only — no section labels):

HOOK (100 words):
"He helped overthrow Sudan's dictator.
Then he became Sudan's biggest monster.
This is Part 2 of Hemedti's story."
Brief recap: who Hemedti is, what Part 1 covered.

REVOLUTION ROLE (300 words):
The 2019 revolution against Omar Bashir — what triggered it.
Hemedti's double game — pretending to support protesters.
The precise moment he betrayed Bashir — date, what happened.
How Bashir was arrested and what role RSF played.

KHARTOUM MASSACRE (400 words):
June 3, 2019 — the sit-in massacre outside military headquarters.
Specific confirmed numbers killed, specific time it started.
RSF's confirmed role — documented evidence.
International condemnation that followed.
Survivor testimonies from documented reports.
Why no one was held accountable.

UAE CONNECTION (300 words):
UAE financial support — confirmed figures from investigative reports.
Gold smuggling operations — how it works, documented routes.
Mohamed bin Zayed relationship — documented meetings and deals.
Why UAE supports RSF: specific geopolitical reasons.

COLOMBIAN MERCENARIES (300 words):
Confirmed reports of foreign fighters from Latin America.
Where they were recruited, what organisations confirmed this.
Their documented role in the 2023 war.
International law violations this represents.

CURRENT WAR (200 words):
April 15, 2023 — war start, what triggered it.
Current documented civilian casualties.
Hemedti's confirmed last location.
Is he alive, is he in hiding, what do sources say?

CONCLUSION (100 words):
"The ICC wants him. Multiple governments have sanctioned him.
But Hemedti has not been found.
Follow Dark Crime Decoded for updates
as this story continues to unfold."

TERMINOLOGY — USE EXACTLY AS WRITTEN:
- First mention: "Rapid Support Forces (RSF)" — then "RSF" alone after that
- First mention of Janjaweed: "Janjaweed militia" — then "Janjaweed" alone
- First mention of SAF: "Sudan Armed Forces (SAF)" — then "SAF" alone
- First mention of ICC: "International Criminal Court (ICC)" — then "ICC" alone
- NO documentary exists about RSF — NEVER reference a film or documentary
- This is based on investigative journalism and documented evidence only
- NEVER write "The RSF Documentary" or "The documentary shows" — say "Evidence confirms" or "Reports show"

RULES:
- 1800 words total
- Every sentence = one specific documented fact
- Never state as confirmed what is only alleged — say "according to reports" or "allegedly"
- Write like a serious investigative documentary narrator
- No section labels — spoken words only

Start immediately with the HOOK."""

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75, premium=True)
    words = clean_word_count(result) if result else 0
    print(f"[Script] Hemedti Part 2: {words} real words")
    return result or ""


def _write_documentary_script(topic: dict, research: dict, part_number: int | None = None) -> str:
    """Write a documentary-style script for topics where no movie/series exists."""
    name = topic.get("topic", "")

    # Route Hemedti to dedicated structured prompts
    if _is_hemedti_topic(name):
        if part_number == 2:
            return _write_hemedti_part2(research)
        return _write_hemedti_part1(research)

    # Generic documentary prompt for all other documentary-only topics
    facts = "\n".join(f"- {f}" for f in (
        research.get("research_facts") or research.get("real_facts", [])
    )[:5]) or "(research the documented events)"
    shocking = "\n".join(f"- {s}" for s in (
        research.get("research_shocking") or research.get("shocking_real_facts", [])
    )[:4]) or "(include documented allegations)"

    part_label = f" — Part {part_number}" if part_number else ""
    next_part_teaser = (
        f'\nEnd with: "Part 2 of this story is coming soon on Dark Crime Decoded."'
        if part_number == 1 else
        f'\nEnd with: "Follow Dark Crime Decoded for stories Hollywood has not told yet."'
    )

    _doc_active_entity = build_active_entity(name) if is_single_subject(name) else {}
    _doc_entity_lock   = entity_lock_instruction(_doc_active_entity)

    prompt = f"""You are a documentary scriptwriter covering under-reported world events.
Write a 1800-2000 word documentary script about: {name}{part_label}

This is a DOCUMENTARY style — no movie or series exists for this topic.
{_doc_entity_lock}
VERIFIED FACTS:
{facts}

SHOCKING DOCUMENTED DETAILS:
{shocking}

Use this EXACT structure (spoken words only — no section labels):

HOOK (100 words):
Open with: "While Hollywood has ignored this story..."
Most shocking documented fact about {name}.
Why the world needs to know this story.

BACKGROUND (400 words):
Who is {name} — full background with specific dates.
Rise to power.
Key events that shocked the world.

CRIMES AND ALLEGATIONS (500 words):
Specific documented events and allegations with dates.
International response if any.
Real numbers — victims, scale, evidence.

MYSTERY SECTION (300 words):
Current status of {name} — confirmed information only.
What different sources say.
What the world is watching.

GLOBAL IMPACT (300 words):
How this person affected the region.
International reaction.
What happens next.

WHY NO MOVIE EXISTS (200 words):
"Hollywood has not touched this story yet.
But the real events are more dramatic than any crime movie ever made."
Compare the scale to famous crime movies viewers know.

CONCLUSION (100 words):
Legacy and ongoing impact.{next_part_teaser}

RULES:
- 1800-2000 words total
- Every sentence = one specific documented fact
- Never state as confirmed what is only alleged — say "allegedly" or "accused of"
- No vague phrases — specific dates, numbers, names
- Write like a serious investigative documentary narrator

Start immediately with the HOOK. Spoken words only."""

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75, premium=True)
    words = clean_word_count(result) if result else 0
    print(f"[Script] Documentary script{part_label}: {words} real words")
    return result or ""


def generate_untold_angle(topic: str, series_label: str) -> dict:
    """Generate one specific untold angle/hidden truth for the video topic.

    Returns dict with keys: angle_title, angle_hook, angle_content.
    Falls back to a generic angle if generation fails.
    """
    prompt = f"""For the topic: {topic} (related to: {series_label})

What is ONE specific hidden truth, controversy, or detail that most people missed?
Must be about a specific moment, person, or decision — not general.

⚠️ ENTITY RULE — the angle MUST:
- Be DIRECTLY and SPECIFICALLY about {topic} or {series_label}
- Name a real person, date, or decision from THIS story
- NOT reference unrelated historical figures, crimes from other eras, or other shows
- NOT invent people or events not documented in {topic}'s actual history

Good example formats (adapted to THIS topic):
- "The moment [real person from {topic}] made the decision that changed everything"
- "The [date/year] document about {topic} that was never made public"
- "The real figure behind [character from {series_label}] that the show completely erased"
- "Why [key decision-maker in {topic}'s story] was never charged despite the evidence"

Return JSON only, no extra text:
{{"angle_title": "...", "angle_hook": "...", "angle_content": "..."}}

angle_title: 5-8 words — must name {topic} or a specific figure from this story
angle_hook: One sentence — must contain a real name, date, or fact from {topic}'s documented history
angle_content: 2-3 sentences — all facts must be from {topic}'s actual documented story"""

    try:
        result = _ai_script_call(prompt, max_tokens=350, temperature=0.85, json_mode=True)
        data   = normalize_ai_json_response(
            result,
            required_keys=["angle_title", "angle_hook", "angle_content"],
        )
        if all(data.get(k) for k in ('angle_title', 'angle_hook', 'angle_content')):
            print(f"[Script] Untold angle: {data['angle_title']}")
            return data
        print("[Fallback] Angle generation returned incomplete data — using default angle")
    except Exception as e:
        print(f"[Fallback] Angle generation failed: {e} — using default angle")

    return {
        "angle_title": f"The Hidden Truth Behind {topic}",
        "angle_hook": f"There is one story about {topic} that almost nobody knows.",
        "angle_content": (
            f"The full truth behind {topic} goes far deeper than any show has revealed. "
            f"Documents, interviews, and declassified files tell a story that was never aired."
        ),
    }


def write_long_script_split(topic: dict, research: dict, series_info: tuple | None,
                             angle: dict | None = None) -> str:
    """Write 1,400–1,900 real-word cinematic script via 5 AI calls → 9–12 min runtime."""
    import time

    series = series_info[0] if series_info else topic.get("niche", topic.get("topic", ""))
    stype  = series_info[1] if series_info else "Movie"
    name   = topic.get("topic", "")

    rvf = research.get("real_vs_fiction") or {}
    _real_people_block = ""
    if rvf.get("real_people"):
        lines = [f"  - {p['name']} ({p.get('role','')}, {p.get('era','')})" for p in rvf["real_people"][:6]]
        _real_people_block = "Real people:\n" + "\n".join(lines) + "\n"
    _chars_block = ""
    if rvf.get("fictional_characters"):
        lines = [f"  - {c['name']} (played by {c.get('played_by','?')}) → based on {c.get('based_on','?')}" for c in rvf["fictional_characters"][:6]]
        _chars_block = "Fictional characters and real counterparts:\n" + "\n".join(lines) + "\n"
    _rvs_block = ""
    if rvf.get("real_vs_show"):
        lines = [f"  - {r['aspect']}: Show said '{r.get('show','')}' / Reality was '{r.get('reality','')}'" for r in rvf["real_vs_show"][:4]]
        _rvs_block = "Show vs reality comparisons:\n" + "\n".join(lines) + "\n"
    _time_loc = ""
    if rvf.get("time_period") or rvf.get("real_locations"):
        _time_loc = f"Time period: {rvf.get('time_period','')}\nLocations: {', '.join(rvf.get('real_locations',[]))}\n"

    # Build show_characters block from research (populated by research_agent Step 0)
    _show_chars = research.get("show_characters") or []
    _is_show_topic = research.get("is_show_topic", False) or bool(_show_chars)
    _show_chars_block = ""
    _ch3_mandatory = ""
    _ch4_mandatory = ""
    if _show_chars:
        sc_lines = [
            f"  - {c['character']} (played by {c.get('actor','?')}) → real person: {c.get('based_on','?')} — {c.get('real_role','')}"
            for c in _show_chars
        ]
        _show_chars_block = "SHOW CAST — compare EACH character below to their real counterpart:\n" + "\n".join(sc_lines) + "\n"
        real_names = ", ".join(
            c.get('based_on','') for c in _show_chars
            if c.get('based_on') and c.get('based_on','').lower() not in ('null','none','various','')
        )
        # Ch3: cover REAL PEOPLE only — no fictional character names in the historical narrative
        if real_names:
            _ch3_mandatory = (
                f"\nMANDATORY — REAL PEOPLE TO COVER IN THIS CHAPTER:\n"
                f"Give each of these real people at least one full paragraph about their actual history:\n"
                f"{real_names}\n"
                f"⚠️ Do NOT use fictional character names in this chapter. "
                f"Fictional character names belong ONLY in the Show vs Reality chapter.\n"
            )
        # Ch4: character-by-character show vs reality — fictional names allowed ONLY here
        char_names = ", ".join(c['character'] for c in _show_chars)
        _ch4_mandatory = (
            f"\nMANDATORY — CHARACTER-BY-CHARACTER COMPARISON (fictional names ONLY allowed in THIS chapter):\n"
            f"{_show_chars_block}"
            f"For each character: state what the show depicted, then what the documented record shows.\n"
        )

    # ── Era / geography lock (prevents cross-era contamination) ──────────────
    _era_lock = ""
    if rvf.get("time_period") or rvf.get("real_locations"):
        _era_lock = (
            f"\n⚠️ ERA & GEOGRAPHY LOCK — this story is set in:\n"
            f"  Time period: {rvf.get('time_period', 'as per research')}\n"
            f"  Locations: {', '.join(rvf.get('real_locations', []) or [])}\n"
            f"STRICT: All entities and events MUST belong to this era and geography. "
            f"Do NOT introduce figures, events, or organisations from different eras or unrelated regions.\n"
        )

    # _topic_context for HISTORICAL chapters (Ch1, Ch2, Ch3, Ch5) — NO fictional characters
    _topic_context = (
        f"Topic: {name}\n"
        f"Series/Movie: {series} ({stype})\n"
        f"Real person: {research.get('real_person', name)}\n"
        f"Key facts: {(research.get('research_facts') or research.get('what_show_got_right', []))[:3]}\n"
        f"{_real_people_block}{_time_loc}{_era_lock}"
    )

    # _ch4_context for Show vs Reality — adds fictional character mapping + rvs comparisons
    _ch4_fiction_block = ""
    if _chars_block or _show_chars_block or _rvs_block:
        _ch4_fiction_block = (
            f"\n--- FICTIONAL CHARACTERS (these are SHOW characters — NOT historical figures) ---\n"
            f"{_chars_block}"
            f"⚠️ The names above are fictional. Use them ONLY in this chapter.\n"
            f"{_rvs_block}"
        )
    _ch4_context = _topic_context + _ch4_fiction_block

    # ── Entity guard setup ────────────────────────────────────────────────────
    _active_entity  = build_active_entity(name) if is_single_subject(name) else {}
    _entity_lock    = entity_lock_instruction(_active_entity)

    base_context = _topic_context  # kept for any legacy references

    # Resolve angle — use passed-in angle or generate one now
    _angle = angle or generate_untold_angle(name, f"{series} {stype}")
    _angle_title   = _angle.get("angle_title", f"The Hidden Truth Behind {name}")
    _angle_hook    = _angle.get("angle_hook", "")
    _angle_content = _angle.get("angle_content", "")

    # ── Story variation profile — prevents formula fatigue ───────────────────
    _vp = _pick_variation_profile(name, series)
    _variation_header = (
        f"\n🎬 NARRATIVE STYLE: {_vp['name'].upper().replace('_', ' ')}\n"
        f"Style description: {_vp['description']}\n"
        f"Opening approach: {_vp['opening_style']}\n"
        f"Act emphasis: {_vp['act_emphasis']}\n"
        f"Apply this style rhythm to this act — stay within the scene chain, bring this energy.\n"
    )
    print(f"[NARRATIVE] Variation profile: {_vp['name']} — {_vp['description'][:60]}…")

    # ── Editorial-assist mode — sensitive topic framing ───────────────────────
    _risk_info       = topic.get("risk_info", {})
    _risk_level      = _risk_info.get("risk_level", "LOW")
    _editorial_mode  = _risk_info.get("editorial_mode", False)
    _editorial_block = ""
    if _editorial_mode:
        try:
            from agents.topic_risk import get_editorial_assist_prompt
            _editorial_block = get_editorial_assist_prompt(_risk_level, name)
            print(f"[RISK] Editorial-assist mode ACTIVE — risk={_risk_level}")
        except Exception:
            pass

    # ── 5-ACT NARRATIVE FLOW ENGINE — story progression phases, not documentary chapters ──
    # Each act = a distinct STORY STATE SHIFT. Viewer must feel: story is MOVING, not explained.
    # Act state chain: UNEASE → INVESTIGATION → ESCALATION → COLLAPSE → AFTERMATH
    # Target totals: 280+300+380+280+180 = 1,420 words → ~9 min at 160 WPM
    # Max totals:    380+400+500+380+240 = 1,900 words → ~12 min at 160 WPM
    _SECTIONS_META = [
        ("Act 1 — Unease & First Clue",    280, 380,  False),
        ("Act 2 — Investigation Begins",   300, 400,  False),
        ("Act 3 — Escalation",             380, 500,  False),
        ("Act 4 — Collapse & Exposure",    280, 380,  False),
        ("Act 5 — Aftermath & Legacy",     180, 240,  True),
    ]

    _SECTION_LABELS = [
        "[SECTION: Act 1 — Unease & First Clue]",
        "[SECTION: Act 2 — Investigation Begins]",
        "[SECTION: Act 3 — Escalation]",
        "[SECTION: Act 4 — Collapse & Exposure]",
        "[SECTION: Act 5 — Aftermath & Legacy]",
    ]

    def _section_instruction(min_w: int, max_w: int, is_final: bool) -> str:
        conclude = (
            "This is the final section — deliver the last revelation, then close with weight. "
            "Call to action for viewers. End with a thought that stays with them."
            if is_final else
            "Do NOT summarize or conclude — the next section continues the story. "
            "End on a tension beat: a clue unresolved, a question unanswered, a consequence pending."
        )
        return (
            f"Write exactly {min_w}–{max_w} real words for this section. "
            "Runtime must come from STORY MOVEMENT — new scenes, new beats, new reveals. "
            "Every 50-80 words must contain a story beat: clue, twist, reveal, turn, or consequence. "
            "NEVER pad with repeated biography, atmosphere, or summaries of prior sections. "
            + conclude
        )

    def _call_section(prompt: str, label: str, min_w: int, max_w: int,
                      call_num: int) -> str | None:
        # Larger sections (700-1200 words each) require more output tokens
        _max_tok = 1600 if call_num == 5 else 2400
        result = _ai_script_call(prompt, max_tokens=_max_tok,
                                  system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
        if not result:
            print(f"[Script] Section {call_num} ({label}): call failed")
            return None
        real  = clean_word_count(result)
        raw   = len(result.split())
        emoji = "✅" if real >= min_w else "⚠️"
        print(f"[Script] Section {call_num} ({label}): {real} real words {emoji} "
              f"(target {min_w}–{max_w}, raw {raw})")

        # If below minimum: targeted expansion instead of full section regeneration.
        # expand_section() sends only the existing text + a short gap-fill instruction,
        # replacing the old 3-attempt retry + continuation loop (3-4x cheaper).
        if real < min_w:
            missing = min_w - real
            print(f"[Script] Section {call_num}: {real}w < {min_w}w — "
                  f"expanding by ~{missing} words via expand_section")
            expanded = expand_section(result, missing)
            exp_real = clean_word_count(expanded)
            if exp_real > real:
                result, real = expanded, exp_real
                emoji = "✅" if real >= min_w else "⚠️"
                print(f"[Script] Section {call_num} after expansion: {real} real words {emoji}")
            else:
                print(f"[Script] Section {call_num}: expansion unchanged — keeping {real}w")

        # Hard cap per section to stop runaway outputs from pushing total runtime.
        if real > max_w:
            result = _trim_plain_text_to_words(result, max_w)
            print(f"[Script] Section {call_num} trimmed to max {max_w} words")

        # Entity contamination guard — sanitise & warn; do not block on single mentions
        if _active_entity:
            passed, offending = validate_entity_consistency(result, _active_entity)
            if not passed:
                result = sanitize_script(result, _active_entity)
                if offending:
                    print(f"[EntityGuard] Section {call_num} sanitised — "
                          f"{len(offending)} offending line(s) removed")

        # Information density audit — remove filler, rewrite on LOW density
        try:
            from agents.script_quality import validate_information_density, remove_filler_phrases
            _density = validate_information_density(result, language="english")
            _dv      = _density.get("verdict", "?")
            _dp      = _density.get("density_pct", 0)
            _fc      = _density.get("filler_count", 0)
            print(
                f"[Density] Section {call_num} ({label}): "
                f"{_dp:.0f}% informative [{_dv}] | filler={_fc} | "
                f"fragments={_density.get('fragment_count',0)}"
            )
            if _fc > 0:
                result = remove_filler_phrases(result)
            if _dv == "LOW":
                print(f"[Density] Section {call_num} LOW — requesting high-density rewrite")
                _dense_prompt = (
                    "The following script section has too much vague atmosphere and not enough "
                    "facts, names, or dates. Rewrite it with maximum information density.\n\n"
                    "RULES: Every 50 words must contain at least one of: "
                    "specific name + action, year/date, piece of evidence, forensic/legal detail, "
                    "or investigative development. No filler phrases. No suspense atmosphere without facts.\n\n"
                    f"Section to rewrite:\n{result}\n\n"
                    "Return the rewritten section only."
                )
                _dense_result = _ai_script_call(
                    _dense_prompt, max_tokens=_max_tok,
                    system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True,
                )
                if _dense_result and clean_word_count(_dense_result) >= int(min_w * 0.75):
                    result = _dense_result
                    print(f"[Density] Section {call_num} rewritten: {clean_word_count(result)}w")
                else:
                    print(f"[Density] Section {call_num} rewrite skipped — insufficient output")
        except Exception as _de:
            print(f"[Density] Section {call_num} check (non-fatal): {_de}")

        return result

    sections: list[str] = []
    prompts_ctx: list[str] = []  # accumulate previous sections for context

    import random as _random

    def _used_facts_block(n: int) -> str:
        """List key sentences already used in sections 0..n-1 as explicit prohibitions."""
        if not sections:
            return ""
        items = []
        for idx, sec in enumerate(sections[:n]):
            sents = [s.strip() for s in sec.replace("\n", " ").split(". ") if len(s.strip()) > 40]
            for sent in sents[:5]:
                items.append(f"- {sent}.")
        if not items:
            return ""
        return (
            "⛔ ALREADY COVERED — do NOT restate, paraphrase, or re-introduce any of the following "
            "(these facts appeared in earlier chapters and must never appear again):\n"
            + "\n".join(items)
            + "\nEvery sentence in your chapter must introduce information that has NOT appeared above."
        )

    # ── Master outline: pre-assign unique facts to each chapter ──────────────
    def _generate_master_outline() -> dict:
        """One AI call that locks 10–15 unique facts to specific chapters before writing starts."""
        _rf = (research.get("research_facts") or research.get("what_show_got_right") or [])[:8]
        _sh = (research.get("research_shocking") or research.get("shocking_real_facts") or [])[:4]
        _in = (research.get("research_inaccuracies") or research.get("what_show_got_wrong") or [])[:4]
        _outline_prompt = f"""You are building a SCENE BEAT MAP for a cinematic crime story about: {name} (related to {series} {stype}).

Task: Distribute 10–15 unique specific story beats across exactly 5 acts.
Each beat must appear in EXACTLY ONE act — never repeated elsewhere.
Every beat must be SPECIFIC: include a real name, date, number, or documented event.

RESEARCH MATERIAL:
Facts: {_rf}
Shocking details: {_sh}
Show inaccuracies: {_in}

HIDDEN ANGLE (reserved for Act 2):
Title: {_angle_title}
Detail: {_angle_content}

Assign story beats to acts using these STRICT story-state roles:
- act1: 2 beats — (a) the specific real-life opening scene (the real moment behind what {series} made famous); (b) the first documented contradiction that creates unease
- act2: 3–4 beats — investigation launch trigger, first evidence found, the hidden angle detail, one witness or informant beat
- act3: 4–5 beats — escalation events each with a year/date; the biggest documented lie {series} told; investigation pressure or key mistake
- act4: 3–4 beats — arrest/capture scene; confession or key statement; key courtroom testimony; the show's final romanticization exposed
- act5: 2 beats — documented aftermath (what happened to key figures); the final haunting legacy fact

Return ONLY valid JSON, no explanation:
{{"act1": ["...", "..."], "act2": ["...", "...", "..."], "act3": ["...", "...", "...", "..."], "act4": ["...", "...", "..."], "act5": ["...", "..."]}}"""

        try:
            raw  = _ai_script_call(_outline_prompt, max_tokens=900, json_mode=True, temperature=0.4)
            data = normalize_ai_json_response(
                raw,
                required_keys=["act1", "act2", "act3", "act4", "act5"],
                list_keys=["act1", "act2", "act3", "act4", "act5"],
            )
            if any(data.get(k) for k in ("act1", "act2", "act3", "act4", "act5")):
                total = sum(len(data.get(k) or []) for k in ("act1","act2","act3","act4","act5"))
                print(f"[Script] Scene beat map: {total} unique beats pre-assigned across 5 acts")
                return data
            print("[Fallback] Scene beat map returned empty — proceeding without pre-assignment")
        except Exception as e:
            print(f"[Fallback] Scene beat map failed ({e}) — proceeding without pre-assignment")
        return {}

    _outline = _generate_master_outline()
    time.sleep(2)  # brief pause before chapter writing begins

    def _facts_block(act_key: str) -> str:
        """Inject pre-assigned story beats as a scene-writing directive for one act."""
        facts = _outline.get(act_key, [])
        if not facts:
            return ""
        lines = "\n".join(f"  • {f}" for f in facts)
        return (
            f"📋 YOUR PRE-ASSIGNED STORY BEATS — write scenes around these specific points "
            f"(reserved exclusively for this act; do not appear in any other act):\n"
            f"{lines}\n"
            "Anchor every scene to this list. Do NOT introduce other events from the research."
        )

    section_prompts = [
        # ── ACT 1: Unease & First Clue ────────────────────────────────────────
        lambda: f"""{_topic_context}{_entity_lock}{_variation_header}{_editorial_block}
Write ACT 1 — UNEASE & FIRST CLUE for a cinematic crime story about {name}.

STORY STATE THIS ACT: NORMAL WORLD → FIRST CRACK OF UNEASE.
The viewer does not yet know the full story. The crack appears.

SCENE CHAIN — write each as a SCENE, not an explanation:

Scene 1 (Opening): Begin at ONE specific documented real-world moment. Not an introduction — a scene. Real place, real year, real person caught in an action. 3-4 sentences. Put the viewer inside it.

Scene 2 (The Famous Version vs Reality): Cut to {series} — what millions saw. One specific scene the show is famous for. 2-3 sentences maximum. Then the pivot: "The real story was different."

Scene 3 (First Contradiction): The specific documented detail that doesn't match. The clue that was there from the start. Name it. Give it a year if documented.

Scene 4 (Unease Builds): The first real warning sign nobody took seriously. One documented moment — what happened, what it meant, why nobody acted. End on an unresolved question that forces the viewer forward.

{_facts_block("act1")}

STRICT SCOPE — this act does NOT cover:
- Real history or biography in depth (that is Act 3)
- The full investigation (that is Act 2)
- Show-vs-reality comparisons in depth (those are Acts 3 and 4)
Plant the hook and the crack. Nothing more.

NARRATION RULES:
- First sentence: a specific scene, not a statement. Under 12 words.
- Mix short punches (5-10 words) with narrative sentences (15-22 words).
- No generic openers ("In a world…", "Throughout history…").

Write flowing cinematic narration — no lists, no bullet points. Minimum 3 sentences per paragraph.
{_section_instruction(700, 900, False)}""",

        # ── ACT 2: Investigation Begins ───────────────────────────────────────
        lambda: f"""{_topic_context}{_entity_lock}
Write ACT 2 — INVESTIGATION BEGINS for a cinematic crime story about {name}.

STORY STATE THIS ACT: CRACK → INVESTIGATION LAUNCHED → FIRST DISCOVERIES.
Something has been found, reported, or triggered. The machinery of discovery starts moving.

SCENE CHAIN:

Scene 1 (Trigger): The specific documented event, report, or discovery that launched the investigation. Real date and person where possible. What happened, who responded, what they found.

Scene 2 (Hidden Angle — OPEN WITH EXACTLY): {_angle_hook}
Expand this into a full 2-3 paragraph investigation scene: {_angle_content}
This is {_angle_title} — the thing {series} never showed.

Scene 3 (First Evidence): The first piece of documented evidence or witness account. Specific — a name, a location, a date. What investigators thought it meant.

Scene 4 (The Lead That Was Missed): A documented investigation mistake, missed clue, or wrong turn. What slipped through. Why. The consequence of that mistake.

{_facts_block("act2")}

STRICT SCOPE — this act does NOT:
- Re-introduce or describe the show's famous scenes (Act 1 handled that)
- Tell the full chronological real history (Act 3 does that)
- Compare multiple show scenes to reality (Act 4 does that)
Every sentence must advance the investigation narrative.

{_used_facts_block(1)}

Write flowing cinematic narration — no lists, no bullet points. Minimum 3 sentences per paragraph.
{_section_instruction(750, 950, False)}

PREVIOUS ACT (context — do NOT repeat):
{sections[0]}""",

        # ── ACT 3: Escalation ─────────────────────────────────────────────────
        lambda: f"""{_topic_context}{_ch3_mandatory}{_entity_lock}
Write ACT 3 — ESCALATION for a cinematic crime story about {name}.

STORY STATE THIS ACT: INVESTIGATION UNDERWAY → PRESSURE BUILDS → STORY EXPLODES.
New facts emerge. Contradictions multiply. The real story becomes bigger and darker.

SCENE CHAIN:

Scene 1 (First Escalation Event): A new documented event that changed everything. New victim, new evidence, new development — with a specific date.

Scene 2 (What {series} Hid): The biggest documented difference between {series} and reality. Write it as a revelation that unfolds in the narrative — not a comparison list: "Here is what {series} showed. Here is what was actually happening."

Scene 3 (Real People — one scene each): For every named real person relevant to this act: one paragraph of documented history, one specific action they took, one consequence. Do NOT use fictional character names here.

Scene 4 (Investigation Pressure): Investigators closing in — or missing crucial leads. Documented public fear, media pressure, institutional failure. Be specific.

Scene 5 (Major Twist or Setback): A documented turning point that reframes the whole story. Something that shocked investigators, the public, or the system. End this act at maximum pressure — about to collapse.

{_facts_block("act3")}

STRICT SCOPE:
- Do NOT re-describe what the show is about (Acts 1–2 established that)
- Do NOT re-state the hidden angle from Act 2 (already covered)
- Do NOT deliver the capture or verdict (Act 4 does that)

{_used_facts_block(2)}

Write flowing cinematic narration — no lists, no bullet points. Mix short punches with narrative sentences.
{_section_instruction(950, 1200, False)}

PREVIOUS ACTS (context — do NOT repeat):
{sections[0]}

{sections[1]}""",

        # ── ACT 4: Collapse & Exposure ────────────────────────────────────────
        lambda: f"""{_ch4_context}{_ch4_mandatory}{_entity_lock}
Write ACT 4 — COLLAPSE & EXPOSURE for a cinematic crime story about {name}.

STORY STATE THIS ACT: EVERYTHING COLLAPSES → TRUTH EXPOSED → VERDICT LANDS.
The arrest. The confession. The courtroom. Each as a documented scene, not a summary.

SCENE CHAIN:

Scene 1 (Capture): The documented arrest, confrontation, or turning point. Where it happened. Who was there. What was said. Write the scene.

Scene 2 (Confession or Key Statement): What is on record about the suspect's statements. Real documented quotes or known paraphrases. What it revealed that nobody expected.

Scene 3 (Courtroom): Key documented testimony. The prosecutor's case. Defense moves. What came out in court that shocked everyone — the thing that was never in {series}.
Fictional character names from the show (listed above) may be used HERE in comparisons.

Scene 4 (Show's Truth Exposed):
Start with EXACTLY: "Here is what {series} got RIGHT:" (2-3 specific things the show accurately depicted)
Then EXACTLY: "Here is what they completely changed or left out:" (2-3 specific things changed)
Each comparison must be a story beat — what the show showed, what actually happened, why it matters.

{_facts_block("act4")}

STRICT SCOPE:
- Do NOT re-tell the chronological real history (Act 3 covered that)
- Do NOT repeat investigation details from Acts 2–3
Every comparison must introduce NEW story details not yet stated in Acts 1–3.

{_used_facts_block(3)}

Write flowing cinematic narration — minimum 3 sentences per paragraph.
{_section_instruction(700, 900, False)}

PREVIOUS ACTS (context — do NOT repeat):
{sections[0]}

{sections[1]}

{sections[2]}""",

        # ── ACT 5: Aftermath & Legacy ─────────────────────────────────────────
        lambda: f"""{_topic_context}{_entity_lock}
Write ACT 5 — AFTERMATH & LEGACY for a cinematic crime story about {name}.

STORY STATE THIS ACT: VERDICT DELIVERED → ECHOES REMAIN → STORY NEVER FULLY ENDS.
Not a summary. Not a recap. The consequences, the unanswered questions, the lasting weight.

SCENE CHAIN:

Scene 1 (Immediate Aftermath): What happened to the key people. Fates, sentences, disappearances, reinventions. Write as scenes — who went where, what happened to them, what remained unresolved.

Scene 2 (The Unanswered Question): The documented gap that remains. What was never proven. What was never found. What the case files show was left open.

Scene 3 (What This Story Reveals): One paragraph connecting {name}'s story to something larger — power, corruption, the gap between what we are shown and what is real.

Scene 4 (Close): End with exactly: "Follow Dark Crime Decoded for more real stories behind your favourite crime series and films."

{_facts_block("act5")}

STRICT SCOPE:
- Do NOT recap or summarize Acts 1–4
- Do NOT repeat ANY fact from earlier acts (see fence below)
- Do NOT use phrases like "In conclusion", "To summarize", "As we have seen", "As we explored"

{_used_facts_block(4)}

Opening: delivered like a verdict — short, direct, no softening.
Aftermath: scenes not summaries.

CRITICAL: End with a fully complete sentence. Never end mid-thought.
Write flowing cinematic narration — no lists, no bullet points.
{_section_instruction(400, 500, True)}

PREVIOUS ACTS (context — do NOT repeat):
{sections[0]}

{sections[1]}

{sections[2]}

{sections[3]}""",
    ]

    for i, (label, min_w, max_w, is_final) in enumerate(_SECTIONS_META):
        prompt = section_prompts[i]()
        result = _call_section(prompt, label, min_w, max_w, i + 1)
        if not result:
            return ""
        sections.append(result)
        if i < len(_SECTIONS_META) - 1:
            time.sleep(3)

    full_script = "\n\n".join(
        f"{_SECTION_LABELS[i]}\n{section}"
        for i, section in enumerate(sections)
    )

    total_real = clean_word_count(full_script)
    total_raw  = len(full_script.split())
    minutes    = total_real / 163  # ~163 wpm for documentary English narration
    print(f"[Script] Total English: {total_real} real words (raw {total_raw}) "
          f"→ Est. runtime: ~{minutes:.0f} min")

    # ── Narrative flow audit — story progression metrics ─────────────────────
    _spv         = scene_progression_validator(full_script)
    _stall_count = len(_spv["stalled_zones"])
    _prog_score  = _spv["progression_score"]
    # Repetition clusters: count repeated 4+ word phrases (proxy via detect_quality_issues)
    _rep_clusters = 0
    try:
        from agents.script_quality import detect_quality_issues as _dqi
        _rep_clusters = len(_dqi(full_script).get("repeated_phrases", []))
    except Exception:
        pass
    # momentum_score: blend of progression_score and inverse-repetition penalty
    _momentum_score = round(_prog_score * max(0.5, 1.0 - _rep_clusters * 0.03), 2)
    print(
        f"[NARRATIVE] profile={_vp['name']} | "
        f"dead_zones={_stall_count} | "
        f"repetition_clusters={_rep_clusters} | "
        f"momentum_score={_momentum_score:.0%} | "
        f"state_changes={len(sections)}"
    )
    if _spv["has_stalls"]:
        print(f"[NARRATIVE] ⚠️ Momentum stalls detected ({_stall_count} zones):")
        for _z in _spv["stalled_zones"][:4]:
            print(f"  {_z}")
        # Soft repair — rewrite up to 2 stalled zones; never blocks if repair fails
        _stalled_texts = _spv.get("stalled_chunks", [])[:2]
        _repairs = 0
        for _sz_text in _stalled_texts:
            if _sz_text and len(_sz_text.split()) >= 150:
                _repaired = rewrite_dead_zone(_sz_text, topic_name=name)
                if _repaired is not _sz_text and _repaired != _sz_text:
                    full_script = full_script.replace(_sz_text, _repaired, 1)
                    _repairs += 1
                    print(f"[NARRATIVE] Dead zone repaired: "
                          f"{len(_sz_text.split())}w → {len(_repaired.split())}w")
        if _repairs:
            print(f"[NARRATIVE] Soft repair complete — {_repairs} zone(s) rewritten")
    else:
        print(f"[NARRATIVE] ✅ Story progression healthy — no dead zones")

    if total_real < LONG_SCRIPT_MIN_WORDS:
        print(f"[Script] ⚠️ RUNTIME BELOW 15-MIN FLOOR: {total_real} words (~{minutes:.0f} min) — "
              f"minimum is {LONG_SCRIPT_MIN_WORDS:,}w ({LONG_SCRIPT_MIN_WORDS//155}+ min). "
              f"Triggering runtime expansion with new story scenes.")
        missing = LONG_SCRIPT_MIN_WORDS - total_real
        full_script = expand_script_runtime(full_script, missing, topic=name)
        total_real = clean_word_count(full_script)
        minutes = total_real / 155
        print(f"[Script] After expansion: {total_real} words (~{minutes:.0f} min)")
    elif total_real > LONG_SCRIPT_MAX_WORDS:
        # Safety valve only — extremely rare; cap at ~90 min to prevent runaway generation
        print(f"[Script] Safety cap: {total_real}w > {LONG_SCRIPT_MAX_WORDS}w — trimming to ~90 min")
        full_script = _cap_script_max_words(full_script, LONG_SCRIPT_MAX_WORDS)

    # ── Quality summary + density audit ──────────────────────────────────────
    try:
        from agents.script_quality import (
            detect_quality_issues, validate_timeline_consistency,
            validate_information_density, remove_filler_phrases,
        )
        # Remove filler phrases from final assembled script
        full_script = remove_filler_phrases(full_script)

        _qi  = detect_quality_issues(full_script)
        _era = rvf.get("time_period", "") if rvf else ""
        _tl  = validate_timeline_consistency(full_script, topic=name, expected_era=_era)
        _filler = _qi.get("filler_count", 0)
        _rep    = len(_qi.get("repeated_phrases", []))

        _density = validate_information_density(full_script, language="english")
        print(
            f"[Quality] SUMMARY — words: {_qi.get('word_count',0)} | "
            f"filler: {_filler} | repeats: {_rep} | "
            f"density: {_density.get('density_pct',0):.0f}% [{_density.get('verdict','?')}] | "
            f"timeline OK: {_tl.get('consistent',True)}"
        )
        if _density.get("verdict") == "LOW":
            print(f"[Quality] WARNING: Low information density — {_density.get('filler_count',0)} filler sentences detected")
        if _filler:
            print(f"[Quality] Filler phrases found: {_qi.get('filler_phrases',[])[:3]}")
        if not _tl.get("consistent", True):
            print(f"[Quality] Timeline violations — "
                  f"fiction bleed: {_tl.get('fiction_bleed',[])} | "
                  f"cross-topic: {_tl.get('cross_topic',[])} | "
                  f"blocked entities: {_tl.get('contamination',[][:3])}")
    except Exception as _qe:
        print(f"[Quality] Summary check failed (non-fatal): {_qe}")

    return full_script


def write_ultra_long_script(topic_name: str, research: dict,
                             series_info: tuple | None, part_number: int = 1) -> str:
    """Write 4000-5000 word script via 6 separate OpenAI calls (target ~14-17 min EN, ~16-19 min AR)."""
    import time

    series = series_info[0] if series_info else "Documentary"
    stype  = series_info[1] if series_info else "Documentary"

    base = f"""Topic: {topic_name}
Series/Movie: {series} ({stype})
Research facts: {(research.get('research_facts') or research.get('real_facts', []))[:5]}
Network: {research.get('network', 'unknown')}
Real person: {research.get('real_person', topic_name)}
Shocking facts: {(research.get('research_shocking') or research.get('shocking_real_facts', []))[:3]}
"""

    sections: list[str] = []

    # SECTION 1 — Hook + Series Intro + Real Background (800 words)
    print("[Script] Writing Section 1/6...")
    s1 = _ai_script_call(f"""{base}
Write SECTION 1 of a true crime documentary. Exactly 800 words.

HOOK (100 words):
Most shocking single fact to open with.
Start with specific date/number/event.
Make viewer unable to stop watching.

SERIES INTRO (250 words):
What {series} showed the world.
Why millions watched it.
Specific scenes that captivated audiences.
Celebrate the show then build excitement:
"But the real story is even more extraordinary..."

REAL BACKGROUND OPENING (450 words):
Who was {topic_name} before everything happened.
Family background with specific details.
Childhood and early life.
First signs of what was to come.
Specific dates and places.

RULES:
- Exactly 800 words
- Every sentence has one specific fact
- No two consecutive sentences start same word
- Write like Netflix documentary narrator
- Dramatic but factual
""", max_tokens=1200, system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
    if s1:
        sections.append(s1)
        print(f"[Script] S1: {clean_word_count(s1)} real words")
    time.sleep(3)

    # SECTION 2 — Early Life + Rise to Power (800 words)
    print("[Script] Writing Section 2/6...")
    s2 = _ai_script_call(f"""{base}
Write SECTION 2 of a true crime documentary. Exactly 800 words. Continue from early life.
DO NOT repeat anything from Section 1.

EARLY CRIMINAL LIFE (400 words):
First involvement in crime.
Specific year and circumstances.
Who recruited or influenced them.
Early crimes with specific details.
How they built initial power/wealth.

RISE TO POWER (400 words):
Key events that accelerated their rise.
Specific dates when major milestones happened.
People who helped or were betrayed.
First major crime or atrocity.
How ordinary people saw them then.

RULES:
- Exactly 800 words
- New information only — no repetition
- Specific dates numbers names places
""", max_tokens=1200, system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
    if s2:
        sections.append(s2)
        print(f"[Script] S2: {clean_word_count(s2)} real words")
    time.sleep(3)

    # SECTION 3 — Main Story + Turning Point (900 words)
    print("[Script] Writing Section 3/6...")
    s3 = _ai_script_call(f"""{base}
Write SECTION 3 of a true crime documentary. Exactly 900 words. The main events.
DO NOT repeat anything from previous sections.

MAIN STORY — PEAK POWER (450 words):
At height of their power what happened.
Most significant events chronologically.
Real victims and real impact.
Specific operations or crimes.
International attention and response.

TURNING POINT (450 words):
The moment everything started to change.
Key event that led to downfall or exposure.
How law enforcement/international community responded.
Specific date when the world noticed.
Real people who fought against them.

RULES:
- Exactly 900 words
- Chronological order with years
- Every paragraph = new information
- Include 10+ specific dates or numbers
""", max_tokens=1400, system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
    if s3:
        sections.append(s3)
        print(f"[Script] S3: {clean_word_count(s3)} real words")
    time.sleep(3)

    # SECTION 4 — Shocking Revelations + International Connections (800 words)
    print("[Script] Writing Section 4/6...")
    s4 = _ai_script_call(f"""{base}
Write SECTION 4 of a true crime documentary. Exactly 800 words. Shocking facts.
DO NOT repeat anything from previous sections.

SHOCKING REVELATIONS (400 words):
5 facts most people never knew.
Information hidden from public.
Connections that were never reported.
Financial crimes or secret deals.
What happened behind the scenes.

INTERNATIONAL CONNECTIONS (400 words):
Foreign governments or organizations involved.
Money flows and financial networks.
How they escaped justice so long.
Who protected them and why.
Documents or evidence that exists.

RULES:
- Exactly 800 words
- Facts that would shock even informed viewers
- Cite specific sources: ICC, UN, journalists
- No speculation — only documented facts
""", max_tokens=1200, system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
    if s4:
        sections.append(s4)
        print(f"[Script] S4: {clean_word_count(s4)} real words")
    time.sleep(3)

    # SECTION 5 — Series vs Reality OR Evidence (1000 words)
    print("[Script] Writing Section 5/6...")
    is_documentary = get_script_angle(topic_name, series_info) == "documentary"
    if is_documentary:
        s5_prompt = f"""{base}
Write SECTION 5 of a true crime documentary. Exactly 800 words. Evidence and investigation.
DO NOT repeat anything from previous sections.

EVIDENCE AND PROOF (400 words):
ICC warrant details and specific charges.
UN investigation findings with dates.
Survivor testimonies — what they described.
Leaked documents or communications.
Journalists killed or arrested covering this.

CURRENT STATUS (400 words):
Where is {topic_name} now?
Last confirmed sighting with date.
What different sources report.
International manhunt details.
What justice looks like for victims.

RULES:
- Exactly 800 words
- Only documented confirmed facts
- Cite sources: ICC, UN, Human Rights Watch
- Respectful of victims
"""
    else:
        s5_prompt = f"""{base}
Write SECTION 5 of a true crime documentary. Exactly 800 words. Real vs Screen comparison.
DO NOT repeat anything from previous sections.

REAL STORY VS {series} (400 words):
Direct comparisons:
"In {series}, they showed X. In reality Y happened."
3-4 specific scene comparisons.
What the {stype} got right — celebrate accuracy.
What was changed for drama — explain why filmmakers chose this.

WHAT THE {stype.upper()} LEFT OUT (400 words):
Key real events not in the {stype}.
Real people not shown or renamed.
Timeline changes and why.
Most dramatic real moment not depicted.
What sequel could cover.

RULES:
- Exactly 800 words
- Specific scene references
- Respectful of filmmakers' creative choices
"""
    s5 = _ai_script_call(s5_prompt, max_tokens=1200, system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
    if s5:
        sections.append(s5)
        print(f"[Script] S5: {clean_word_count(s5)} real words")
    time.sleep(3)

    # SECTION 6 — Conclusion (500 words)
    print("[Script] Writing Section 6/6...")
    s6 = _ai_script_call(f"""{base}
Write SECTION 6 — THE CONCLUSION of a true crime documentary. Exactly 500 words.
DO NOT repeat anything from previous sections.

AFTERMATH (250 words):
What happened after the main events.
Where key people are now.
Justice served or denied — specific outcomes.
Impact on victims families today.
Legacy of this case on history.

FINAL REFLECTION (150 words):
Why this story matters today.
What it teaches about power and corruption.
Connection to current world events.
Why people need to know this story.

CTA (100 words):
"The story of {topic_name} is far from over..."
Tease what Part 2 will cover (if part 1).
"Follow Dark Crime Decoded for more real stories
that change how you see the world."
Strong emotional ending.

RULES:
- Exactly 500 words
- Emotional but factual ending
- Strong memorable final line
""", max_tokens=800, system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
    if s6:
        sections.append(s6)
        print(f"[Script] S6: {clean_word_count(s6)} real words")

    # Expand any section that fell below its minimum threshold
    SECTION_MINS = [600, 600, 700, 600, 600, 400]
    for i, section in enumerate(sections):
        min_w = SECTION_MINS[i] if i < len(SECTION_MINS) else 400
        if clean_word_count(section) < min_w:
            print(f"[Script] Expanding section {i + 1} (below {min_w} word min)...")
            expanded = _ai_script_call(
                f"Expand this section to minimum {min_w} words. "
                f"Add more specific facts, dates, storytelling. "
                f"Keep same topic and style.\n\n{section}",
                max_tokens=1200,
                system_prompt=_SCRIPT_SYSTEM_PROMPT,
                premium=True,
            )
            if expanded and clean_word_count(expanded) > clean_word_count(section):
                sections[i] = expanded

    full_script = "\n\n".join(sections)
    total_words = clean_word_count(full_script)
    total_minutes = total_words / 130
    if total_words < 3000:
        print(f"[Script] ❌ Too short: {total_words} words (minimum 3000)")
    elif total_words < 4000:
        print(f"[Script] ✅ Good: {total_words} words = ~{total_minutes:.0f} min")
    else:
        print(f"[Script] ✅ Excellent: {total_words} words = ~{total_minutes:.0f} min")
    return full_script


def _emergency_backup_script(topic_name: str, series_label: str) -> str:
    """
    Generate a minimal locally-built script when all AI providers have failed.
    Returns enough content for the pipeline to produce a video without crashing.
    Logs clearly so the operator knows a backup was used.
    """
    print(f"[Fallback] Local emergency script used for: {topic_name}")
    return (
        f"[SECTION: Introduction]\n"
        f"The true story behind {topic_name} is one of the most remarkable in crime history. "
        f"{series_label} brought this story to millions of viewers. "
        f"But the real events were far more complex than any screen adaptation could capture. "
        f"Tonight, we explore what really happened.\n\n"

        f"[SECTION: The Real Story]\n"
        f"The story of {topic_name} begins long before {series_label} first aired. "
        f"Real events unfolded over years, shaping the narrative that would later captivate audiences worldwide. "
        f"The documented history reveals details that went far beyond what viewers saw on screen. "
        f"Key figures involved left lasting marks on history that are still felt today.\n\n"

        f"[SECTION: Show vs Reality]\n"
        f"Here is what {series_label} got RIGHT: "
        f"The core story of {topic_name} was faithfully represented in broad strokes. "
        f"The emotional truth of the events was captured with considerable accuracy. "
        f"The major turning points were portrayed in ways that matched the historical record.\n\n"
        f"Here is what they completely changed or left out: "
        f"Many supporting figures were omitted or combined into composite characters. "
        f"The timeline was compressed significantly to fit the screen format. "
        f"Certain documented details were altered for dramatic effect.\n\n"

        f"[SECTION: Conclusion]\n"
        f"The real story of {topic_name} remains one of the most significant in its field. "
        f"Follow Dark Crime Decoded for more real stories behind your favourite crime series and films."
    )


def _write_darkcrimed_script(topic: dict) -> dict:
    """Investigative documentary script for Dark Crime Decoded."""
    research = topic.get("research", {})
    series   = topic.get("series", topic.get("niche", ""))

    # Use new structured fields if available, fall back to legacy fields
    facts_list       = research.get("research_facts")        or research.get("what_show_got_right", [])
    inaccuracy_list  = research.get("research_inaccuracies") or research.get("what_show_got_wrong", [])
    shocking_list    = research.get("research_shocking")     or research.get("shocking_real_facts", [])

    research_facts        = "\n".join(f"- {f}" for f in facts_list)       or "(research the real story)"
    research_inaccuracies = "\n".join(f"- {i}" for i in inaccuracy_list)  or "(research what the show dramatized)"
    research_shocking     = "\n".join(f"- {s}" for s in shocking_list)    or "(include surprising real details)"

    # Wikipedia-sourced verified data (may be None if DDG fallback was used)
    wiki_network      = research.get("network") or "the network"
    wiki_year         = research.get("premiere_year") or "unknown year"
    wiki_real_person  = research.get("real_person") or topic.get("topic", "")

    # ── PART 1: Script body ───────────────────────────────────────────────────
    _topic_str = topic.get("topic") or ""
    _si_long = get_series_for_person(_topic_str)
    _angle   = get_script_angle(_topic_str, _si_long)

    # Documentary-only topics: use investigative prompt, skip series comparison, early return
    if _angle == "documentary":
        user_note    = research.get("user_discovery", "") or topic.get("user_note", "")
        part_number  = detect_part_number(user_note)
        print(f"[Script] Documentary angle detected for: {_topic_str} (part={part_number})")

        _raw_doc         = _write_documentary_script(topic, research, part_number)
        _raw_doc         = check_hallucination(_raw_doc)
        _raw_doc         = fix_first_mention(_raw_doc, is_arabic=False)
        script_text      = validate_script(_raw_doc)
        _series_name_raw = _si_long[0] if _si_long else topic.get("niche", _topic_str)
        _series_type_raw = "Documentary"

        # Hemedti-specific title overrides
        _topic_lower = safe_lower(_topic_str)
        if "hemedti" in _topic_lower or "حميدتي" in _topic_lower or "dagalo" in _topic_lower:
            if part_number == 1:
                doc_title = (
                    "Hemedti Part 1: From Camel Trader to Warlord | Dark Crime Decoded"
                )
            elif part_number == 2:
                doc_title = (
                    "Hemedti Part 2: The Massacre, UAE and Colombian Mercenaries | Dark Crime Decoded"
                )
            else:
                doc_title = (
                    "Hemedti: The Most Dangerous Man You Never Heard Of | Dark Crime Decoded"
                )
        else:
            # Dynamic title from generated angle — avoids generic "What The World Needs To Know"
            part_suffix = f" — Part {part_number}" if part_number else ""
            _doc_angle = generate_untold_angle(topic["topic"], "")
            _doc_angle_title = _doc_angle.get("angle_title", "")
            if _doc_angle_title and "hidden truth" not in _doc_angle_title.lower():
                doc_title = f"{_doc_angle_title}{part_suffix} | Dark Crime Decoded"
            else:
                doc_title = (
                    f"The Untold Story of {topic['topic']}{part_suffix} | Dark Crime Decoded"
                )

        # Queue Part 2 automatically when Part 1 is being written
        if part_number == 1:
            queue_part2_topic(topic)

        script_data = {
            "title":           doc_title,
            "hook":            script_text[:120] if script_text else "",
            "script":          script_text,
            "on_screen_texts": [],
            "caption":         (
                f"Part {part_number} — " if part_number else ""
            ) + f"The real untold story of {topic['topic']}. Follow Dark Crime Decoded.",
            "hashtags":        _build_darkcrimed_hashtags("", None),
            "thumbnail_text":  topic["topic"][:30],
            "chapters":        generate_chapters_from_script(script_text, topic["topic"], "english"),
            "topic":           topic["topic"],
            "niche":           topic["niche"],
            "search_query":    topic.get("search_query", ""),
            "keywords":        topic.get("keywords", []),
            "language":        "english",
            "series_name":     _series_name_raw,
            "series_type":     _series_type_raw,
            "part_number":     part_number,
            "user_discovery":          research.get("user_discovery", ""),
            "user_discovery_expanded": research.get("user_discovery_expanded", []),
            "source_urls":             research.get("source_urls", []),
            "search_queries":          research.get("search_queries", []),
        }
        print(f"[Script] Written (documentary english): '{script_data['title']}'")
        return script_data

    # Prefer series_type from research, then PERSON_TO_SERIES lookup, then default
    _series_name_raw = research.get("series_name") or (topic.get("series_name")) or (_si_long[0] if _si_long else series)
    _series_type_raw = research.get("series_type") or (_si_long[1] if _si_long else "Movie")

    if _series_type_raw == "Movie":
        series_label    = f"{_series_name_raw} Movie"
        content_type    = "film"
        platform_word   = "filmmakers"
        content_word    = "the film"
    else:
        series_label    = f"{_series_name_raw} Series"
        content_type    = "series"
        platform_word   = "showrunners"
        content_word    = "the show"

    user_discovery     = research.get("user_discovery", "")
    discovery_expanded = research.get("user_discovery_expanded", [])
    discovery_section  = ""
    if user_discovery:
        expanded_text = "\n".join(f"- {d}" for d in discovery_expanded) if discovery_expanded else ""
        discovery_section = f"""
IMPORTANT — HOST DISCOVERY (make this the central hook of the video):
The channel host found this specific connection/fact:
"{user_discovery}"

WHAT WE FOUND WHEN WE RESEARCHED THIS DEEPER:
{expanded_text or "(use the facts above to expand on this discovery)"}

Build the story AROUND this discovery. Open the video with it as the hook.
The host found something most viewers don't know — celebrate that discovery.
"""

    # Build real_vs_fiction context block for single-call fallback
    _rvf_fb = research.get("real_vs_fiction") or {}
    _rvf_fb_block = ""
    if _rvf_fb.get("real_people"):
        _rp_lines = [f"  - {p['name']}: {p.get('role','')} ({p.get('era','')})" for p in _rvf_fb["real_people"][:6]]
        _rvf_fb_block += "REAL PEOPLE (cover ALL of them in the historical sections):\n" + "\n".join(_rp_lines) + "\n"
    if _rvf_fb.get("fictional_characters"):
        _fc_lines = [f"  - {c['name']} (played by {c.get('played_by','?')}) → real counterpart: {c.get('based_on','?')}" for c in _rvf_fb["fictional_characters"][:6]]
        _rvf_fb_block += (
            "FICTIONAL→REAL MAP (use ONLY in the 'Real Story vs Screen Story' section — "
            "NEVER use fictional names in the historical narrative sections):\n"
            + "\n".join(_fc_lines) + "\n"
        )
    if _rvf_fb.get("real_vs_show"):
        _rvs_lines = [f"  - {r['aspect']}: reality='{r.get('reality','')}' vs show='{r.get('show','')}'" for r in _rvf_fb["real_vs_show"][:4]]
        _rvf_fb_block += "SHOW VS REALITY COMPARISONS (for the comparison section only):\n" + "\n".join(_rvs_lines) + "\n"

    # Inject show_characters (populated by research_agent STEP 0)
    _sc_fb = research.get("show_characters") or []
    _mandatory_fb = ""
    if _sc_fb:
        sc_lines_fb = [
            f"  - {c['character']} ({c.get('actor','?')}) → real person: {c.get('based_on','?')}: {c.get('real_role','')}"
            for c in _sc_fb
        ]
        real_names_fb = ", ".join(
            c.get('based_on','') for c in _sc_fb
            if safe_lower(c.get('based_on')) not in ('null','none','various','')
        )
        _rvf_fb_block += (
            "SHOW CAST (use fictional names ONLY in the Show vs Reality section):\n"
            + "\n".join(sc_lines_fb) + "\n"
        )
        _mandatory_fb = (
            f"\nMANDATORY — REAL PEOPLE (cover ALL of these in the historical sections):\n"
            f"{real_names_fb}\n"
            f"Give each real person at least one full paragraph.\n"
            f"⚠️ Use fictional character names ONLY in the 'Real Story vs Screen Story' section.\n"
            f"NEVER use fictional character names as historical anchors.\n"
        )

    _dc_active_entity = build_active_entity(topic["topic"]) if is_single_subject(topic["topic"]) else {}
    _dc_entity_lock   = entity_lock_instruction(_dc_active_entity)

    part1_prompt = f"""You are a cinematic longform crime storyteller for YouTube.
Write a 2400-3200 word SCENE-DRIVEN CINEMATIC STORY about: {topic['topic']}
The related series/movie is: {series_label}
ABSOLUTE MINIMUM: 2,325 words (15 minutes at 155 WPM). Videos below 15 minutes are automatic failures.
{_dc_entity_lock}
THIS IS NOT A DOCUMENTARY SUMMARY — IT IS LONGFORM SOCIAL STORYTELLING.
Viewer must feel: "I am trapped inside a cinematic crime story." NOT: "I am listening to a narrated article."

NARRATIVE MOMENTUM ENGINE: Runtime comes from STORY MOVEMENT, not word inflation.
Every 50-80 words must contain a story beat: clue, twist, reveal, turn, or consequence.
Move through: event → reaction → investigation → discovery → escalation → setback → revelation → consequence

NARRATION STYLE: Scene-driven cinematic narration. Flowing paragraphs, no lists, no bullet points. Minimum 3 sentences per paragraph. Each section must feel like a mini-thriller sequence, not a factual explanation block.

COVER ALL CHARACTERS: Dedicate at least one full paragraph to EACH major character. Never focus on just one person.
{_mandatory_fb}{_rvf_fb_block}
CRITICAL: Use ONLY these verified Wikipedia facts. Do NOT invent any information.
Network: {wiki_network}
Series premiered: {wiki_year}
Real person: {wiki_real_person}
{discovery_section}
VERIFIED FACTS (from Wikipedia):
{research_facts}

HOW HISTORY INSPIRED THE SHOW (from Wikipedia):
{research_inaccuracies}

SHOCKING REAL FACTS (from Wikipedia):
{research_shocking}

If you are not 100% sure about a fact — do not include it.
Always say "{wiki_network}" not "Netflix" unless the network IS Netflix.

TONE: Celebrate BOTH the real story AND the show. The show is great entertainment. The real story is even more fascinating. Never attack or accuse the show — explain and celebrate.

Use this EXACT structure (no section labels in the output — spoken words only):

HOOK (150 words = ~60 seconds):
Write as a CINEMATIC SCENE — put the viewer inside ONE specific documented moment.
Short punching sentences. Then escalate. End with an open question that forces watching.
Do NOT open with a summary or generic suspense. The SCENE is the hook.
Strong style: "He sat across from the investigator. His hands were steady. He had done this before."

SERIES INTRO (280 words = ~1.8 minutes):
Celebrate what {series_label} showed the world — it is great television.
Write what made it famous as a SCENE — the moment viewers will remember.
Then pivot: the real story has dimensions the show never captured.

REAL BACKGROUND (450 words = ~2.9 minutes):
Write the real person's origins as SCENES, not biography summaries.
Each era of their life is a new scene with a turning point.
Specific dates, real places, real decisions — each with a consequence.

MAIN STORY (700 words = ~4.5 minutes):
Write the full story as a cinematic sequence of scenes.
Every scene ends with a story beat: clue, turn, reveal, or consequence.
Documented events reconstructed with scene-level specificity.
Real victims, real locations, real consequences — named and human.

INVESTIGATION AND REVELATIONS (400 words = ~2.6 minutes):
Write as an investigation sequence: what investigators found, in what order, what it revealed.
Each discovery is a scene. Each revelation escalates the stakes.
3-4 documented facts that even the show's biggest fans do not know.

REAL STORY VS SCREEN STORY (120 words = ~0.8 minutes):
ONLY write a comparison if you have a VERIFIED, SPECIFIC difference with different facts or numbers.
Write each comparison as a story beat: "The show depicted X. The documented record shows Y."
NEVER write the same number or fact twice as if they are different.
NEVER invent a difference that does not exist.

If no specific verified difference exists, use ONE of these universal film truths instead:
- Timeline compression: "{series_label} compressed events spanning [X] years into [runtime]. Many real moments were left out to fit the story."
- Character composites: "Some characters in {series_label} are composites of multiple real people. {platform_word} combined characters to simplify complex real-world relationships."
- Dialogue invention: "All dialogue in {series_label} was written by screenwriters — the real {wiki_real_person} never said those exact words, but the spirit was captured accurately."
- Ending dramatisation: "{series_label} dramatised the ending for emotional impact. The real events were less cinematic but equally powerful."

End this section with: "{series_label} may have taken creative liberties, but it captures the spirit of the real story. The real {wiki_real_person} was just as fascinating — if not more so — than the screen version."

CONCLUSION (120 words = ~0.8 minutes):
- What happened after the events {series_label} depicted
- Where the real people are now
- One question to tease the next video
- End with: "Follow Dark Crime Decoded for more real stories behind your favourite crime series"

TOTAL TARGET: 1800 words minimum, 2500 words maximum.
SECTION TOTALS: 150+350+450+750+350+100+200 = 2350 words = ~14-16 minutes at 150-160 wpm.

PRISON SENTENCE RULE (critical for Arabic translation):
Always write "served X years IN PRISON" or "spent X years BEHIND BARS" — never just "served X years".
Google Translate needs the prison context word to produce correct Arabic ("سجن" not "خدم").
Example: "He served 15 years in prison" NOT "He served 15 years".

STRICT WRITING RULES:
1. NEVER start two consecutive sentences with the same word
2. NEVER use "He was" more than once per paragraph
3. Use varied sentence starters: year ("In 1993..."), place, number, action subject, age, reveal, contrast, viewer address
4. Each sentence must contain exactly ONE specific fact (name, number, date, or place)
5. Mix sentence lengths — short punchy sentences after long ones
6. Name {series_label} at most 8 times total across the entire script
7. Include at least 6 real dates or numbers
8. Use "..." for dramatic pauses

ANTI-REPETITION RULES:
- Never use the series/movie name more than once per paragraph (max 8 times total)
- Replace repeated series name with: "the film", "the movie", "it", "the show", "the series"
- Each paragraph must introduce NEW information not already stated
- Never repeat a fact already stated earlier in the script
- If you catch yourself writing "{series_label}" twice in a row, stop and use a pronoun instead

BANNED PHRASES — never use these:
- "what the show got wrong" / "what Netflix lied about" / "what Hollywood changed" / "inaccuracies in the show"
- "delve into" / "complex figure" / "shaped by" / "rose to infamy" / "criminal mastermind"
- "hero to some" → use the actual act (e.g. "He built 84 football fields for the poor")
- NEVER repeat the same fact twice

CORRECT PHRASES TO USE INSTEAD:
- "the real story that inspired the show"
- "what really happened in history"
- "the fascinating true events behind the series"
- "the real person who inspired the character"
- "what happened before/after the show's timeline"
- "historical facts that make the story even more incredible"

Topic: {topic['topic']}
Series/Movie: {series_label}

Start immediately with the HOOK. Write spoken words only — no labels, no headers."""

    # Generate untold angle first — used in script + title + short video
    _angle_data = generate_untold_angle(topic["topic"], series_label)

    # Primary: 5-call split targeting 2,600–3,350 real words (~17-22 min)
    script_text = write_long_script_split(topic, research, _si_long, angle=_angle_data)
    if script_text and clean_word_count(script_text) >= LONG_SCRIPT_MIN_WORDS:
        script_text = validate_script(script_text)
        print(f"[Script] ✅ Split method OK: {clean_word_count(script_text)} real words")
    else:
        if script_text:
            print(f"[Script] Split too short ({clean_word_count(script_text)} real words) — falling back to single call")
        else:
            print("[Script] Split method failed — falling back to single call")
        script_text = ""
        for attempt in range(2):
            _prompt = part1_prompt
            if attempt > 0:
                _prompt += f"""

CRITICAL: Previous attempt was only {clean_word_count(script_text)} real words. MINIMUM REQUIRED: {LONG_SCRIPT_MIN_WORDS} real words.
You must EXPAND every section significantly:
- HOOK: Add more shocking statistics
- SERIES INTRO: Describe the show in more detail
- REAL BACKGROUND: Add childhood, family, early life details
- MAIN STORY: Add more specific events with exact dates
- SHOCKING REVELATIONS: Add 2 more unknown facts
- REAL VS SCREEN: Add 3 specific scene comparisons
- CONCLUSION: Add what happened to key people afterwards
Do not summarize — give full detailed information."""
            script_text = validate_script(_ai_script_call(_prompt, max_tokens=6000, temperature=0.85).strip())
            words   = clean_word_count(script_text)
            minutes = words / 163
            print(f"[Script] Attempt {attempt + 1}: {words} real words = ~{minutes:.1f} minutes")
            if words >= LONG_SCRIPT_MIN_WORDS:
                print(f"[Script] ✅ Length OK: {words} real words")
                break
            print(f"[Script] WARNING: Too short ({words} real words) — retrying...")

    # ── Emergency backup: generate minimal local script if all AI calls failed ─
    if not script_text or clean_word_count(script_text) < 200:
        print(f"[Fallback] All AI providers returned empty — generating emergency backup script")
        script_text = _emergency_backup_script(topic["topic"], series_label)

    # Safety valve: only triggers for runaway generation (> ~60 min)
    if clean_word_count(script_text) > LONG_SCRIPT_MAX_WORDS:
        print(f"[Script] Safety cap applied (> {LONG_SCRIPT_MAX_WORDS}w)")
        script_text = _cap_script_max_words(script_text, LONG_SCRIPT_MAX_WORDS)

    # ── Entity contamination guard ────────────────────────────────────────────
    if _dc_active_entity:
        _eg_passed, _eg_offending = validate_entity_consistency(script_text, _dc_active_entity)
        if not _eg_passed:
            print(f"[EntityGuard] Long script contaminated — sanitising {len(_eg_offending)} paragraph(s)")
            script_text = sanitize_script(script_text, _dc_active_entity)

    # ── Topic anchor validation: must mention real person + show ─────────────
    if not _validate_on_topic(script_text, topic["topic"], series_label):
        print(f"[Script] Long script missing topic anchors — attempting one regeneration")
        try:
            _anchor_p = part1_prompt + (
                f"\n\nCRITICAL: You MUST explicitly name '{topic['topic']}' (real person) "
                f"and '{series_label}' (the show/movie) and explain the connection. "
                "Do NOT write generic crime content."
            )
            _anch_raw = _ai_script_call(_anchor_p, max_tokens=6000, temperature=0.85)
            _anch = validate_script(_anch_raw.strip()) if _anch_raw else ""
            if _anch and clean_word_count(_anch) >= LONG_SCRIPT_MIN_WORDS:
                script_text = _cap_script_max_words(_anch, LONG_SCRIPT_MAX_WORDS)
                print(f"[Script] Topic-anchored script: {clean_word_count(script_text)} words")
            else:
                print(f"[Fallback] Topic-anchor regen failed or too short — keeping original script")
        except Exception as _ae:
            print(f"[Fallback] Topic-anchor regen raised exception ({_ae}) — keeping original script")

    # ── PART 2: Generate metadata only (title, hook, captions, etc.) ────────
    _series_info    = get_series_for_person(topic["topic"])
    _related_series = f"{_series_info[0]} {_series_info[1]}" if _series_info else series
    part2_prompt = f"""You are a content packaging assistant.
Based on this voiceover script about "{topic['topic']}", generate the metadata fields.

TITLE FORMAT (mandatory):
Generate a strong, story-driven documentary title directly from the actual content.
The untold angle for this video: "{_angle_data.get('angle_title', topic['topic'])}"
The real subject: {topic['topic']}
Related series/movie (supporting context only — NOT required in title): {_related_series}

GOOD TITLE PATTERNS (use as inspiration, not templates):
"The Last Night of Jeffrey Epstein | Dark Crime Decoded"
"How Pablo Escobar Built His Empire | Dark Crime Decoded"
"The Secret Files of the Dahmer Case | Dark Crime Decoded"
"The Interview That Broke John Douglas | Dark Crime Decoded"
"The Real Pablo Escobar Was Even Darker Than Narcos | Dark Crime Decoded"

DO NOT use "[Person] & [Movie/Series] — [hook]" format.
DO NOT invent adaptations or force series framing into the title.
The series/movie is OPTIONAL context — only include it if it naturally fits the story.
TONE: Gripping and revelatory. The title teases the hidden truth.
Max 90 chars total.

Return ONLY this JSON with no extra text:
{{
  "title": "[Compelling documentary title from the actual story] | Dark Crime Decoded",
  "hook": "First 3-second spoken hook sentence",
  "on_screen_texts": [
    "Short bold text for second 0",
    "Short bold text for second 10",
    "Short bold text for second 20",
    "Short bold text for second 35"
  ],
  "caption": "2-3 sentence caption for social media",
  "hashtags": "#tag1 #tag2 #tag3 #tag4 #tag5 #tag6 #tag7 #tag8 #tag9 #tag10",
  "thumbnail_text": "4-word thumbnail text"
}}"""

    meta = normalize_ai_json_response(
        _ai_script_call(part2_prompt, max_tokens=1000, temperature=0.3, json_mode=True),
        required_keys=["title", "hook", "on_screen_texts", "caption", "hashtags", "thumbnail_text"],
        list_keys=["on_screen_texts"],
    )
    _angle_str = _angle_data.get("angle_title", "")
    _fallback_title = (
        f"{_angle_str} | Dark Crime Decoded"
        if _angle_str else f"The True Story of {topic['topic']} | Dark Crime Decoded"
    )
    script_data = {
        "title":          meta.get("title", _fallback_title),
        "hook":           meta.get("hook", ""),
        "script":         script_text,
        "on_screen_texts": meta.get("on_screen_texts", []),
        "caption":        meta.get("caption", ""),
        "hashtags":       _build_darkcrimed_hashtags(meta.get("hashtags", ""), _series_info),
        "thumbnail_text": meta.get("thumbnail_text", ""),
        "chapters":       generate_chapters_from_script(
            script_text,
            topic["topic"],
            "english",
            angle_title=_angle_data.get("angle_title", ""),
        ),
    }
    _topic_name = topic.get("topic", "")
    script_data["topic"]              = _topic_name
    script_data["niche"]              = topic.get("niche", _topic_name)
    script_data["search_query"]       = topic.get("search_query", _topic_name)
    script_data["keywords"]           = topic.get("keywords", [_topic_name] if _topic_name else [])
    script_data["language"]           = "english"
    script_data["manual_topic"]       = bool(topic.get("manual_topic"))
    script_data["series_name"]        = _series_name_raw
    script_data["series_type"]        = _series_type_raw
    script_data["angle_title"]        = _angle_data.get("angle_title", "")
    script_data["angle_hook"]         = _angle_data.get("angle_hook", "")
    # Carry discovery fields so Telegram preview can show them
    script_data["user_discovery"]          = user_discovery
    script_data["user_discovery_expanded"] = discovery_expanded
    # Carry show_characters forward so write_short_script can use them
    script_data["show_characters"]         = research.get("show_characters", [])
    # Source references from research — passed through for Telegram preview + video overlays
    script_data["source_urls"]    = research.get("source_urls", [])
    script_data["search_queries"] = research.get("search_queries", [])
    _s = script_data["script"]
    _s = upgrade_script_for_retention(_s)
    _s = pick_best_hook(_s, topic=topic.get("topic", ""), series=_series_name_raw)
    _s = evaluate_and_fix_script(_s)
    from agents.script_quality import (
        apply_all_quality_filters, detect_quality_issues,
        score_fact_density, detect_fiction_bleed, validate_timeline_consistency,
    )
    _s = apply_all_quality_filters(_s)
    _qi = detect_quality_issues(_s)
    _fd = score_fact_density(_s, topic=topic.get("topic",""), series=_series_name_raw)
    _fc_names = [
        c.get("based_on","") or c.get("character","")
        for c in (research.get("show_characters") or [])
        if c.get("character")
    ]
    _fb = detect_fiction_bleed(_s, _fc_names) if _fc_names else {}
    _era = (research.get("verified_facts") or {}).get("time_period", "")
    _tl  = validate_timeline_consistency(_s, topic=topic.get("topic", ""), expected_era=_era)
    print(
        f"[Quality] SUMMARY — words: {_qi.get('word_count',0)} | "
        f"filler: {_qi.get('filler_count',0)} | "
        f"fact density: {_fd.get('density_pct',0)}% ({_fd.get('verdict','?')}) | "
        f"fiction bleed: {_fb.get('bleed_count',0)} paragraph(s) | "
        f"timeline OK: {_tl.get('consistent',True)} | violations: {_tl.get('violation_count',0)}"
    )
    if _qi.get("filler_count", 0):
        print(f"[Quality] Filler phrases detected: {_qi.get('filler_phrases',[])}")
    if _fb.get("bleed_count", 0):
        print(f"[Quality] Fiction bleed detected — fictional names outside Show vs Reality: "
              f"{[o[1] for o in _fb.get('offenders',[])[:3]]}")
    if not _tl.get("consistent", True):
        print(f"[Quality] Timeline violations — "
              f"fiction bleed: {_tl.get('fiction_bleed',[])} | "
              f"cross-topic: {_tl.get('cross_topic',[])} | "
              f"blocked entities: {_tl.get('contamination',[])[:3]}")
    _balance_entities = [_topic_name, _series_name_raw, "Jordan Belfort", "Danny Porush", "Stratton Oakmont"]
    _s = balance_entity_mentions(_s, [e for e in _balance_entities if e])
    script_data["script"] = _s
    print(f"[Script] Written (english): '{script_data['title']}'")
    return script_data


def fix_arabic_prison_terms(arabic_text: str) -> str:
    """Fix mistranslated prison/service terms that Google Translate gets wrong."""
    import re

    # Regex patterns: خدم + number + time unit
    patterns = [
        (r'خدم\s+(\d+)\s+عامًا',  r'سجن \1 عامًا'),
        (r'خدم\s+(\d+)\s+عاما',   r'سجن \1 عاماً'),
        (r'خدم\s+(\d+)\s+عام',    r'سجن \1 عام'),
        (r'خدم\s+(\d+)\s+سنة',    r'سجن \1 سنة'),
        (r'خدم\s+(\d+)\s+سنوات',  r'سجن \1 سنوات'),
        (r'خدم\s+(\d+)\s+شهرًا',  r'قضى \1 شهراً في السجن'),
        (r'خدم\s+(\d+)\s+شهرا',   r'قضى \1 شهراً في السجن'),
        (r'خدم\s+(\d+)\s+شهور',   r'قضى \1 شهور في السجن'),
    ]
    for pattern, replacement in patterns:
        arabic_text = re.sub(pattern, replacement, arabic_text)

    # Fixed string replacements
    fixed = [
        ("خدم في السجن",      "قضى في السجن"),
        ("خدم مدة في السجن",  "قضى مدة في السجن"),
        ("خدم فترة",          "قضى فترة"),
        ("خدم وقتًا",         "قضى وقتاً"),
        ("خدم حكمًا",         "نفّذ حكماً"),
        ("خدم عقوبة",         "نفّذ عقوبة"),
        ("خدم سنوات",         "سجن سنوات"),
        ("خدم عاماً",         "سجن عاماً"),
        ("خدم عام",           "سجن عام"),
        ("خدم أشهر",          "سجن أشهر"),
        ("خدم شهر",           "سجن شهر"),
        ("خدم مدة",           "قضى مدة"),
        ("خدم وقت",           "قضى وقت"),
    ]
    for wrong, correct in fixed:
        arabic_text = arabic_text.replace(wrong, correct)

    return arabic_text


def fix_arabic_cta(arabic_text: str) -> str:
    """Fix mistranslated CTA verbs and preserve channel name in English."""
    replacements = [
        ("اتبع Dark Crime Decoded",  "تابع Dark Crime Decoded"),
        ("اتبع دارك كرايم",          "تابع Dark Crime Decoded"),
        ("اتبعنا",                   "تابعونا"),
        ("اتبع القناة",              "تابع القناة"),
        ("اتبع للحصول",             "تابع للحصول"),
        # Restore channel name if Google Translate transliterated it
        ("داركرايم ديكودد",          "Dark Crime Decoded"),
        ("دارك كرايم ديكودد",        "Dark Crime Decoded"),
        ("دارك كرايم ديكوديد",       "Dark Crime Decoded"),
        ("دارك كرايم",               "Dark Crime Decoded"),
    ]
    for wrong, correct in replacements:
        arabic_text = arabic_text.replace(wrong, correct)
    return arabic_text


def fix_arabic_rsf(text: str) -> str:
    """Fix RSF and related terminology wrongly translated by Google Translate."""
    fixes = [
        # RSF wrong translations — Google maps RSF to Reporters Without Borders ❌
        ("مراسلون بلا حدود",                              "قوات الدعم السريع"),
        ("مراسلين بلا حدود",                              "قوات الدعم السريع"),
        ("المراسلون بلا حدود",                             "قوات الدعم السريع"),
        ("منظمة RSF",                                     "قوات الدعم السريع"),
        ("RSF السودان",                                   "قوات الدعم السريع في السودان"),
        # Fake documentary phrases
        ("الفيلم الوثائقي الذي أعدته منظمة قوات الدعم السريع", "هذا التحقيق"),
        ("وثائقي قوات الدعم السريع",                      "هذا التحقيق"),
        ("الفيلم الوثائقي لمنظمة",                        "تحقيق"),
        ("الذي أعدته منظمة",                              "الذي يكشفه"),
    ]
    for wrong, correct in fixes:
        text = text.replace(wrong, correct)
    # Bare RSF must come last so compound phrases above match first
    text = text.replace("RSF", "قوات الدعم السريع")
    return text


def fix_rsf_translation(arabic_text: str) -> str:
    """Alias kept for backward compatibility — delegates to fix_arabic_rsf."""
    return fix_arabic_rsf(arabic_text)


def check_hallucination(script_text: str) -> str:
    """Remove hallucinated references to a non-existent RSF documentary."""
    fake_replacements = {
        "The RSF Sudan Documentary portrays":  "Evidence and testimonies show",
        "The RSF Documentary shows":           "Investigation reveals",
        "The documentary portrays his rise":   "Documented evidence shows his rise",
        "The film effectively shows":          "Survivor testimonies confirm",
        "The documentary depicts":             "Evidence confirms",
        "the RSF documentary":                 "this investigation",
        "The RSF documentary":                 "This investigation",
        "an RSF documentary":                  "investigative reporting",
    }
    for fake, real in fake_replacements.items():
        script_text = script_text.replace(fake, real)
    return script_text


def fix_first_mention(text: str, is_arabic: bool = False) -> str:
    """Ensure first abbreviation mention includes the full name."""
    if is_arabic:
        if "RSF" in text and "قوات الدعم السريع" not in text:
            text = text.replace("RSF", "قوات الدعم السريع (RSF)", 1)
    else:
        if "RSF" in text and "Rapid Support Forces" not in text:
            text = text.replace("RSF", "Rapid Support Forces (RSF)", 1)
    return text


# Acronyms / Latin terms that machine translators (Google, MyMemory, DeepL)
# tend to silently drop when translating to Arabic.  Applied BEFORE every
# translation call and AFTER as a safety net in _fix_arabic.
# Listed longest-first so "BTK killer" is substituted before "BTK".
_AR_ACRONYM_SUBS: list[tuple[str, str]] = [
    ("BTK killer",   "قاتل بي تي كي"),
    ("BTK Killer",   "قاتل بي تي كي"),
    ("the BTK",      "بي تي كي"),
    ("BTK",          "بي تي كي"),
    ("FBI",          "إف بي آي"),
    ("CIA",          "سي آي إيه"),
    ("DEA",          "دي إيه إيه"),
    ("NSA",          "إن إس إيه"),
    ("LAPD",         "شرطة لوس أنجلوس"),
    ("NXIVM",        "نيكزيوم"),
    ("Dennis Rader", "دينيس رادر"),
]

_LATIN_LETTER_AR: dict[str, str] = {
    'A': 'إيه', 'B': 'بي',     'C': 'سي',    'D': 'دي',    'E': 'إي',
    'F': 'إف',  'G': 'جي',     'H': 'إتش',   'I': 'آي',    'J': 'جاي',
    'K': 'كاي', 'L': 'إل',     'M': 'إم',    'N': 'إن',    'O': 'أوه',
    'P': 'بي',  'Q': 'كيو',    'R': 'آر',    'S': 'إس',    'T': 'تي',
    'U': 'يو',  'V': 'في',     'W': 'دبليو', 'X': 'إكس',   'Y': 'واي',
    'Z': 'زي',
}


def _apply_acronym_subs(text: str) -> str:
    """Replace known Latin acronyms with Arabic phonetics, then convert any
    remaining ALL-CAPS Latin word (2-6 chars) letter-by-letter.
    """
    import re as _re
    for en, ar in _AR_ACRONYM_SUBS:
        text = _re.sub(r'\b' + _re.escape(en) + r'\b', ar, text)
    def _spell_out(m: '_re.Match') -> str:
        return ' '.join(_LATIN_LETTER_AR.get(c, c) for c in m.group(0).upper())
    text = _re.sub(r'\b[A-Z]{2,6}\b', _spell_out, text)
    return text


def _fix_arabic(text: str) -> str:
    """Apply all Arabic post-processing fixes in one call."""
    text = fix_arabic_prison_terms(text)
    text = fix_arabic_cta(text)
    text = fix_arabic_rsf(text)
    text = _apply_acronym_subs(text)
    return text


def format_for_tts(text: str) -> str:
    """
    Format script text for natural TTS delivery.
    Auto-routes Arabic text to format_for_tts_arabic().
    - Short punchy sentences get their own line.
    - Shocking facts / numbers get trailing ellipsis.
    - Long sentences split at natural pause conjunctions.
    - Section markers are preserved unchanged.
    """
    import re
    # Detect Arabic by Unicode block presence
    if re.search(r'[\u0600-\u06FF]', text):
        return format_for_tts_arabic(text)

    lines_out: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Preserve blank lines and section markers
        if not line or line.startswith("[SECTION:"):
            lines_out.append(raw_line)
            continue

        # Split the line into individual sentences
        sentences = re.split(r'(?<=[.!?])\s+', line)
        formatted: list[str] = []
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            # Rule 2: numbers / shocking facts get "..."
            # Matches sentences ending with a plain period that contain a number
            # or that are ≤8 words (punchy fact)
            words = sent.split()
            has_number = bool(re.search(r'\d[\d,]*', sent))
            is_punchy  = len(words) <= 8 and sent.endswith(".")
            if (has_number or is_punchy) and sent.endswith("."):
                sent = sent[:-1] + "..."

            # Rule 3: split long sentences at natural pause conjunctions
            # Only split if sentence is >14 words
            if len(words) > 14:
                # Split before: but, and, yet, so, while, because, after, before,
                #               when, though, although, however, until
                pause_pattern = re.compile(
                    r'\s+(but|and yet|yet|so|while|because|after|before|'
                    r'when|though|although|however|until)\s+',
                    re.IGNORECASE,
                )
                parts = pause_pattern.split(sent)
                rebuilt: list[str] = []
                i = 0
                while i < len(parts):
                    chunk = parts[i].strip()
                    if i + 1 < len(parts):
                        conjunction = parts[i + 1]
                        next_chunk  = parts[i + 2].strip() if i + 2 < len(parts) else ""
                        # Add ellipsis after first part, capitalise conjunction
                        if chunk and not chunk[-1] in ".!?...":
                            chunk += "..."
                        rebuilt.append(chunk)
                        # Start next chunk with the conjunction capitalised
                        if next_chunk:
                            rebuilt.append(conjunction.capitalize() + " " + next_chunk)
                        i += 3
                    else:
                        if chunk:
                            rebuilt.append(chunk)
                        i += 1
                formatted.extend(rebuilt)
            else:
                formatted.append(sent)

        # Rule 4: consecutive short sentences (≤6 words) each on their own line
        lines_out.extend(formatted)
        lines_out.append("")  # blank line between original lines for breathing room

    result = "\n".join(lines_out).strip()
    # Collapse 3+ consecutive blank lines → 1
    result = re.sub(r'\n{3,}', '\n\n', result)
    line_count = len([l for l in result.splitlines() if l.strip()])
    print(f"[Script] Script formatted for TTS — {line_count} lines")
    return result


def _clean_arabic_with_openai(section_text: str) -> str:
    """Ask OpenAI to rewrite Arabic section in clean fusha — short sentences, no filler."""
    import os as _os
    import requests as _req

    api_key = _os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise Exception("No OpenAI key")

    prompt = (
        "أعد كتابة هذا النص العربي بأسلوب فصيح حديث ومباشر.\n"
        "جمل قصيرة وقوية. أفعال قوية. احذف الحشو والتكرار.\n"
        "حافظ على نفس المعنى والوقائع تماماً.\n"
        "أعد النص المعاد صياغته فقط بدون تعليق.\n\n"
        f"{section_text}"
    )
    try:
        r = _req.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "أنت محرر نصوص وثائقية عربية محترف."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2000,
                "temperature": 0.4,
            },
            timeout=45,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Script] Arabic cleanup failed: {e}")
    return section_text


def _groq_clean_arabic(section_text: str) -> str:
    """Groq fallback: rewrite Arabic section in clean fusha."""
    prompt = (
        "أعد كتابة هذا النص العربي بأسلوب فصيح حديث ومباشر.\n"
        "جمل قصيرة وقوية. أفعال قوية. احذف الحشو والتكرار.\n"
        "حافظ على نفس المعنى والوقائع تماماً.\n"
        "أعد النص المعاد صياغته فقط بدون تعليق.\n\n"
        f"{section_text}"
    )
    resp = _groq_call(
        messages=[
            {"role": "system", "content": "أنت محرر نصوص وثائقية عربية محترف."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2000,
        temperature=0.4,
    )
    return (resp.choices[0].message.content or "").strip()


def format_for_tts_arabic(text: str) -> str:
    """
    Format Arabic script for natural TTS delivery.
    - OpenAI cleanup pass (fusha, no filler), falling back to Groq or as-is.
    - Each sentence on its own line.
    - Shocking facts / numbers → trailing "..."
    - Short punchy clauses each on own line.
    - Breathing-room blank lines every 2-3 lines.
    """
    import re

    # Section markers go through unchanged; process section bodies separately.
    section_marker_re = re.compile(
        r'((?:^\s*[\[\{\(]\s*(?:section|chapter|part|القسم|قسم)\s*:[^\]\}\)\n]+[\]\}\)]\s*$))',
        flags=re.IGNORECASE | re.MULTILINE,
    )
    parts = section_marker_re.split(text)
    out_parts: list[str] = []

    for part in parts:
        if section_marker_re.match(part):
            out_parts.append(part)
            continue
        if not part.strip():
            out_parts.append(part)
            continue

        # Skip LLM cleanup — it compresses Arabic content by 50%+ via max_tokens=2000 + "delete filler" prompt
        cleaned = part

        lines_out: list[str] = []
        line_count_since_break = 0

        # Split at Arabic sentence endings: . ؟ ! ، (comma as soft pause)
        # Use period/question/exclamation as hard splits, comma as soft split
        sentences = re.split(r'(?<=[.؟!،])\s*', cleaned)

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue

            # Numbers or short punchy clauses → ellipsis
            has_number = bool(re.search(r'\d[\d,]*', sent))
            words      = sent.split()
            is_punchy  = len(words) <= 6

            if (has_number or is_punchy) and sent[-1] in '.؟!،':
                sent = sent[:-1] + "..."
            elif sent[-1] not in '.؟!،...':
                sent += "."

            lines_out.append(sent)
            line_count_since_break += 1

            # Breathing room every 2-3 lines
            if line_count_since_break >= 3:
                lines_out.append("")
                line_count_since_break = 0

        out_parts.append("\n".join(lines_out))

    result = "\n".join(out_parts).strip()
    result = re.sub(r'\n{3,}', '\n\n', result)
    line_count = len([l for l in result.splitlines() if l.strip()])
    print(f"[Script] Arabic script formatted for TTS — {line_count} lines")
    return result


def translate_to_arabic_google(text: str) -> str:
    """Translate English text to Arabic using Google Translate free REST API."""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl":     "en",
        "tl":     "ar",
        "dt":     "t",
        "q":      text,
    }
    import requests as _requests
    response = _requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    result     = response.json()
    translated = "".join([item[0] for item in result[0]])
    return _fix_arabic(translated)


def translate_to_arabic_mymemory(text: str) -> str:
    """Translate to Arabic via MyMemory free API (no key, 1000 req/day).
    Chunks text at 490 chars to stay inside the free-tier limit.
    """
    import requests as _req
    _CHUNK = 490
    chunks = [text[i:i + _CHUNK] for i in range(0, len(text), _CHUNK)]
    parts: list[str] = []
    for chunk in chunks:
        if not chunk.strip():
            parts.append(chunk)
            continue
        r = _req.get(
            "https://api.mymemory.translated.net/get",
            params={"q": chunk, "langpair": "en|ar"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        translated = data.get("responseData", {}).get("translatedText", "")
        if not translated or "MYMEMORY WARNING" in translated:
            raise ValueError(f"MyMemory bad response: {data.get('responseStatus')}")
        parts.append(translated)
    return _fix_arabic(" ".join(parts))


def translate_to_arabic_deepl(text: str) -> str:
    """Translate to Arabic via DeepL free API (500k chars/month free tier).
    Requires DEEPL_API_KEY environment variable (free account at deepl.com).
    """
    import requests as _req
    api_key = os.getenv("DEEPL_API_KEY", "").strip()
    if not api_key:
        raise ValueError("DEEPL_API_KEY not set")
    # Free tier uses api-free.deepl.com; paid uses api.deepl.com
    base = "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"
    r = _req.post(
        f"{base}/v2/translate",
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
        json={"text": [text], "source_lang": "EN", "target_lang": "AR"},
        timeout=30,
    )
    r.raise_for_status()
    translated = r.json()["translations"][0]["text"]
    if not translated:
        raise ValueError("DeepL returned empty translation")
    return _fix_arabic(translated)


def _groq_translate_arabic(english_text: str, topic: str = "") -> str:
    """Translate to Arabic using Groq with detailed documentary prompt."""
    word_count   = len(english_text.split())
    min_ar_words = int(word_count * 1.0)
    prompt = f"""Translate this English script to Arabic.

CRITICAL RULES:
1. DO NOT shorten or summarize anything
2. Every English paragraph = one Arabic paragraph
3. Keep ALL sentences — do not skip any
4. Maintain dramatic pacing and storytelling
5. Arabic should be SAME LENGTH as English
6. If English has {word_count} words → Arabic must have minimum {min_ar_words} words
7. Do not combine sentences
8. Keep all specific facts, dates, numbers
9. RSF = قوات الدعم السريع (NEVER مراسلون بلا حدود)
10. Keep "Dark Crime Decoded" in English
11. Keep series/movie names in English
12. This is serious investigative journalism — translate formally and accurately

English text:
{english_text}

Return ONLY the Arabic translation. No explanations, no notes."""
    resp = _groq_call(
        messages=[
            {"role": "system", "content": "أنت مترجم عربي محترف متخصص في الجريمة الحقيقية والصحافة الاستقصائية."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=6000,
        temperature=0.3,
    )
    return _fix_arabic((resp.choices[0].message.content or "").strip())


def try_translate_arabic(text: str, topic: str = "") -> str:
    """Translate to Arabic using a cost-tiered chain.
    Free providers first — OpenAI only as last resort.

    1. Google Translate  (free, no key, handles any length)
    2. MyMemory         (free, no key, 1000 req/day, chunked)
    3. DeepL            (free tier 500k chars/month, needs DEEPL_API_KEY)
    4. Groq             (LLM translation, free tier)
    5. OpenAI gpt-4o    (last resort — highest quality but costs money)
    """
    # Pre-substitute acronyms that machine translators silently drop (BTK → بي تي كي)
    text = _apply_acronym_subs(text)

    # 1. Google Translate (free — primary)
    try:
        result = translate_to_arabic_google(text)
        if result:
            print("[Script] Arabic translation via Google ✅")
            return result
    except Exception as e:
        print(f"[Script] Google translation failed: {e}")

    # 2. MyMemory (free — no key required)
    try:
        result = translate_to_arabic_mymemory(text)
        if result:
            print("[Script] Arabic translation via MyMemory ✅")
            return result
    except Exception as e:
        print(f"[Script] MyMemory translation failed: {e}")

    # 3. DeepL (free tier — needs DEEPL_API_KEY)
    try:
        result = translate_to_arabic_deepl(text)
        if result:
            print("[Script] Arabic translation via DeepL ✅")
            return result
    except Exception as e:
        print(f"[Script] DeepL translation failed: {e}")

    # 4. Groq (LLM — semantic quality, free tier)
    try:
        result = _groq_translate_arabic(text, topic=topic)
        if result:
            print("[Script] Arabic translation via Groq ✅")
            return result
    except Exception as e:
        print(f"[Script] Groq translation failed: {e}")

    # 5. OpenAI (last resort — costs money)
    try:
        result = translate_to_arabic_openai(text, topic=topic)
        if result:
            print("[Script] Arabic translation via OpenAI ✅ (fallback)")
            return result
    except Exception as e:
        print(f"[Script] OpenAI translation unavailable: {e}")

    print("[Script] ⚠️ All translation services failed — returning original English text")
    return text


def translate_to_arabic_openai(english_text: str, topic: str = "") -> str:
    """Translate to Arabic via OpenAI gpt-4o-mini with correct RSF terminology. Falls back to Google."""
    import os as _os
    import requests as _req

    api_key = _os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise Exception("No OpenAI key")

    word_count = len(english_text.split())
    # Arabic can become too short if we allow aggressive compression.
    # Keep Arabic at least equal to English word count for duration stability.
    min_ar_words = int(word_count * 1.0)

    def _build_prompt(strong: bool = False) -> str:
        extra = (
            "\n\nWARNING: Previous attempt was too short. You MUST translate EVERY sentence. "
            "Do NOT skip, summarize, or combine paragraphs. Every English paragraph must become "
            "one full Arabic paragraph of the same length."
        ) if strong else ""
        return f"""Translate this English script to Arabic.

CRITICAL RULES:
1. DO NOT shorten or summarize anything
2. Every English paragraph = one Arabic paragraph
3. Keep ALL sentences — do not skip any
4. Maintain dramatic pacing and storytelling
5. Arabic should be SAME LENGTH as English
6. If English has {word_count} words → Arabic must have minimum {min_ar_words} words
7. Do not combine sentences
8. Keep all specific facts, dates, numbers
9. RSF = قوات الدعم السريع (NEVER مراسلون بلا حدود)
10. Keep "Dark Crime Decoded" in English
11. First mention of RSF: "قوات الدعم السريع (RSF)"
12. First mention of SAF: "القوات المسلحة السودانية (SAF)"
13. First mention of ICC: "محكمة الجنايات الدولية (ICC)"
14. Keep all proper names in original language (Hemedti, Dagalo, Khartoum, Darfur, etc.)
15. Keep series/movie names in English
16. This is serious investigative journalism — translate formally and accurately

English word count: {word_count}
Your Arabic translation must be at least {min_ar_words} words.{extra}

English text:
{english_text}

Return ONLY the Arabic translation. No explanations, no notes."""

    def _do_translate(prompt_text: str) -> str | None:
        try:
            r = _req.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a professional Arabic documentary translator for a Netflix/Al Jazeera "
                                "investigative true crime series. "
                                "Translate into natural spoken Modern Standard Arabic — not formal Classical Arabic. "
                                "TARGET STYLE: Netflix investigative documentary narration — cold, factual, evidence-driven. "
                                "FORBIDDEN STYLE: poetic horror atmosphere, vague suspense filler, repetitive dark "
                                "phrases, emotional monologue without facts. "
                                "INVESTIGATION-FIRST RULES: every translated paragraph must preserve at least one of: "
                                "a specific name + concrete action, a date or timeline marker, "
                                "a piece of forensic evidence or legal testimony, or an investigative development. "
                                "Atmospheric sentences (mood without facts) are tolerated only once per four paragraphs — "
                                "all others must carry factual weight. "
                                "When an English sentence is long, break it into two shorter Arabic sentences for spoken rhythm. "
                                "Short punchy English sentences must stay short in Arabic. "
                                "Adapt idioms and sarcasm naturally — never translate literally. "
                                "Preserve every fact, name, and date — never summarise or skip content."
                            ),
                        },
                        {"role": "user", "content": prompt_text},
                    ],
                    "max_tokens": 6000,
                    "temperature": 0.3,
                },
                timeout=90,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"[Script] OpenAI translation failed: {e}")
        return None

    result = _do_translate(_build_prompt(strong=False))
    if result:
        result = _fix_arabic(result)
        ar_words = len(result.split())
        en_words = word_count
        ratio = ar_words / max(en_words, 1)
        print(f"[Script] EN: {en_words} words | AR: {ar_words} words | Ratio: {ratio:.2f}")
        # Short fragments naturally compress in Arabic — accept immediately.
        if en_words < 80:
            print("[Script] Short fragment — accepted without ratio check")
            print("[Script] OpenAI Arabic translation ✅")
            return result
        # One optional retry if very short; then accept whatever we get.
        if ratio < 0.75:
            print("[Script] ⚠️ Ratio low — one retry with stronger instruction")
            retry = _do_translate(_build_prompt(strong=True))
            if retry:
                retry = _fix_arabic(retry)
                retry_words = len(retry.split())
                if retry_words > ar_words:
                    print(f"[Script] Retry AR: {retry_words} words — using retry")
                    result = retry
                    ar_words = retry_words
                else:
                    print(f"[Script] Retry not longer ({retry_words} vs {ar_words}) — keeping original")
            final_ratio = ar_words / max(en_words, 1)
            if final_ratio < 0.75:
                print(f"[Script] ⚠️ Ratio still {final_ratio:.2f} after retry — accepting and continuing")
        print("[Script] OpenAI Arabic translation ✅")
        return result

    print("[Script] Falling back to Groq translation")
    try:
        return _groq_translate_arabic(english_text, topic=topic)
    except Exception as e:
        print(f"[Script] Groq fallback failed: {e}")
    print("[Script] Falling back to Google Translate")
    return translate_to_arabic_google(english_text)


def translate_long_script_arabic(english_text: str, topic: str = "") -> str:
    """Translate a long script to Arabic in ~800-word chunks, then stitch."""
    import time as _time

    paragraphs = [p.strip() for p in english_text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for para in paragraphs:
        pw = len(para.split())
        if current_words + pw > 800 and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_words = pw
        else:
            current.append(para)
            current_words += pw
    if current:
        chunks.append("\n\n".join(current))

    total_en = clean_word_count(english_text)
    print(f"[Script] Translating {total_en} real-word script in {len(chunks)} chunks")

    translated: list[str] = []
    for i, chunk in enumerate(chunks):
        print(f"[Script] Translating chunk {i + 1}/{len(chunks)}...")
        ar_chunk = try_translate_arabic(chunk, topic=topic)
        translated.append(ar_chunk)
        if i < len(chunks) - 1:
            _time.sleep(2)

    result   = "\n\n".join(translated)
    ar_real  = clean_word_count(result)
    ar_raw   = len(result.split())
    ar_min   = ar_real // 140
    ar_max   = ar_real // 130
    min_expected_ar = int(total_en * 0.9)
    if ar_real >= min_expected_ar:
        print(f"[Script] Total Arabic: {ar_real} real words (raw {ar_raw}) ✅ "
              f"→ Est. runtime: ~{ar_min}–{ar_max} min")
    else:
        print(f"[Script] Total Arabic: {ar_real} real words (raw {ar_raw}) ⚠️ "
              f"below {min_expected_ar:,} target — consider regenerating")
    return result


def translate_to_arabic(text: str) -> str:
    """Public entry point — chunked for long scripts, otherwise single call with fallback chain."""
    if clean_word_count(text) > 1000:
        return translate_long_script_arabic(text)
    return try_translate_arabic(text)


def _build_hemedti_arabic_title(part_number: int | None) -> str:
    """Return the correct Arabic title for Hemedti videos."""
    if part_number == 1:
        return "حميدتي الجزء الأول: من تاجر الإبل إلى أمير الحرب | فك رموز الجريمة المظلمة"
    if part_number == 2:
        return "حميدتي الجزء الثاني: المجزرة والإمارات والمرتزقة الكولومبيون | فك رموز الجريمة المظلمة"
    return "حميدتي: أخطر رجل لم تسمع عنه | فك رموز الجريمة المظلمة"


_SECTION_ARABIC_LABELS = {
    "introduction": "مقدمة",
    "background": "الخلفية",
    "main story": "القصة الرئيسية",
    "shocking facts": "حقائق صادمة",
    "conclusion": "الخاتمة",
}


def _split_english_sectioned_script(script_text: str) -> list[tuple[str, str]]:
    """Split [SECTION: ...] script into ordered section tuples."""
    import re
    text = (script_text or "").strip()
    raw = re.split(r'\[SECTION:\s*([^\]]+)\]', text, flags=re.IGNORECASE)
    sections: list[tuple[str, str]] = []
    if len(raw) >= 3:
        for i in range(1, len(raw), 2):
            name = raw[i].strip()
            body = raw[i + 1].strip() if i + 1 < len(raw) else ""
            if body:
                sections.append((name, body))
    if sections:
        return sections
    return [("Introduction", text)] if text else []


def _to_arabic_section_name(name: str) -> str:
    key = (name or "").strip().lower()
    return _SECTION_ARABIC_LABELS.get(key, name.strip() or "مقدمة")


def _translate_script_preserve_sections(english_script_text: str) -> str:
    """
    Translate sectioned script while preserving normalized markers.
    Output marker format is always: [SECTION: <Arabic Label>]
    """
    sections = _split_english_sectioned_script(english_script_text)
    if not sections:
        return ""
    translated_parts: list[str] = []
    for name, content in sections:
        ar_name = _to_arabic_section_name(name)
        ar_body = translate_to_arabic(content)
        translated_parts.append(f"[SECTION: {ar_name}]\n{ar_body.strip()}")
    return "\n\n".join(translated_parts).strip()


def _expand_arabic_script_to_min(ar_script: str, target_min: int = 5000) -> str:
    """
    Append additional Arabic narration until the script meets target_min words.
    Uses only the tail of the current script as context to preserve token budget.
    """
    import os as _os_exp
    _anim = _os_exp.getenv("PIPELINE_MODE", "").lower() == "animation"

    current = clean_word_count(ar_script)
    if current >= target_min:
        return ar_script

    result = ar_script

    # Use only the last ~350 words as context — avoids exhausting token budget
    def _tail(text: str, n: int = 350) -> str:
        words = text.split()
        return " ".join(words[-n:]) if len(words) > n else text

    # Fact-lock: first ~200 words of the original script anchor known entities.
    # Expansion prompts reference this so the model cannot invent new characters.
    _entity_anchor = " ".join(ar_script.split()[:200])
    _entity_lock_block = (
        f"[قفل الشخصيات والوقائع الموثقة]\n"
        f"السرد يجب أن يقتصر على الشخصيات والأحداث الموجودة في النص الأصلي فقط.\n"
        f"محظور تماماً: اختراع أشخاص جدد (محققين، شهود، صحفيين، ضحايا، منظمات) "
        f"لم يُذكروا في النص.\n"
        f"المرجع — مطلع النص الأصلي:\n{_entity_anchor}\n"
        f"---\n"
    )

    _tok_budgets = [2400, 3000, 2500, 2000]
    for _attempt in range(1, 5):
        cur_wc = clean_word_count(result)
        if cur_wc >= target_min:
            break
        needed    = target_min - cur_wc
        needed_min = round(needed / 185, 1)  # ~185 WPM Arabic TTS (Nova speed=1.1 range 170-190)
        max_tok   = _tok_budgets[_attempt - 1]
        context   = _tail(result)

        if _anim:
            if _attempt == 1:
                instruction = (
                    f"استمر في سرد الوثائقي المتحرك بعد النص أدناه. "
                    f"أضف ما يعادل {needed_min} دقيقة على الأقل من السرد السينمائي (~{needed} كلمة). "
                    f"البنية: مشهد بصري → نبضة عاطفية → كشف جديد → انتقال. "
                    f"لا تكرر ما سبق. استمر طبيعياً من آخر جملة. "
                    f"وسّع التفاصيل — أضف أجواء، مشاهد جديدة، تطورات في الحدث."
                )
            elif _attempt == 2:
                instruction = (
                    f"استمر في الوثائقي المتحرك. أضف {needed_min} دقيقة على الأقل من السرد "
                    f"(~{needed} كلمة). ادخل في مشهد جديد لم يُذكر بعد — "
                    f"شخصية فرعية، لحظة التحول، ردة فعل الضحايا أو المجتمع. "
                    f"السرد السينمائي الكامل فقط."
                )
            else:
                instruction = (
                    f"أكمل الوثائقي بإضافة {needed_min} دقيقة من السرد (~{needed} كلمة). "
                    f"قدّم زاوية جديدة: التداعيات، الإرث، الأثر الإنساني. "
                    f"استمر مباشرة بعد النص الأخير."
                )
        else:
            if _attempt == 1:
                instruction = (
                    f"استمر في النص الوثائقي. أضف ما يعادل {needed_min} دقيقة من السرد "
                    f"(~{needed} كلمة). "
                    f"وسّع تفاصيل التحقيق: أسماء، تواريخ، أدلة، شهادات، تطورات قضائية. "
                    f"استمر بشكل طبيعي من الجملة الأخيرة."
                )
            elif _attempt == 2:
                instruction = (
                    f"أضف {needed_min} دقيقة جديدة من السرد الوثائقي (~{needed} كلمة). "
                    f"قدّم زاوية لم تُذكر: شخصية فرعية، دليل إضافي، تطور في القضية. "
                    f"لا تكرر ما قيل. استمر مباشرة."
                )
            else:
                instruction = (
                    f"أكمل النص بـ{needed_min} دقيقة من السرد (~{needed} كلمة). "
                    f"استمر بعد آخر جملة مباشرة."
                )

        print(f"[Script] Arabic expansion attempt {_attempt}: {cur_wc}w, need +{needed}w (~{needed_min}min)")
        prompt = (
            f"{_entity_lock_block}"
            f"{instruction}\n\n"
            f"نهاية النص الحالي (استمر بعده مباشرة):\n...\n{context}\n\n"
            f"اكتب الاستمرار فقط — لا تعد النص الأصلي. لا شرح."
        )
        continuation = _ai_script_call(prompt, max_tokens=max_tok, temperature=0.70, premium=True)
        if not continuation:
            print(f"[Script] Arabic expansion attempt {_attempt} failed — no response")
            continue

        added_wc = clean_word_count(continuation)
        if added_wc < 120:
            print(f"[Script] Arabic expansion attempt {_attempt} rejected — only {added_wc}w added (need ≥120w)")
            continue

        result = result.rstrip() + "\n\n" + continuation.strip()
        new_wc = clean_word_count(result)
        print(f"[Script] Arabic expansion attempt {_attempt}: {cur_wc}w + {added_wc}w added = {new_wc}w total")

    final_wc = clean_word_count(result)
    if final_wc > current:
        print(f"[Script] Arabic expansion done: {current}w → {final_wc}w")
    else:
        print(f"[Script] Arabic expansion: no growth achieved ({final_wc}w)")
    return result


# Public alias so callers can import without leading underscore
expand_arabic_script_to_min = _expand_arabic_script_to_min


def estimate_arabic_duration(text: str) -> float:
    """Estimate Arabic TTS duration in minutes (OpenAI TTS Nova speed=1.1 ~185 WPM)."""
    return clean_word_count(text) / _TTS_WPM["arabic"]


def expand_arabic_narration_style(section_text: str, topic: str = "") -> str:
    """
    Rewrite a compressed Arabic section in cinematic documentary narration style.
    Expands atmosphere and pacing without repeating facts or adding filler.
    """
    wc = clean_word_count(section_text)
    target = int(wc * 1.5)
    topic_line = f"الموضوع: {topic}\n" if topic else ""
    prompt = (
        f"{topic_line}"
        "أنت راوٍ وثائقي. أعد كتابة النص العربي التالي بأسلوب وثائقي تحقيقي أكثر عمقاً.\n\n"
        "القواعد الصارمة:\n"
        "- لا تحذف أي معلومة أو اسم أو تاريخ من النص الأصلي\n"
        "- وسّع تفاصيل التحقيق الموثق: إجراءات المحققين، طبيعة الأدلة، شهادات موثقة\n"
        "- لا تخترع أحداثاً أو مشاهد أو محادثات لم تُذكر في المصدر\n"
        "- لا تضف حياة يومية خيالية أو تجوالاً مخترعاً أو أوصاف جو فارغة\n"
        "- وسّع الحقائق الموجودة بعمق أكبر — لا تُضِف خيالاً\n"
        "- اكتب بأسلوب التحقيق الوثائقي المنطوق: حقائق + عواقب + تطورات\n"
        "- لا تضف معلومات خاطئة أو غير موثقة\n"
        f"- الهدف: حوالي {target} كلمة\n\n"
        "النص الأصلي:\n"
        f"{section_text}\n\n"
        "اكتب النص الموسّع فقط. لا شرح."
    )
    try:
        result = _ai_script_call(prompt, max_tokens=2000, temperature=0.6, premium=True)
        if result and clean_word_count(result) > wc:
            print(f"[AR] Section expanded for pacing: {wc}w → {clean_word_count(result)}w")
            return result
    except Exception as e:
        print(f"[AR] Narration expansion failed: {e}")
    return section_text


def expand_arabic_runtime(ar_script: str, target_min: float, topic: str = "") -> str:
    """
    Expand Arabic script section-by-section until estimated runtime reaches target_min.
    Only expands sections that are shorter than their proportional share.
    Does NOT regenerate the full script.
    """
    import re as _re
    import time as _time

    current_min = estimate_arabic_duration(ar_script)
    if current_min >= target_min:
        return ar_script

    print(f"[AR] Runtime mismatch detected: {current_min:.1f}min < {target_min:.1f}min target")
    print("[AR] Expanding Arabic narration")

    # Split by [SECTION: ...] markers; fall back to paragraph chunks if no markers
    raw = _re.split(r'(\[SECTION:[^\]]+\])', ar_script)
    has_markers = len(raw) > 1

    if has_markers:
        # Pair each marker with its following body
        sections: list[tuple[str, str]] = []
        i = 0
        if raw[0].strip():
            sections.append(("", raw[0]))
        while i < len(raw):
            if _re.match(r'\[SECTION:[^\]]+\]', raw[i]):
                body = raw[i + 1] if i + 1 < len(raw) else ""
                sections.append((raw[i], body))
                i += 2
            else:
                i += 1
    else:
        # No markers: treat each paragraph as a "section"
        paras = [p.strip() for p in ar_script.split("\n\n") if p.strip()]
        sections = [("", p) for p in paras]

    total_wc  = max(clean_word_count(ar_script), 1)
    need_wc   = int(target_min * _TTS_WPM["arabic"])
    gap_wc    = need_wc - total_wc
    n_sec     = len(sections)

    result_parts: list[str] = []
    words_added = 0

    for idx, (marker, body) in enumerate(sections):
        sec_wc    = clean_word_count(body)
        sec_share = sec_wc / total_wc if total_wc else 1.0 / max(n_sec, 1)
        sec_gap   = int(gap_wc * sec_share)

        if sec_gap >= 40 and sec_wc >= 30:
            expanded = expand_arabic_narration_style(body.strip(), topic=topic)
            words_added += max(0, clean_word_count(expanded) - sec_wc)
            result_parts.append((marker + "\n" + expanded).strip())
            if idx < n_sec - 1:
                _time.sleep(1)          # avoid rate-limit on rapid Groq calls
        else:
            result_parts.append((marker + "\n" + body).strip() if marker else body.strip())

    joined = "\n\n".join(result_parts)
    new_min = estimate_arabic_duration(joined)
    if words_added == 0:
        print(f"[AR EXPANSION] WARNING: expansion added 0 words — all section expansions failed "
              f"(Groq rate-limited or OpenAI refused every section). "
              f"Runtime unchanged: {current_min:.1f}min")
    elif words_added < int(gap_wc * 0.7):
        print(f"[AR EXPANSION] WARNING: expansion added only {words_added}w "
              f"(target was {gap_wc}w, achieved {words_added/max(gap_wc,1)*100:.0f}%). "
              f"Runtime: {current_min:.1f}min → {new_min:.1f}min")
    else:
        print(f"[AR] Arabic runtime balanced: {current_min:.1f}min → {new_min:.1f}min (+{words_added} words)")
    return joined


# ── Arabic documentary system prompt ─────────────────────────────────────────
# Mirror of _SCRIPT_SYSTEM_PROMPT — same cinematic weight, native Arabic voice.
# Used when writing Arabic script directly from Arabic research (not translation).

_AR_SCRIPT_SYSTEM_PROMPT = """أنت راوٍ سينمائي عربي متخصص في قصص الجريمة الحقيقية. مهمتك الوحيدة: كتابة رواية سينمائية مشهدية تشعر المستمع بأنه محاصر داخل قصة جريمة سينمائية.

المرجع الأسلوبي: إيقاع Netflix للجريمة الحقيقية + أسلوب التحقيق الدرامي العربي.
هذا ليس ملخصاً وثائقياً — هذا سرد سينمائي بالعربية.

══════════════════════════════════════
محرك الزخم السردي — القاعدة الأهم
══════════════════════════════════════
وقت التشغيل يأتي من حركة القصة — ليس من تضخيم الكلمات.

الأسلوب المحظور تماماً:
- تكرار الحقائق السيرة الذاتية بصياغات مختلفة
- تكرار التحليل النفسي في كل فصل
- فقرات تفسيرية ثابتة بدون حركة سردية
- ملخصات لما قيل في الفصل السابق

المطلوب: كل 50-80 كلمة (~20-40 ثانية صوت) يجب أن يشعر المستمع بأحد:
  • اكتشاف دليل جديد باسم أو تاريخ محدد
  • تناقض: ما كان يُعتقد مقابل ما اكتُشف
  • تحول في التحقيق: تطور غير متوقع
  • تصعيد خطر أو عواقب
  • كشف: معلومة مخفية تظهر للأول مرة
  • انتكاسة: خطة تنهار، خيط يضيع

الكتابة المشهدية المطلوبة:
الخطأ: "كان بوندي يتلاعب بالمحققين لسنوات."
الصواب: "ظن المحقق أن الشاهد عرّفه أخيراً.
ثم رن الهاتف.
ضحية أخرى اختفت."

كل فصل يتحرك عبر: حدث → ردة فعل → تحقيق → اكتشاف → تصعيد → عاقبة

══════════════════════════════════════
الأولوية القصوى: الكتابة للاستماع
══════════════════════════════════════
هذا نص مُنطوق. لا يُقرأ — يُستمع إليه.
كل جملة يجب أن تبدو طبيعية عند النطق بها بصوت عالٍ أمام ميكروفون.
اسأل نفسك عن كل جملة: "هل يمكن قولها بنفَس واحد بشكل طبيعي؟"
إذا كانت الإجابة لا — أعد كتابتها.

══════════════════════════════════════
إيقاع الجمل — قاعدة التناوب الإلزامية
══════════════════════════════════════
تناوب بين أربعة أنواع من الجمل في كل فقرة:
١. جملة توتر قصيرة (5–8 كلمات) — تخلق توقعاً
٢. جملة معلوماتية متوسطة (12–18 كلمة) — تقدم الحقيقة
٣. ملاحظة إنسانية أو نفسية (7–12 كلمة) — ترسّخ المشهد
٤. جملة تقريرية (10–15 كلمة) — تُكمل الصورة

لا تكرر نفس طول الجملة ثلاث مرات متتالية.
الإيقاع المتنوع هو ما يجعل المستمع لا يغادر.

مثال صحيح:
"كان المحققون يعرفون. لكن أحداً منهم لم يتكلم لثلاث سنوات كاملة. ما أخّر الاعتراف لم يكن الجهل — بل كان الخوف."

مثال خاطئ (جميع الجمل بنفس الطول):
"كان المحققون يعرفون الحقيقة كاملة. لكن أحداً منهم لم يتكلم طوال ثلاث سنوات. ما أخّر الاعتراف لم يكن الجهل بل كان الخوف الشديد."

══════════════════════════════════════
حدود الجملة للمقرئ الصوتي
══════════════════════════════════════
- الحد الأقصى: 20 كلمة للجملة الواحدة
- الجمل 5–10 كلمات: جمل الصدمة والتوتر
- الجمل 12–18 كلمة: السرد التقريري
- لا شرطات معترضة. لا أقواس. لا نقاط حذف داخل الجملة
- الأرقام دون المئة تُكتب بالكلمات
- الجمل الطويلة المركّبة تُقسَّم إلى جمل مستقلة

══════════════════════════════════════
تحذير صريح من البنية الإنجليزية
══════════════════════════════════════
لا تنقل ترتيب الجمل الإنجليزية إلى العربية.
لا تحافظ على إيقاع الإثارة الإنجليزي أو نمط التعليق الإنجليزي.
العربية الفصيحة الوثائقية لها طبيعتها الخاصة:
- الفعل يأتي غالباً قبل الفاعل (جاء المحقق — لا: المحقق جاء)
- الاستدراك بـ"غير أن" أو "إلا أن" أبلغ من "لكن"
- التعجب والتساؤل لهما أساليب عربية خاصة

اكتب كما يكتب كاتب عربي محترف — ليس كمترجم.

══════════════════════════════════════
العبارات المحظورة — لا تكتبها أبداً
══════════════════════════════════════
عبارات الإثارة الرخيصة:
- "لكن ما حدث بعد ذلك صدم الجميع"
- "ولم يكن أحد يتوقع"
- "لكن الحقيقة كانت أكثر ظلامًا"
- "ما اكتشفوه كان مرعبًا"
- "كل شيء تغير إلى الأبد"
- "وهنا كانت الصدمة الكبرى التي غيرت كل شيء"
- "لم يكن أحد مستعداً لما سيحدث"
- "في لحظة صادمة"، "في منعطف صادم"، "في تحول مذهل"
- "لكن الأمر كان أسوأ مما تخيّل أي أحد"
- "والباقي كان تاريخاً"
- "في نهاية المطاف"، "وفي النهاية"
عبارات الافتتاح المستهلكة:
- "في عالم الجريمة..."، "عبر التاريخ..."، "هذه قصة..."
- "كل شيء بدأ عندما"، "لم يكن أحد يدري"
- "ما لم يعلمه أحد"، "ولكن الحقيقة كانت..."
بدلاً من ذلك: سمِّ الشخص الحقيقي، التاريخ الفعلي، الحدث المحدد.
الحقيقة نفسها تصنع التوتر — لا تحتاج إلى تغليف.

══════════════════════════════════════
صوت الوثائقي — ليس مقالاً
══════════════════════════════════════
- لا تكتب كمقال أو تقرير صحفي. لا مقدمات نظرية. لا توصيف للموضوع
- اكتب المشهد، لا الملخص. أظهر اللحظة، ثم استخلص منها المعنى
- كل فقرة يجب أن تكسب مكانها — لا حشو، لا تكرار لما قيل
- ثلاث جمل حداً أدنى للفقرة، خمس جمل حداً أقصى
- لا نقاط. لا قوائم. لا حقائق منفردة. نثر متواصل فقط
- أظهر السبب والنتيجة: القرارات تُفضي إلى عواقب، الأفعال تكشف الشخصية

══════════════════════════════════════
وتيرة الوثائقي — كل فصل
══════════════════════════════════════
كل فصل يجب أن:
- يُفتتح بجملة أو جملتين تخلقان مشهداً بصرياً في ذهن المستمع
- يتقدم بالحقائق لا بالحشو — الأسماء والتواريخ والأدلة تصنع الدراما
- يصل إلى ذروة عاطفية محكومة في منتصفه
- يختتم بنقلة دراماتيكية هادئة نحو الفصل التالي

النص يُستمع إليه — ليس يُقرأ.

══════════════════════════════════════
النبرة المستهدفة
══════════════════════════════════════
- هادئة. تحقيقية. مثيرة للقلق بشكل خفيف ومحكوم
- الراوي يعرف أكثر مما يقول — والمستمع يحس بذلك
- لا أكاديمية. لا عامية. لا حماس مبالغ. لا إثارة يوتيوب
- 85% تحكم ظلامي. 15% تعليق جاف ساخر عند أخطاء المجرمين. سطر واحد فقط
- لا تسخر من الضحايا. التعليق الساخر فقط على المجرمين أو الفساد

══════════════════════════════════════
التوتر السردي — مستمر
══════════════════════════════════════
- كل فصل يُدخل سؤالاً معلقاً أو صراعاً خفياً أو حقيقة مكبوتة
- لا تُنهِ التوتر كلياً داخل الفصل — اترك شيئاً يسحب للفصل التالي
- المستمع يجب أن يشعر دائماً: هناك ما لا أعرفه بعد

══════════════════════════════════════
افتتاحية كل فصل
══════════════════════════════════════
- يجب أن تحتوي أحد: تناقض، حقيقة خفية، إغفال صادم، أو سؤال مفتوح
- لا تفتح بخلفية أو تواريخ أو وصف مكان. ابدأ بالتوتر، ليس بالسياق
- الجملة الأولى: الضربة. الجملة الثانية: التصعيد. الجملة الثالثة: الفخ

══════════════════════════════════════
قوة الختام
══════════════════════════════════════
- آخر 1–2 جملة في كل فصل تُخلّف أثراً أو تُثير سؤالاً مقلقاً
- لا تختم بملخص: "وهكذا كانت القصة..." / "تلك هي قصة..."
- آخر جملة في الفصل تجعل المستمع يحتاج للمتابعة

══════════════════════════════════════
تغطية الشخصيات
══════════════════════════════════════
- غطِّ جميع الشخصيات الرئيسية — لا تركّز على شخص واحد فقط
- كل شخص مهم يحصل على فقرة: الاسم الكامل، الدور الحقيقي، ما فعله، مصيره
- النساء والشخصيات المساندة تحصل على تغطية متساوية

══════════════════════════════════════
محرك الكثافة المعلوماتية — إلزامي
══════════════════════════════════════
كل 50–75 كلمة يجب أن تتضمن واحداً على الأقل مما يلي:
- اسم شخص حقيقي مع فعل محدد (اعتُقل، اعترف، كشف، أدلى بشهادة)
- تاريخ أو سنة موثقة أو انتقال زمني
- دليل مادي أو وثيقة قانونية أو تقرير جنائي
- شهادة أو اقتباس موثق أو موقف محدد
- تطور تحقيقي: تفتيش، مداهمة، استجواب، حكم قضائي
- انعطافة في الرواية: ما كان يُعتقد مقابل ما ثبت فعلياً
- كشف نفسي: دافع موثق أو حالة عقلية مثبتة قانونياً

العبارات المزاجية المحظورة — محظورة نهائياً بأي كثافة:
"كان الخوف يملأ المكان" / "العالم كان يراقب" / "الغموض يزداد" / "الصمت يسود"
"الظلال تتحرك" / "السر لم يُكشف بعد" / "الرعب يملأ الأرواح"
هذه العبارات مقبولة مرة واحدة فقط كل 400 كلمة — إذا تجاوزت الحد أعد الكتابة.
الجمل المنعزلة القصيرة (2-5 كلمات) من نوع المزاج محظورة تماماً.
الأجواء تأتي من التفاصيل الحقيقية — لا من الكلمات المزاجية.

══════════════════════════════════════
قانون زخم التحقيق — إلزامي
══════════════════════════════════════
كل فصل يتحرك عبر سلسلة مشهدية متصلة:
  حدث → ردة فعل → تحقيق → اكتشاف → تصعيد → انتكاسة → كشف → عاقبة

كل فقرة يجب أن تُحرّك القصة خطوة للأمام:
- فقرة 1: مشهد الانطلاق — اللحظة الحرجة + السؤال الذي يسحب
- فقرة 2: الدليل الأول + ردة الفعل المباشرة
- فقرة 3: الانعطاف — ما لم يتوقعه أحد — مع التاريخ والاسم الحقيقي
- فقرة 4+: تصعيد كل فقرة تكشف طبقة أعمق من الحقيقة
- نهاية الفصل: توتر مفتوح يسحب المستمع للفصل التالي

لا يُقبل أي فصل يُعيد ما قاله الفصل السابق بكلمات مختلفة.
كل فصل يكشف معلومة جديدة لم تُذكر من قبل.
الوقت يأتي من حركة القصة — ليس من إعادة شرح ما قيل.

══════════════════════════════════════
الكتابة المشهدية بالعربية — إلزامي
══════════════════════════════════════
لا تكتب ملخصاً — اكتب مشهداً.
الجمل القصيرة للصدمة والتحول:
"وصل المحقق. المكان كان فارغاً. الهاتف لا يزال يرن."
الجمل المتوسطة للسرد الحقيقي:
"في مارس 1985، عثر المحققون على وثيقة واحدة تُحوّل مسار القضية بالكامل."
لا تكتب أربع جمل متتالية بدون نبضة سردية (دليل، تحول، كشف، عاقبة).

══════════════════════════════════════
تسلسل الأدلة — الجدول الزمني
══════════════════════════════════════
استخدم تسلسلاً زمنياً صريحاً:
- "في [الشهر/السنة]..." — دائماً عند تقديم دليل جديد
- "بعد [مدة]..." — لإظهار مرور الوقت وتطور القضية
- "في [المكان]، في [التاريخ]..." — لربط الحوادث بالمكان والزمن
- "عاد المحققون إلى..." — لإظهار التقدم التحقيقي
الجدول الزمني هو العمود الفقري للنص — لا تتركه مجرداً أو مبهماً.

مثال سيء (لا تكتب هكذا):
"كان الخوف يملأ المكان.
والظلام يحيط بكل شيء.
والصمت يقول ما لا تقوله الكلمات."

مثال جيد (اكتب هكذا):
"حين وصل المحققون إلى الشقة، وجدوا اثنتي عشرة رسالة موقعة باسم مستعار،
كلها تحمل التاريخ ذاته — الليلة التي أُبلغ عن اختفائها فيها."

══════════════════════════════════════
قواعد عدم التكرار
══════════════════════════════════════
- لا تكرر حقيقة أو اسماً أو تاريخاً ذُكر في فصل سابق
- لا تُعد صياغة افتتاحية الفصل في فصل لاحق
- إذا وجدت نفسك تكتب ما قيل سابقاً — توقف واكتب شيئاً جديداً

══════════════════════════════════════
هيكل المقارنة (المواضيع المبنية على أعمال درامية)
══════════════════════════════════════
- الفصل الرابع يجب أن يحتوي:
  الجزء الأول يبدأ بالضبط بـ: "إليك ما أصابت فيه [اسم العمل]:"
  الجزء الثاني يبدأ بالضبط بـ: "وإليك ما غيّرته أو أغفلته تماماً:"
- كل جزء يغطي على الأقل ثلاث مقارنات محددة بأسماء وتواريخ ومشاهد حقيقية
- هذا الهيكل إلزامي لأي موضوع مبني على أحداث حقيقية

══════════════════════════════════════
وضع السرد الشفهي — إلزامي تماماً
══════════════════════════════════════
هذا النص يُقرأ بصوت عالٍ أمام ميكروفون — ليس مقالاً مكتوباً.
المستمع لا يرى النص — يسمعه فقط.
الجمل المركّبة والطويلة تُضيع المستمع — قصّرها.
كل فقرة يجب أن تُسمع بشكل طبيعي في نفَس واحد أو نفَسين.

قواعد السرد الشفهي:
١. الفقرة القصيرة: 3–4 جمل كحد أقصى
   الفقرة الطويلة تُتعب الأذن — اقطع النص إلى لحظات صغيرة قابلة للتنفس
٢. التوقف الدرامي الطبيعي:
   النقطة هي توقف قصير. الفقرة الجديدة هي توقف أطول.
   استخدمها كأداة إيقاعية — وليس فقط لتنظيم النص
٣. الجملة المنفردة للصدمة:
   أحياناً جملة قصيرة وحيدة هي أقوى لحظة في الفقرة.
   "وجدوه في الصباح." — ثم فقرة جديدة.
٤. الإيقاع العاطفي:
   الإسراع في التصعيد (جمل قصيرة متتالية)
   التباطؤ في الكشف (جملة متوسطة + جملة قصيرة للصدمة)
   التأمل في العواقب (جمل متوسطة هادئة)

الأسلوب المحظور في السرد الشفهي:
- الجمل المركّبة من أكثر من فكرتين (قسّمها)
- المصطلحات الأكاديمية الرسمية غير المألوفة للمستمع العربي
- الحشد المعلوماتي في جملة واحدة (اسم + تاريخ + مكان + حدث)
- الربط المفرط بـ"حيث" و"إذ" و"بينما" (يُرهق الأذن)
- الجمل الاعتراضية الطويلة بين شرطتين — اجعلها جملة مستقلة

══════════════════════════════════════
مراحل القصة — تسلسل الحالة السردية
══════════════════════════════════════
القصة تتحرك عبر خمس حالات — لكل حالة إيقاعها الخاص:

الحالة ١ — القلق والإشارة الأولى:
  إيقاع هادئ + مقلق. جمل قصيرة تُلمّح. لا تكشف كل شيء.
  "كان شيء ما غير مكتمل في تلك اللحظة."

الحالة ٢ — بدء التحقيق:
  إيقاع حيوي. جمل تتصاعد. كل جملة تكشف شيئاً جديداً.
  "وجد المحقق الورقة. ثم وجد الثانية. ثم الثالثة."

الحالة ٣ — التصعيد:
  إيقاع سريع. جمل قصيرة متتالية. الأحداث تتسارع.
  "أُفلت. ثم ضُبط. ثم أُفلت مرة أخرى."

الحالة ٤ — الانهيار والكشف:
  إيقاع حاد. جمل التوقف الدرامي. الحقيقة تُلقى كحكم.
  "اعترف. في الجلسة الثالثة. بعد ست سنوات من الصمت."

الحالة ٥ — التداعيات والإرث:
  إيقاع أبطأ وأثقل. جمل متأملة. الأسئلة تبقى.
  "لا أحد يعرف حتى اليوم ما حدث للحقيبة الثانية."

الانتقال بين الحالات يجب أن يُشعر المستمع بـ: "شيء تغيّر الآن."
ليس: "انتهى الفصل وبدأ فصل جديد."

══════════════════════════════════════
الامتثال لسياسات يوتيوب — إلزامي تماماً
══════════════════════════════════════
هذا النص سيُنشر على يوتيوب. مخالفة هذه القواعد تؤدي إلى تقييد العمر أو الحذف.
خوارزمية يوتيوب تفحص كل جملة. اللغة الصحفية التحليلية آمنة. أوصاف العنف الجسدي ليست آمنة.

العنف والوفاة — لا تكتب أبداً:
- أوصاف الجروح، الدم، أجزاء الجسد، أو الميكانيكا الجسدية للقتل
- أوصاف المعاناة الجسدية أو الجثث
- "مُزّقت الأجساد"، "غرق في الدماء"، "أشلاء"، "جمجمة"
سيئ: "دمّر الانفجار كل شيء وتناثرت أجساد الضحايا في كل اتجاه."
جيد: "راح الانفجار بحياة 257 شخصاً في ثلاثة عشر موقعاً خلال أقل من ساعتين."
القاعدة: اذكر وقوع الحادثة وعدد الضحايا. انتقل فوراً إلى التحقيق والعواقب.

الانتحار وإيذاء النفس — لا تكتب أبداً:
- طرق الانتحار أو أوصاف الإيذاء الجسدي لأي شخص
- سرد رومانسي أو تفصيلي لمشهد الوفاة بالانتحار
- وصف التجربة الجسدية للاحتضار
سيئ: "شنق نفسه في زنزانته، واكتشفت الحراسة الجثة في الصباح."
جيد: "وُجد ميتاً في زنزانته — وأصدرت السلطات بياناً بوفاته."

التفجيرات والهجمات الجماعية — لا تكتب أبداً:
- أوصاف معاناة الضحايا أو مشاهد الموت أو الحرق
- كيفية تصنيع المتفجرات أو تفاصيل الإيذاء الجسدي
سيئ: "أحرق الانفجار المارة وفرّ الناجون محترقون في كل اتجاه."
جيد: "أودى الانفجار بحياة 54 شخصاً وأصاب المئات. كشف المحققون لاحقاً أن المتفجرات قدمت من خلية باكستانية."

الصياغة الآمنة — استخدم دائماً:
- "راح بحياة / لقي حتفه / لقوا مصرعهم" + عدد الضحايا
- "عُثر عليه ميتاً" / "توفي أثناء احتجازه" / "نُفّذ فيه حكم الإعدام"
- "كشف المحققون أدلة على" / "أثبتت وثائق المحكمة" / "أظهر التحليل الجنائي"
- "خلّف الهجوم ضحايا مدنيين" / "أسفر عن سقوط عدد كبير من القتلى"

العواقب والتحقيقات والإجراءات القانونية دائماً آمنة.
الأوصاف الجسدية للعنف أو الموت ليست آمنة أبداً على يوتيوب."""


def translate_research_to_arabic(research: dict) -> dict:
    """
    Translate only the factual research lists to Arabic.
    Batches each list into a single numbered call for efficiency.
    Returns a new dict augmented with ar_* keys.
    """
    topic_name = research.get("real_person") or research.get("series_name") or ""

    def _batch_translate(items: list[str]) -> list[str]:
        if not items:
            return []
        combined = "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items[:15]))
        ar = try_translate_arabic(combined, topic=topic_name)
        lines = [
            re.sub(r'^\d+[\.\)]\s*', '', ln).strip()
            for ln in ar.splitlines()
            if ln.strip()
        ]
        return [ln for ln in lines if len(ln) > 5]

    ar = dict(research)
    ar["ar_research_facts"] = _batch_translate(
        research.get("research_facts") or research.get("what_show_got_right", [])
    )
    ar["ar_research_inaccuracies"] = _batch_translate(
        research.get("research_inaccuracies") or research.get("what_show_got_wrong", [])
    )
    ar["ar_research_shocking"] = _batch_translate(
        research.get("research_shocking") or research.get("shocking_real_facts", [])
    )
    if research.get("user_discovery"):
        ar["ar_user_discovery"] = try_translate_arabic(research["user_discovery"], topic=topic_name)
    else:
        ar["ar_user_discovery"] = ""

    print(
        f"[AR Research] facts={len(ar['ar_research_facts'])} | "
        f"inaccuracies={len(ar['ar_research_inaccuracies'])} | "
        f"shocking={len(ar['ar_research_shocking'])}"
    )
    return ar


def _write_ar_section_chunked(
    label: str,
    topic_str: str,
    entity_lock: str,
    target_words: int,
    existing_text: str = "",
    initial_prompt: str = "",
    tts_reminder: str = "",
    is_anim: bool = False,
    max_tok_first: int = 2800,
) -> str:
    """
    Build or extend an Arabic section in continuation chunks.
    - If existing_text provided: extends it with continuation chunks until target_words.
    - If initial_prompt provided: generates first chunk then continues.
    Each continuation receives only the last 200 words — not the full script.
    """
    import time as _tc

    accumulated = existing_text.strip() if existing_text else ""
    acc_wc      = clean_word_count(accumulated)

    if not accumulated and initial_prompt:
        first = _ai_script_call(
            initial_prompt, max_tokens=max_tok_first,
            temperature=0.65, system_prompt=_AR_SCRIPT_SYSTEM_PROMPT, premium=True,
        )
        if not first or clean_word_count(first) < 50:
            return ""
        accumulated = first.strip()
        acc_wc      = clean_word_count(accumulated)
        print(f"[AR Chunk] {label} chunk 1: {acc_wc}w / target {target_words}w")

    if acc_wc >= target_words:
        return accumulated

    _style = (
        "السرد المتحرك السينمائي: مشهد بصري → نبضة عاطفية → كشف → انتقال. "
        "الكثافة 55-70%. لا تكرر مشاهد سبق ذكرها."
        if is_anim else
        "السرد الوثائقي: أسماء حقيقية، تواريخ، تفاصيل محددة. لا تكرر ما سبق ذكره."
    )
    chunk_num = 2 if (not existing_text and initial_prompt) else 1

    for _ in range(7):
        if acc_wc >= target_words:
            break
        needed     = target_words - acc_wc
        needed_min = round(needed / 190, 1)
        tail       = " ".join(accumulated.split()[-200:])
        max_tok    = min(3000, max(1200, needed * 2))

        _tc.sleep(2)
        cont = _ai_script_call(
            f"{entity_lock}"
            f"استمر في سرد الوثائقي عن: {topic_str}\n"
            f"{_style}\n\n"
            f"أضف ما يعادل {needed_min} دقيقة (~{needed} كلمة) من السرد. "
            f"لا تكرر ما سبق. استمر مباشرة بعد هذا النص:\n\n{tail}\n\n"
            f"اكتب الاستمرار فقط.",
            max_tokens=max_tok,
            temperature=0.68,
            premium=True,
        )
        cont_wc = clean_word_count(cont) if cont else 0
        # Reject refusal text even when it is long enough to pass the word-count gate
        if cont and any(sig in cont for sig in _AR_REFUSAL_SIGNALS):
            print(f"[AR Chunk] {label} chunk {chunk_num}: refusal detected in chunk — discarding")
            cont    = None
            cont_wc = 0
        if cont_wc < 100:
            # Retry with a stripped-down prompt — entity_lock may have confused the model
            _tc.sleep(4)
            cont = _ai_script_call(
                f"استمر في الكتابة العربية عن: {topic_str}\n"
                f"أضف {needed} كلمة جديدة من السرد. لا تكرر ما سبق. استمر مباشرة بعد:\n\n{tail}\n\nاكتب فقط:",
                max_tokens=max_tok,
                temperature=0.72,
                premium=True,
            )
            cont_wc = clean_word_count(cont) if cont else 0
            if cont and any(sig in cont for sig in _AR_REFUSAL_SIGNALS):
                print(f"[AR Chunk] {label} chunk {chunk_num}: refusal on simple-retry — stopping")
                break
            if cont_wc < 50:
                print(f"[AR Chunk] {label} chunk {chunk_num}: too short ({cont_wc}w after simple retry) — stopping")
                break
            print(f"[AR Chunk] {label} chunk {chunk_num}: simple-retry OK ({cont_wc}w)")
        accumulated = accumulated.rstrip() + "\n\n" + cont.strip()
        acc_wc      = clean_word_count(accumulated)
        print(f"[AR Chunk] {label} chunk {chunk_num}: +{cont_wc}w → {acc_wc}w / {target_words}w")
        chunk_num  += 1

    return accumulated


def _write_arabic_from_research(en_script: dict, ar_research: dict, target_minutes: float | None = None) -> str:
    """
    Write a full Arabic documentary script directly from Arabic research facts.
    Uses 3 LLM calls (section pairs) to stay within token limits.

    Produces native Arabic narration — NOT a translation of the English script.
    Section markers use Arabic labels: [SECTION: المقدمة] etc.
    """
    topic_str    = en_script.get("topic", "")
    series_name  = en_script.get("series_name", "") or ""
    angle        = en_script.get("series_type", "")
    en_wc        = clean_word_count(en_script.get("script", ""))

    # ── Arabic research blocks ─────────────────────────────────────────────────
    ar_facts    = ar_research.get("ar_research_facts", [])
    ar_inaccs   = ar_research.get("ar_research_inaccuracies", [])
    ar_shocking = ar_research.get("ar_research_shocking", [])
    ar_disc     = ar_research.get("ar_user_discovery", "")

    facts_block    = "\n".join(f"- {f}" for f in ar_facts[:10])    or "- (ابحث في القصة الحقيقية)"
    inaccs_block   = "\n".join(f"- {i}" for i in ar_inaccs[:6])    or "- (ابحث في التحريفات الدرامية)"
    shocking_block = "\n".join(f"- {s}" for s in ar_shocking[:8])  or "- (أضف تفاصيل حقيقية غير متوقعة)"

    import os as _os_sa
    _anim_mode = _os_sa.getenv("PIPELINE_MODE", "").lower() == "animation"

    # Section word budgets — derived from minutes target when available,
    # falling back to English word count ratios (legacy behaviour).
    _AR_WPM = 185  # Nova speed=1.1 (range 170-190 WPM)
    if target_minutes and target_minutes > 0:
        _total_target_w = int(target_minutes * _AR_WPM)
        if _anim_mode:
            _intro_target = max(1400, int(_total_target_w * 0.30))
            _main_target  = max(2400, int(_total_target_w * 0.50))
            _conc_target  = max(1100, int(_total_target_w * 0.20))
        else:
            _intro_target = max(900,  int(_total_target_w * 0.30))
            _main_target  = max(1400, int(_total_target_w * 0.50))
            _conc_target  = max(700,  int(_total_target_w * 0.20))
        print(
            f"[AR Budget] target={target_minutes}min → "
            f"intro={_intro_target}w main={_main_target}w conc={_conc_target}w"
        )
    else:
        # Legacy: ratios of English word count
        if _anim_mode:
            _intro_target = max(1400, int(en_wc * 0.44))
            _main_target  = max(2400, int(en_wc * 0.76))
            _conc_target  = max(1100, int(en_wc * 0.36))
        else:
            _intro_target = max(900,  int(en_wc * 0.30))
            _main_target  = max(1400, int(en_wc * 0.50))
            _conc_target  = max(700,  int(en_wc * 0.25))
    # Keep sec_wc for simplified/emergency prompts
    sec_wc = _intro_target

    entity_lock = (
        f"[قفل الموضوع] اكتب فقط عن: {topic_str}. "
        f"لا تذكر جرائم أو مجرمين غير ذوي صلة مباشرة بهذا الموضوع.\n\n"
    )

    is_doc = (angle or "").lower() not in ("movie", "series", "mini-series")
    # Historical/biblical topics are always pure documentary — no series framing
    if _is_historical_topic(topic_str) or _is_historical_topic(series_name):
        series_name = ""
        is_doc = True

    # ── Call 1: Hook + Background ─────────────────────────────────────────────
    # Series is supporting context only — never the primary frame of the documentary
    series_line = (
        f"العمل ذو الصلة (سياق داعم فقط — ليس محور الوثائقي): {series_name}\n"
        if series_name and not is_doc else ""
    )
    disc_line   = f"ملاحظة خاصة من البحث: {ar_disc}\n" if ar_disc else ""

    if _anim_mode:
        # Animation mode: scene-driven storytelling, 55-70% informative density
        _tts_reminder = (
            "\n[أسلوب الوثائقي المتحرك — السرد السينمائي]:\n"
            "اكتب كما لو أن المشاهد يرى مشاهد متحركة أمامه — ليس تقريراً جافاً.\n"
            "بنية كل وحدة سردية: [مشهد بصري] ← [نبضة عاطفية] ← [جو أو توتر] ← [تصعيد أو كشف] ← [انتقال]\n"
            "الكثافة المعلوماتية المستهدفة: 55-70% (ليس 90%+). "
            "ربع الكلمات يجب أن تكون جواً وإيقاعاً وتنفساً بصرياً.\n"
            "أفعال بصرية مطلوبة: المحقق يقلّب الأوراق ببطء. الكاميرا تقترب من الوجه. "
            "الغرفة تبدو فارغة لكن الساعة لا تزال تدق. "
            "هذه ليست إضافات — هي ما يجعل السرد سينمائياً.\n"
            "مساحات تنفس: كل 150-200 كلمة اترك لحظة توتر أو صمت مقصود "
            "قبل الانتقال للمعلومة التالية.\n"
            "[إيقاع TTS الرهيب]: تناوب بين الجمل القصيرة (6-10 كلمات لحظات التوتر) "
            "والمتوسطة (14-18 كلمة للسرد العادي). لا جملة تتجاوز 22 كلمة.\n"
        )
    else:
        _tts_reminder = (
            "\n[قواعد الكثافة المعلوماتية]:\n"
            "كل 100-150 كلمة يجب أن تتضمن واحداً على الأقل من: اسم شخص مع فعل محدد، "
            "تاريخ أو سنة موثقة، دليل جنائي، اعتراف أو شهادة، تطور تحقيقي.\n"
            "الجمل الجوية مقبولة بنسبة 30-45% من النص — ادمجها مع الحقائق بشكل طبيعي.\n"
            "[إيقاع TTS]: تناوب بين الجمل القصيرة (8-12 كلمة) والمتوسطة (15-20 كلمة). "
            "لا جملة تتجاوز 22 كلمة.\n"
        )

    _intro_min_str = f"ما يعادل 4-6 دقائق من السرد (~{_intro_target} كلمة)"

    # For historical/religious topics: prioritise Quran and Islamic history over Torah/Bible
    _religious_source_note = ""
    if _is_historical_topic(topic_str):
        _religious_source_note = (
            "\n[أولوية المصادر للوثائقي الإسلامي والتاريخي]:\n"
            "1. القرآن الكريم والأحاديث النبوية أولاً.\n"
            "2. التاريخ الإسلامي وعلم الآثار الإسلامي ثانياً.\n"
            "3. المصادر الأثرية المحايدة ثالثاً.\n"
            "لا تعتمد على التوراة أو الإنجيل كمصدر رئيسي — اذكرهما كمصدر مقارن فقط إذا لزم الأمر.\n"
        )

    if _anim_mode:
        # 3-phase cold open: atmosphere-only → one shocking fact → real context
        _cold_open_w = 350
        _hook_w      = 250
        _bg_w        = max(400, _intro_target - _cold_open_w - _hook_w)
        prompt_1 = (
            f"{entity_lock}"
            f"أنت راوٍ وثائقي متحرك. اكتب السرد الافتتاحي لوثائقي متحرك عن: {topic_str}\n"
            f"{series_line}"
            f"\nالحقائق الأساسية (للرجوع إليها — لا تذكرها في المشهد الافتتاحي أو الكشف):\n{facts_block}\n"
            f"{disc_line}"
            f"{_religious_source_note}"
            f"{_tts_reminder}\n"
            f"اكتب الفصول الثلاثة التالية بالعربية الفصحى — السرد المتحرك السينمائي:\n\n"
            f"[SECTION: المشهد الافتتاحي]\n"
            f"(الجو والمكان فقط — حوالي {_cold_open_w} كلمة. "
            f"ممنوع تماماً: الأسماء، التواريخ، الحقائق، الشرح، الخلفية. "
            f"فقط: المكان، الزمان، الجو، التوتر، الشعور، السؤال المعلق في الهواء. "
            f"الكاميرا تتحرك ببطء في فضاء مجهول. الصمت أثقل من الكلام. "
            f"المشاهد يشعر بثقل ما قبل أن يعرف ما هو.)\n\n"
            f"[SECTION: الكشف الصادم]\n"
            f"(حقيقة واحدة فقط — حوالي {_hook_w} كلمة. "
            f"جملة واحدة قصيرة تكشف الحدث أو الشخص، ثم صمت مقصود في السرد. "
            f"لا شرح. لا سياق. لا تتابع. المشاهد يحتاج لثانية ليستوعب ما سمعه. "
            f"انتهِ بسؤال يبقى معلقاً.)\n\n"
            f"[SECTION: الخلفية]\n"
            f"(السياق والشخصيات الحقيقية — حوالي {_bg_w} كلمة. "
            f"الآن فقط تبدأ الحقائق: أسماء حقيقية، تواريخ فعلية، بيئة ومكان. "
            f"كل شخص مهم يأخذ مشهداً مستقلاً مع وصف بصري. "
            f"صِف البيئة كما لو أن الكاميرا تتحرك في الفضاء الجغرافي.)\n\n"
            f"اكتب الفصول الثلاثة كاملة. النص السردي المنطوق فقط — بدون عناوين إضافية أو شرح."
        )
    else:
        prompt_1 = (
            f"{entity_lock}"
            f"اكتب بالعربية فصلين من وثائقي جريمة حقيقية عن: {topic_str}\n"
            f"{series_line}"
            f"\nالحقائق البحثية الحقيقية:\n{facts_block}\n"
            f"{disc_line}"
            f"{_religious_source_note}"
            f"{_tts_reminder}\n"
            f"اكتب الفصلين التاليين بالعربية الفصحى بأسلوب وثائقي سينمائي:\n\n"
            f"[SECTION: المقدمة]\n"
            f"(خطاف قوي + تأسيس التوتر — {_intro_min_str}. "
            f"الجملة الأولى يجب أن تكون صادمة وقصيرة. لا خلفية في البداية. ابدأ بالتوتر.)\n\n"
            f"[SECTION: الخلفية]\n"
            f"(السياق التاريخي والشخصيات — {_intro_min_str}. "
            f"كل شخص مهم يأخذ فقرة كاملة. أسماء حقيقية وتواريخ فعلية.)\n\n"
            f"اكتب الفصلين كاملين. لا تكتب عناوين أو ملاحظات. النص الوثائقي فقط."
        )

    # ── Call 2: Main Story + (Show vs Reality / Shocking Details) ─────────────
    _main_min_str = f"ما يعادل 8-11 دقيقة من السرد (~{_main_target} كلمة)"
    if is_doc:
        if _anim_mode:
            prompt_2 = (
                f"{entity_lock}"
                f"أنت راوٍ وثائقي متحرك. استمر في سرد الوثائقي المتحرك: {topic_str}\n"
                f"\nالحقائق الأساسية:\n{facts_block}\n"
                f"\nالحقائق الصادمة:\n{shocking_block}\n"
                f"{disc_line}"
                f"{_tts_reminder}\n"
                f"اكتب:\n\n"
                f"[SECTION: القصة الحقيقية]\n"
                f"(قلب الوثائقي المتحرك — {_main_min_str}. "
                f"روِ القصة بتسلسل مشاهد: كل مشهد له بداية بصرية، توتر، وكشف. "
                f"أعمق تفاصيل الجريمة أو الحدث — كل قرار، كل نقطة تحول، كل شخص. "
                f"اترك مساحة تنفس بصرية بين المشاهد. لا تتسرع في الانتقال.)\n\n"
                f"[SECTION: التصعيد والكشف]\n"
                f"(ما يعادل 4-6 دقائق من السرد. "
                f"الحقائق الأقل شهرة مع بناء درامي: "
                f"كل حقيقة تُقدَّم كمشهد اكتشاف — ليس كقائمة معلومات. "
                f"وتيرة أسرع هنا — الجمل تقصر، التوتر يتصاعد.)\n\n"
                f"النص السردي المنطوق فقط."
            )
        else:
            prompt_2 = (
                f"{entity_lock}"
                f"استمر في كتابة وثائقي: {topic_str}\n"
                f"\nالحقائق البحثية:\n{facts_block}\n"
                f"\nالحقائق المفاجئة:\n{shocking_block}\n"
                f"{disc_line}"
                f"{_tts_reminder}\n"
                f"اكتب:\n\n"
                f"[SECTION: القصة الحقيقية]\n"
                f"(قلب الوثائقي — {_main_min_str}. "
                f"أعمق تفاصيل الجريمة أو الحدث. كل قرار، كل عاقبة، كل شخص مهم. "
                f"تناوب بين الجمل القصيرة والمتوسطة.)\n\n"
                f"[SECTION: حقائق صادمة]\n"
                f"(ما يعادل 3-4 دقائق من السرد. الحقائق الأقل معرفة، الأشد إثارة. "
                f"الحقائق نفسها تصنع الدراما — لا تضف عبارات إثارة.)\n\n"
                f"النص الوثائقي الكامل فقط."
            )
    else:
        chars_block = "\n".join(
            f"- {c.get('character','?')}: مبني على {c.get('based_on','?')}"
            for c in (ar_research.get("show_characters") or [])[:6]
            if c.get("character")
        ) or "(لا توجد شخصيات خيالية محددة)"

        prompt_2 = (
            f"{entity_lock}"
            f"استمر في كتابة وثائقي: {topic_str}\n"
            f"العمل ذو الصلة (مرجع داعم فقط): {series_name}\n"
            f"\nالحقائق البحثية:\n{facts_block}\n"
            f"\nما غيّرته السلسلة/الفيلم مقارنة بالواقع (للإثراء فقط):\n{inaccs_block}\n"
            f"\nالشخصيات الخيالية ومقابلهم الحقيقيون:\n{chars_block}\n"
            f"{_tts_reminder}\n"
            f"اكتب:\n\n"
            f"[SECTION: القصة الحقيقية]\n"
            f"({_main_min_str}. القصة الحقيقية بعمق — الشخصية الحقيقية والأحداث والقرارات. "
            f"العمل الدرامي سياق داعم فقط — لا تجعله محور الفصل. "
            f"تناوب بين الجمل القصيرة والمتوسطة.)\n\n"
            f"[SECTION: حقائق صادمة]\n"
            f"(ما يعادل 3-4 دقائق من السرد. الحقائق الأقل شهرة والأشد إثارة. "
            f"يمكن الإشارة إلى {series_name} كمرجع بصري أو سردي، لكن الحقيقة هي المحور.)\n\n"
            f"النص الوثائقي الكامل فقط."
        )

    # ── Call 3: Conclusion ─────────────────────────────────────────────────────
    _conc_min_str = f"ما يعادل 3-5 دقائق من السرد (~{_conc_target} كلمة)"
    # Conclusion prompt intentionally omits shocking_block — it was used in Hook/MainStory.
    # Including it here triggers OpenAI content refusals on crime topics.
    if _anim_mode:
        prompt_3 = (
            f"{entity_lock}"
            f"أنت راوٍ وثائقي. أكمل الوثائقي التاريخي عن: {topic_str}\n"
            f"{_tts_reminder}\n"
            f"اكتب الخاتمة:\n\n"
            f"[SECTION: الخاتمة]\n"
            f"({_conc_min_str}. "
            f"ابدأ بمشهد بصري هادئ — الكاميرا تبتعد ببطء. "
            f"تأمّل في دلالة القصة وما تعنيه للمجتمع. "
            f"اختم بسؤال مفتوح أو حقيقة إنسانية عميقة. "
            f"آخر ثلاث جمل قصيرة ومحملة بالمعنى. "
            f"لا تلخّص ما سبق. لا تكتب: 'وهكذا...' أو 'تلك هي قصة...')\n\n"
            f"النص السردي فقط."
        )
    else:
        prompt_3 = (
            f"{entity_lock}"
            f"أكمل الوثائقي التاريخي: {topic_str}\n"
            f"{_tts_reminder}\n"
            f"اكتب الخاتمة:\n\n"
            f"[SECTION: الخاتمة]\n"
            f"({_conc_min_str}. "
            f"تأمّل في دلالة هذه القصة. اختم بسؤال مفتوح أو حقيقة إنسانية أو دلالة أعمق. "
            f"آخر جملتين تتركان أثراً عميقاً في المستمع. "
            f"لا تكتب: 'وهكذا...' أو 'تلك هي قصة...' "
            f"جمل قصيرة ومحكومة — لا ختامات مطوّلة.)\n\n"
            f"النص الوثائقي فقط."
        )

    # ── Section minimum word counts ──────────────────────────────────────────
    # Animation mode uses aggressive minimums matching cinematic runtime targets.
    # Standard mode keeps moderate minimums to handle Groq rate-limit fallback paths.
    # Minimum word counts for ACCEPTANCE of each section call.
    # Main story minimum is the first-chunk floor — chunking extends it after.
    # Acceptance floor is intentionally low — any real content (100w) passes,
    # then _write_ar_section_chunked extends ALL sections to their full targets.
    # High minimums caused every section to fail all 3 retries; chunking never ran.
    _SECTION_MIN_WC: dict[str, int] = {
        "AR-Hook+Background": 100,
        "AR-MainStory+Ch4":   100,
        "AR-Conclusion":      100,
    }
    # Core sections: missing any of these = pipeline must not continue
    _CORE_SECTIONS: frozenset = frozenset({"AR-MainStory+Ch4"})

    # ── Simplified prompt (retry attempt 2) ──────────────────────────────────
    def _simplified_section_prompt(label: str) -> str:
        """Stripped-down prompt for retry 2: fewer style rules, same content."""
        if "MainStory" in label:
            if is_doc:
                return (
                    f"{entity_lock}"
                    f"اكتب بالعربية الفصحى عن: {topic_str}\n\n"
                    f"الحقائق:\n{facts_block}\n\n"
                    f"الحقائق المفاجئة:\n{shocking_block}\n\n"
                    f"[SECTION: القصة الحقيقية]\n"
                    f"اكتب حوالي {sec_wc * 2} كلمة. روِ القصة الحقيقية بتفاصيل دقيقة — "
                    f"الأسماء والتواريخ والقرارات والعواقب.\n\n"
                    f"[SECTION: حقائق صادمة]\n"
                    f"اكتب حوالي {sec_wc} كلمة. الحقائق الأقل شهرة والأشد إثارة.\n\n"
                    f"النص الوثائقي فقط."
                )
            else:
                return (
                    f"{entity_lock}"
                    f"اكتب بالعربية الفصحى عن: {topic_str}\n"
                    f"العمل ذو الصلة (سياق داعم): {series_name}\n\n"
                    f"الحقائق:\n{facts_block}\n\n"
                    f"[SECTION: القصة الحقيقية]\n"
                    f"اكتب حوالي {sec_wc * 2} كلمة. القصة الحقيقية بعمق — الشخصية والأحداث الحقيقية.\n\n"
                    f"[SECTION: حقائق صادمة]\n"
                    f"اكتب حوالي {sec_wc} كلمة. الحقائق الأقل شهرة والأشد إثارة.\n\n"
                    f"النص الوثائقي فقط."
                )
        # For other sections, just return original prompt
        return ""

    # ── Emergency prompt (retry attempt 3) ───────────────────────────────────
    def _emergency_section_prompt(label: str) -> str:
        """Bare-minimum prompt — no style rules, facts only. Last resort."""
        if "MainStory" in label:
            target_wc = sec_wc * 2
            section_a = "القصة الحقيقية"
            section_b = "حقائق صادمة"
            detail_b  = "اكتب حقائق مفاجئة وأقل شهرة عن الموضوع."
            return (
                f"اكتب نصاً عربياً وثائقياً بسيطاً عن: {topic_str}\n\n"
                f"استخدم هذه الحقائق:\n{facts_block}\n\n"
                f"[SECTION: {section_a}]\n"
                f"اكتب حوالي {target_wc} كلمة. نص عربي مباشر يروي القصة بتفاصيل واضحة.\n\n"
                f"[SECTION: {section_b}]\n"
                f"اكتب حوالي {sec_wc} كلمة. {detail_b}\n\n"
                f"اكتب النص فقط. لا شرح."
            )
        return ""

    # ── Execute calls with retry chain ────────────────────────────────────────
    # Main story uses a capped first-chunk budget — _write_ar_section_chunked
    # extends it afterward in 900-1200w continuation passes.
    if _anim_mode:
        calls = [
            ("AR-Hook+Background",    prompt_1, 4500),   # one call → ~1400-2000w
            ("AR-MainStory+Ch4",      prompt_2, 3000),   # first chunk → extended by chunker
            ("AR-Conclusion",         prompt_3, 3500),   # one call → ~900-1500w
        ]
    else:
        calls = [
            ("AR-Hook+Background",    prompt_1, 3500),
            ("AR-MainStory+Ch4",      prompt_2, 2800),
            ("AR-Conclusion",         prompt_3, 2800),
        ]
    parts: list[str] = []
    _missing_core       = False
    _any_openai_refused = False   # track if OpenAI content-refused any section this run
    import time as _t

    for label, ptext, max_tok in calls:
        min_wc = _SECTION_MIN_WC.get(label, 150)
        section = ""
        _openai_refused_this_section = False   # reset per-section

        for attempt in range(1, 4):
            # Smart cooldown wait: if ALL effective providers are blocked, sleep
            # until the soonest one recovers instead of burning retries.
            # When OpenAI was content-refused this section it won't be called
            # again — treat it as blocked so Groq+Gemini wait fires correctly.
            _now_a    = _t.time()
            _g_wait   = max(0.0, _GROQ_RATE_LIMITED_UNTIL    - _now_a)
            _o_wait   = max(0.0, _OPENAI_QUOTA_EXCEEDED_UNTIL - _now_a)
            _gem_wait = max(0.0, _GEMINI_QUOTA_EXCEEDED_UNTIL - _now_a)
            _oai_effectively_blocked = _o_wait > 0 or _openai_refused_this_section
            if _g_wait > 0 and _oai_effectively_blocked and _gem_wait > 0:
                _soonest = min(_GROQ_RATE_LIMITED_UNTIL, _GEMINI_QUOTA_EXCEEDED_UNTIL)
                _sleep_s = min(max(0.0, _soonest - _t.time()), 120.0)
                if _sleep_s > 1:
                    _oai_label = "refused" if _openai_refused_this_section else f"{int(_o_wait)}s"
                    print(
                        f"[AR WAIT] All effective providers cooling — waiting {int(_sleep_s)}s "
                        f"(Groq:{int(_g_wait)}s Gemini:{int(_gem_wait)}s OpenAI:{_oai_label})"
                    )
                    _t.sleep(_sleep_s)
            elif attempt > 1:
                _t.sleep(3)

            if attempt == 1:
                prompt_used = ptext
                temp        = 0.65
            elif attempt == 2:
                print(f"[AR RETRY] Attempt 2 for {label} — simplified prompt")
                fallback_p = _simplified_section_prompt(label)
                prompt_used = fallback_p if fallback_p else ptext
                temp        = 0.70
            else:
                print(f"[AR RETRY] Attempt 3 for {label} — emergency reconstruction")
                print("[AR FALLBACK] Research reconstruction active")
                emergency_p = _emergency_section_prompt(label)
                prompt_used = emergency_p if emergency_p else ptext
                temp        = 0.75

            # After an OpenAI content refusal, bypass OpenAI entirely on retry
            # so Groq / Gemini (more permissive) get a chance.
            _call_premium = not _openai_refused_this_section
            if _openai_refused_this_section:
                print(f"[AR RETRY] {label} attempt {attempt}: OpenAI previously refused — routing to Groq/Gemini")

            print(f"[AR Script] Writing {label} (attempt {attempt}/3)...")
            raw = _ai_script_call(
                prompt_used,
                max_tokens=max_tok,
                temperature=temp,
                system_prompt=_AR_SCRIPT_SYSTEM_PROMPT if attempt < 3 else None,
                premium=_call_premium,
            )
            raw_wc      = clean_word_count(raw) if raw else 0
            _raw_chars  = len(raw) if raw else 0
            _raw_lines  = (raw or "").count("\n")

            # Secondary refusal check for anything that slipped through _openai_call
            # (e.g. a refusal > 300 chars, or from Groq/Gemini using similar phrasing).
            _is_refusal = raw and raw_wc < 60 and any(sig in raw for sig in _AR_REFUSAL_SIGNALS)
            if _is_refusal:
                print(
                    f"[AR REFUSAL] {label} attempt {attempt}: content refusal detected — "
                    f"switching to Groq/Gemini for next attempt"
                )
                _openai_refused_this_section = True
                _any_openai_refused          = True
                raw    = None
                raw_wc = 0

            print(
                f"[AR RAW] {label} attempt {attempt}: "
                f"raw_len={_raw_chars}chars raw_wc={raw_wc}w raw_lines={_raw_lines} "
                f"(min={min_wc}w max_tok={max_tok})"
            )
            if raw and raw_wc < min_wc:
                _stripped = raw.strip()
                print(
                    f"[AR PARSER DAMAGE CHECK] {label}: "
                    f"stripped_len={len(_stripped)}chars "
                    f"starts_with={repr(_stripped[:60])} "
                    f"ends_with={repr(_stripped[-60:])}"
                )

            if raw and raw_wc >= min_wc:
                section = raw.strip()
                print(f"[AR Script] {label}: {raw_wc}w ✅ (attempt {attempt})")
                break
            else:
                print(
                    f"[AR VALIDATION] {label} failed minimum threshold: "
                    f"{raw_wc}w < {min_wc}w"
                )
                if attempt < 3:
                    print(f"[AR RETRY] Attempt {attempt + 1} scheduled")

        if section:
            # All sections: extend with continuation chunks until per-section target reached.
            # Low minimums above ensure any real output enters chunking here.
            _sec_target = {
                "AR-Hook+Background": _intro_target,
                "AR-MainStory+Ch4":   _main_target,
                "AR-Conclusion":      _conc_target,
            }.get(label, _main_target)
            if clean_word_count(section) < _sec_target:
                _sec_pre = clean_word_count(section)
                print(f"[AR Chunk] {label} {_sec_pre}w < target {_sec_target}w — extending")
                section = _write_ar_section_chunked(
                    label         = label,
                    topic_str     = topic_str,
                    entity_lock   = entity_lock,
                    target_words  = _sec_target,
                    existing_text = section,
                    tts_reminder  = _tts_reminder,
                    is_anim       = _anim_mode,
                )
            parts.append(section)
        else:
            print(
                f"[AR VALIDATION] Missing mandatory section: {label} — "
                f"all 3 attempts failed"
            )
            if label in _CORE_SECTIONS:
                _missing_core = True
                print(f"[AR PIPELINE] Core section missing — {label}")

    # ── Section completeness check ────────────────────────────────────────────
    if _missing_core:
        print("[AR PIPELINE] Regeneration triggered — emergency reconstruction of main story")
        # If OpenAI content-refused this topic, go straight to Groq/Gemini
        _em_premium = not _any_openai_refused
        if _any_openai_refused:
            print("[AR PIPELINE] OpenAI refused content — using Groq/Gemini for emergency reconstruction")
        emergency_final = _ai_script_call(
            _emergency_section_prompt("AR-MainStory+Ch4"),
            max_tokens=2600,
            temperature=0.75,
            premium=_em_premium,
        )
        # If Groq/Gemini also failed, try the other tier once
        if (not emergency_final or clean_word_count(emergency_final) < 200) and _any_openai_refused:
            emergency_final = _ai_script_call(
                _emergency_section_prompt("AR-MainStory+Ch4"),
                max_tokens=2600,
                temperature=0.75,
                premium=True,
            )
        if emergency_final and clean_word_count(emergency_final) >= 200:
            # Extend emergency section to main target via chunking
            _em_wc = clean_word_count(emergency_final)
            if _em_wc < _main_target:
                print(f"[AR PIPELINE] Extending emergency main story: {_em_wc}w → target {_main_target}w")
                emergency_final = _write_ar_section_chunked(
                    label         = "AR-MainStory+Ch4",
                    topic_str     = topic_str,
                    entity_lock   = entity_lock,
                    target_words  = _main_target,
                    existing_text = emergency_final.strip(),
                    is_anim       = _anim_mode,
                )
            # Insert before conclusion (last part) to maintain story order
            if len(parts) > 1:
                parts.insert(-1, emergency_final.strip())
            else:
                parts.append(emergency_final.strip())
            print(
                f"[AR FALLBACK] Emergency reconstruction added: "
                f"{clean_word_count(emergency_final)}w"
            )
        else:
            # All LLMs failed — last-resort recovery chain
            print("[AR EMERGENCY] All LLM providers failed — attempting recovery")
            _en_body    = en_script.get("script", "").strip()
            _gt_success = False

            # Recovery path A: Google-Translate the English script (when available)
            if _en_body:
                try:
                    _en_words   = _en_body.split()
                    _gt_start   = len(_en_words) // 5
                    _gt_end     = 4 * len(_en_words) // 5
                    _en_excerpt = " ".join(_en_words[_gt_start:_gt_end])[:3500]
                    _gt_result  = translate_to_arabic_google(_en_excerpt)
                    if _gt_result and clean_word_count(_gt_result) >= 100:
                        _gt_section = f"[SECTION: القصة الحقيقية]\n{_gt_result}"
                        if len(parts) > 1:
                            parts.insert(-1, _gt_section)
                        else:
                            parts.append(_gt_section)
                        print(f"[AR EMERGENCY] Google Translate fallback: {clean_word_count(_gt_result)}w added ✅")
                        _gt_success = True
                    else:
                        print("[AR EMERGENCY] Google Translate returned empty")
                except Exception as _gt_err:
                    print(f"[AR EMERGENCY] Google Translate failed: {_gt_err}")

            # Recovery path B: Use existing Arabic research facts directly (no LLM, no English needed)
            if not _gt_success:
                _ar_facts   = ar_research.get("ar_research_facts",    [])
                _ar_shock   = ar_research.get("ar_research_shocking", [])
                _ar_inaccs  = ar_research.get("ar_research_inaccuracies", [])
                _all_ar_facts = list(dict.fromkeys(_ar_facts + _ar_shock + _ar_inaccs))
                if _all_ar_facts:
                    _facts_text = "\n\n".join(_all_ar_facts[:10])
                    _raw_section = (
                        f"[SECTION: القصة الحقيقية]\n"
                        f"الحقائق الموثقة حول {topic_str}:\n\n{_facts_text}"
                    )
                    if len(parts) > 1:
                        parts.insert(-1, _raw_section)
                    else:
                        parts.append(_raw_section)
                    print(f"[AR EMERGENCY] Arabic research facts used directly: {clean_word_count(_raw_section)}w added ✅")
                    _gt_success = True

            # Recovery path C: Translate just the topic description via Google
            if not _gt_success:
                try:
                    _topic_en   = f"The true story of {topic_str}. A documentary about real events and real people."
                    _gt_result2 = translate_to_arabic_google(_topic_en)
                    if _gt_result2 and clean_word_count(_gt_result2) >= 5:
                        _raw_section2 = f"[SECTION: القصة الحقيقية]\n{_gt_result2}"
                        if len(parts) > 1:
                            parts.insert(-1, _raw_section2)
                        else:
                            parts.append(_raw_section2)
                        print(f"[AR EMERGENCY] Topic-only translation: {clean_word_count(_gt_result2)}w added ✅")
                        _gt_success = True
                except Exception as _gt_err2:
                    print(f"[AR EMERGENCY] Topic translation failed: {_gt_err2}")

            if not _gt_success:
                print("[AR EMERGENCY] All recovery paths exhausted — returning empty to caller")
                return ""

    if not parts:
        return ""

    # Guarantee each chunk has a TTS-visible section marker.
    # Without explicit markers the TTS splitter only finds the markers
    # embedded inside AR-MainStory+Ch4 (القصة الحقيقية / الرواية مقابل الواقع),
    # leaving Hook+Background and Conclusion orphaned and unspoken.
    import re as _re_label
    _canonical_markers = {
        "AR-Hook+Background": "المقدمة",
        "AR-Conclusion":      "الخاتمة",
    }
    _labeled_parts: list[str] = []
    for _chunk_label, _chunk_text in zip(
        ["AR-Hook+Background", "AR-MainStory+Ch4", "AR-Conclusion"], parts
    ):
        if not _chunk_text:
            continue
        _has_any_marker = bool(_re_label.search(r'\[SECTION:', _chunk_text))
        _default_marker = _canonical_markers.get(_chunk_label)
        if _default_marker and not _has_any_marker:
            _labeled_parts.append(f"[SECTION: {_default_marker}]\n{_chunk_text.strip()}")
        elif _chunk_label == "AR-MainStory+Ch4" and not _has_any_marker:
            _labeled_parts.append(f"[SECTION: القصة الرئيسية]\n{_chunk_text.strip()}")
        else:
            _labeled_parts.append(_chunk_text)

    full_ar = "\n\n".join(_labeled_parts)

    # Runtime floor: Arabic minimum 20 min = 5000 words at 250 wpm.
    # If generated script is short, append one continuation chunk.
    _AR_MIN_WORDS = 5000
    _final_wc = clean_word_count(full_ar)
    if _final_wc < _AR_MIN_WORDS:
        _deficit_w = _AR_MIN_WORDS - _final_wc
        _deficit_min = round(_deficit_w / 190, 1)
        print(f"[AR Runtime] {_final_wc}w < {_AR_MIN_WORDS}w ({_AR_MIN_WORDS // 190}min) minimum — "
              f"appending {_deficit_w}w ({_deficit_min}min) extension")
        import time as _trt
        _trt.sleep(2)
        _tail = " ".join(full_ar.split()[-150:])
        _ext = _ai_script_call(
            f"{entity_lock}"
            f"استمر في السرد الوثائقي عن: {topic_str}\n"
            f"{_tts_reminder}\n"
            f"أضف ما يعادل {_deficit_min} دقيقة (~{_deficit_w} كلمة) من السرد التفصيلي. "
            f"لا تكرر ما سبق. استمر مباشرة من حيث توقفت:\n\n{_tail}\n\n"
            f"اكتب الاستمرار فقط. لا عناوين.",
            max_tokens=min(4000, max(1500, _deficit_w * 2)),
            temperature=0.68,
            premium=True,
        )
        if _ext and clean_word_count(_ext) >= 100:
            full_ar = full_ar.rstrip() + "\n\n" + _ext.strip()
            print(f"[AR Runtime] Extended: {_final_wc}w → {clean_word_count(full_ar)}w")
        else:
            print(f"[AR Runtime] Extension failed — proceeding with {_final_wc}w")

    # Post-process: remove atmospheric filler and log density
    try:
        from agents.script_quality import (
            remove_arabic_filler_phrases,
            validate_information_density,
            apply_all_quality_filters,
        )
        full_ar = remove_arabic_filler_phrases(full_ar)
        full_ar = apply_all_quality_filters(full_ar, language="arabic")
        _ar_density = validate_information_density(full_ar, language="arabic")
        print(
            f"[Density-AR] Script density: {_ar_density.get('density_pct',0):.0f}% [{_ar_density.get('verdict','?')}] "
            f"| filler removed={_ar_density.get('filler_count',0)} "
            f"| fragments={_ar_density.get('fragment_count',0)}"
        )
    except Exception as _dae:
        print(f"[Density-AR] Post-process (non-fatal): {_dae}")

    return full_ar


def tts_readability_pass(arabic_text: str) -> str:
    """
    Lightweight regex post-processor for Arabic TTS readability.
    Splits connector-heavy sentences to improve spoken cadence.
    Does NOT touch facts, names, or dates — pure structural cleanup.
    """
    import re as _re
    if not arabic_text or len(arabic_text.split()) < 100:
        return arabic_text

    t = arabic_text

    # 1. حيث / إذ mid-sentence → sentence break, preserve connector at new sentence start
    t = _re.sub(r'،\s*(حيث|إذ)\s+', r'.\n\1 ', t)

    # 2. بينما / وقد mid-sentence → sentence break
    t = _re.sub(r'،\s*(بينما|وقد)\s+', r'.\n\1 ', t)

    # 3. Triple-و chain: phrase، وphrase، وphrase → split at second join
    t = _re.sub(
        r'([^.،؟!\n]{15,50})،\s*و([^.،؟!\n]{10,35})،\s*و',
        r'\1.\n\2.\n',
        t,
    )

    # 4. Add dramatic pause (blank line) before key pivot phrases
    t = _re.sub(r'\.\s+(بدلاً من|في الواقع|في الحقيقة|والحقيقة أن|غير أن)\s+', r'.\n\n\1 ', t)

    # 5. Normalise: no triple newlines
    t = _re.sub(r'\n{3,}', '\n\n', t)

    # 6. Sentences ending mid-line before \n get a closing period
    t = _re.sub(r'([^\.\n!؟،])\n', r'\1.\n', t)

    # 7. Remove double periods
    t = _re.sub(r'\.{2,}', '.', t)

    return t.strip()


def translate_script(en_script: dict, research: dict | None = None) -> dict:
    """
    Generate the Arabic script for a given English script.

    When `research` is provided (recommended):
      1. Translate research facts to Arabic
      2. Write Arabic script directly from Arabic research
      → Native Arabic pacing, cadence, suspense — not a compressed translation.

    When `research` is None (legacy fallback):
      Translate the finished English script section by section.
    """
    _topic_lower = safe_lower(en_script.get("topic"))
    _is_hemedti  = any(k in _topic_lower for k in ["hemedti", "حميدتي", "dagalo"])

    if _is_hemedti:
        ar_title = _build_hemedti_arabic_title(en_script.get("part_number"))
    else:
        ar_title = _generate_arabic_title_llm(
            topic_str=en_script.get("topic", ""),
            angle_title=en_script.get("angle_title", ""),
            series_name=en_script.get("series_name"),
            series_type=en_script.get("series_type"),
        )

    # Arabic runtime target — independent of English length.
    # Set via AR_TARGET_MINUTES env var or derived from pipeline mode.
    _ar_target_minutes = float(os.getenv("AR_TARGET_MINUTES", "0") or "0")
    if _ar_target_minutes <= 0:
        _ar_target_minutes = {
            "fast": 30.0, "animation": 35.0, "full": 45.0,
        }.get(os.getenv("PIPELINE_MODE", "fast").lower(), 30.0)
    print(f"[AR] Runtime target: {_ar_target_minutes}min")

    # ── Choose script generation path ─────────────────────────────────────────
    # Path A (preferred): write Arabic directly from Arabic research
    # Path B (fallback):  translate the finished English script
    _ar_script_body = ""
    _used_research_path = False

    if research:
        print("[AR] Writing Arabic script from research (native path)...")
        try:
            ar_research = translate_research_to_arabic(research)
            # Pass series_type from en_script so the prompt knows the angle
            _ar_en_script_augmented = dict(en_script)
            _ar_en_script_augmented.setdefault("series_type", research.get("series_type", ""))
            _ar_script_body = _write_arabic_from_research(
                _ar_en_script_augmented, ar_research,
                target_minutes=_ar_target_minutes,
            )
            if _ar_script_body and clean_word_count(_ar_script_body) >= 300:
                # Validate core section is present before accepting
                _core_present = any(
                    m in _ar_script_body
                    for m in ["القصة الحقيقية", "الرواية مقابل الواقع", "حقائق صادمة"]
                )
                if not _core_present:
                    print("[AR VALIDATION] Missing mandatory section: القصة الحقيقية")
                    print("[AR PIPELINE] Core story section absent — forcing translation fallback")
                    _ar_script_body = ""
                else:
                    _ar_script_body = fix_first_mention(_ar_script_body, is_arabic=True)
                    _used_research_path = True
                    print(f"[AR] Research path: {clean_word_count(_ar_script_body)}w written ✅")
            else:
                print("[AR] Research path produced empty/short script — falling back to translation")
                _ar_script_body = ""
        except Exception as _ar_e:
            print(f"[AR] Research path failed ({_ar_e}) — falling back to translation")
            _ar_script_body = ""

    if not _ar_script_body:
        print("[AR] Translation path (legacy)...")
        _ar_script_body = fix_first_mention(
            _translate_script_preserve_sections(en_script["script"]), is_arabic=True
        )

    ar_data = {
        "title":           ar_title,
        "hook":            translate_to_arabic(en_script.get("hook", "")),
        "script":          _ar_script_body,
        "on_screen_texts": [translate_to_arabic(t) for t in en_script["on_screen_texts"]],
        "caption":         translate_to_arabic(en_script["caption"]),
        "hashtags":        translate_to_arabic(en_script["hashtags"]),
        "thumbnail_text":  translate_to_arabic(en_script["thumbnail_text"]),
        "chapters":        (
            generate_chapters_from_script(_ar_script_body, en_script.get("topic", ""), "arabic")
            if _ar_script_body else en_script.get("chapters", "")
        ),
    }
    _topic_name = en_script.get("topic", "")
    ar_data["topic"]           = _topic_name
    ar_data["niche"]           = en_script.get("niche", _topic_name)
    ar_data["search_query"]    = en_script.get("search_query", _topic_name)
    ar_data["keywords"]        = en_script.get("keywords", [_topic_name] if _topic_name else [])
    ar_data["language"]        = "arabic"
    ar_data["manual_topic"]    = bool(en_script.get("manual_topic"))
    ar_data["series_name"]     = en_script.get("series_name", "")
    ar_data["series_type"]     = en_script.get("series_type", "")
    ar_data["arabic_path"]     = "research" if _used_research_path else "translation"

    _ar_wc = clean_word_count(ar_data.get("script", ""))
    print(f"[Script] Arabic word count ({ar_data['arabic_path']} path): {_ar_wc}")

    # Arabic floor by mode — animation and full need higher minimums
    _pipeline_mode_tr = os.getenv("PIPELINE_MODE", "fast").lower()
    if _pipeline_mode_tr not in _WORD_FLOORS:
        _pipeline_mode_tr = "fast"
    _AR_WORD_FLOOR  = _WORD_FLOORS[_pipeline_mode_tr]["arabic"]
    _AR_WORD_TARGET = int(_AR_WORD_FLOOR * 1.1)               # 10% headroom above floor
    if _ar_wc < _AR_WORD_TARGET:
        print(f"[AR RUNTIME] Script words: {_ar_wc} — below target {_AR_WORD_TARGET:,}, expanding...")
        ar_data["script"] = _expand_arabic_script_to_min(ar_data["script"], target_min=_AR_WORD_TARGET)

    # ── Runtime target check ──────────────────────────────────────────────────
    _en_wc     = clean_word_count(en_script.get("script", ""))
    _en_min    = _en_wc / 160.0
    _ar_min    = estimate_arabic_duration(ar_data.get("script", ""))
    _ar_wc_now = clean_word_count(ar_data.get("script", ""))
    _ar_contract_ref = get_runtime_contract(_pipeline_mode_tr, "arabic")
    # Arabic runtime target is set independently of English — Arabic is primary.
    _ar_target = max(_ar_contract_ref["min_minutes"], _ar_target_minutes)
    print(
        f"[AR RUNTIME] Script words: {_ar_wc_now}\n"
        f"[AR RUNTIME] OpenAI TTS estimated duration: ~{_ar_min:.1f}min "
        f"(EN: ~{_en_min:.1f}min | AR target: {_ar_target:.1f}min)"
    )
    if _ar_min < _ar_target:
        print(f"[AR EXPANSION] Runtime {_ar_min:.1f}min below target {_ar_target:.1f}min — regenerating narration")
        _topic = en_script.get("topic", "")
        ar_data["script"] = expand_arabic_runtime(
            ar_data["script"], target_min=_ar_target, topic=_topic
        )
    _stage_wc_expand  = clean_word_count(ar_data.get("script", ""))
    _stage_min_expand = _stage_wc_expand / _TTS_WPM["arabic"]
    print(f"[AR STAGE] name=expand_runtime | words={_stage_wc_expand} | runtime={_stage_min_expand:.1f}min")

    if ar_data.get("script"):
        # ── FINAL LOCK: baseline snapshot after all expansion ─────────────────
        # Nothing after this point may reduce word count below 90% of this value.
        _locked_script = ar_data["script"]
        _locked_wc     = clean_word_count(_locked_script)
        _locked_min    = _locked_wc / _TTS_WPM["arabic"]
        print(f"[AR STAGE] name=final_lock | words={_locked_wc} | runtime={_locked_min:.1f}min")

        # ── Upgrade (guarded — rejects if shorter than 90% of locked baseline) ─
        ar_data["script"]  = upgrade_arabic_script(ar_data["script"])
        _post_upgrade_wc   = clean_word_count(ar_data.get("script", ""))
        _post_upgrade_min  = _post_upgrade_wc / _TTS_WPM["arabic"]
        _regress_warn      = " WARNING=RUNTIME_REGRESSION" if _post_upgrade_wc < _locked_wc * 0.90 else ""
        print(f"[AR STAGE] name=upgrade_script | words={_post_upgrade_wc} | runtime={_post_upgrade_min:.1f}min{_regress_warn}")

        # ── TTS-only cleanup (punctuation / normalization — no content rewriting) ─
        ar_data["script"] = evaluate_and_fix_script(ar_data["script"])
        from agents.script_quality import (
            normalize_arabic_documentary_text, normalize_arabic_tts, enforce_arabic_purity,
            remove_arabic_filler_phrases, validate_information_density,
        )
        ar_data["script"] = normalize_arabic_documentary_text(ar_data["script"])
        ar_data["script"] = normalize_arabic_tts(ar_data["script"])
        ar_data["script"] = tts_readability_pass(ar_data["script"])
        ar_data["script"] = enforce_arabic_purity(ar_data["script"])
        ar_data["script"] = remove_arabic_filler_phrases(ar_data["script"])
        ar_data["script"] = apply_mishkal_tashkeel(ar_data["script"])

        # ── Section marker guard — ensure all 3 structural sections survive cleanup ─
        # Runs AFTER all cleanup so guards cannot be stripped again.
        # Without markers, TTS generates the whole script as one section → truncation risk.
        import re as _re_mg
        _ar_script = ar_data.get("script", "")
        if _ar_script:
            # Guard 1: intro marker
            if not _re_mg.search(r'\[SECTION:\s*المقدمة', _ar_script):
                ar_data["script"] = f"[SECTION: المقدمة]\n{_ar_script.lstrip()}"
                _ar_script = ar_data["script"]
                print("[AR Script] Marker guard: prepended [SECTION: المقدمة] — marker was absent after processing")

            # Guard 2: main story — any of the accepted section names counts
            _main_story_patterns = [
                r'\[SECTION:\s*القصة\s*الحقيقية',
                r'\[SECTION:\s*القصة\s*الحقيقية',
                r'\[SECTION:\s*التصعيد',
                r'\[SECTION:\s*الرواية\s*مقابل',
                r'\[SECTION:\s*حقائق\s*صادمة',
                r'\[SECTION:\s*الخلفية',
            ]
            if not any(_re_mg.search(p, _ar_script) for p in _main_story_patterns):
                # Split at conclusion marker (if present) and insert main story before it
                _conc_match = _re_mg.search(r'\[SECTION:\s*الخاتمة', _ar_script)
                if _conc_match:
                    _before = _ar_script[:_conc_match.start()].rstrip()
                    _after  = _ar_script[_conc_match.start():]
                    ar_data["script"] = _before + "\n\n[SECTION: القصة الحقيقية]\n\n" + _after
                    _ar_script = ar_data["script"]
                    print("[AR Script] Marker guard: inserted [SECTION: القصة الحقيقية] before conclusion")

            # Guard 3: conclusion marker
            if not _re_mg.search(r'\[SECTION:\s*الخاتمة', _ar_script):
                # Append a conclusion marker before the last 15% of the script
                _lines = _ar_script.split("\n")
                _split = max(int(len(_lines) * 0.85), len(_lines) - 30)
                ar_data["script"] = (
                    "\n".join(_lines[:_split]).rstrip()
                    + "\n\n[SECTION: الخاتمة]\n"
                    + "\n".join(_lines[_split:]).lstrip()
                )
                print("[AR Script] Marker guard: inserted [SECTION: الخاتمة] — conclusion marker was absent")

        # ── RUNTIME PRESERVATION GUARD — restore locked version if cleanup regressed ─
        _post_cleanup_wc  = clean_word_count(ar_data.get("script", ""))
        _post_cleanup_min = _post_cleanup_wc / _TTS_WPM["arabic"]
        _contract_floor   = _ar_contract_ref["min_minutes"]
        if _post_cleanup_wc < _locked_wc * 0.90:
            print(
                f"[RUNTIME GUARD] Post-cleanup regression: {_post_cleanup_wc}w < {_locked_wc}w×90% "
                f"— restoring locked pre-upgrade version ({_locked_wc}w | {_locked_min:.1f}min)"
            )
            ar_data["script"] = _locked_script
        elif _post_cleanup_min < _contract_floor:
            print(
                f"[RUNTIME GUARD] Post-cleanup below contract: {_post_cleanup_min:.1f}min < "
                f"{_contract_floor:.0f}min — restoring locked version ({_locked_min:.1f}min)"
            )
            ar_data["script"] = _locked_script

        _ar_final_density = validate_information_density(ar_data["script"], language="arabic")
        print(
            f"[AR Narration] Cinematic narrative pacing validated | "
            f"density: {_ar_final_density.get('density_pct',0):.0f}% [{_ar_final_density.get('verdict','?')}] | "
            f"filler_removed={_ar_final_density.get('filler_count',0)}"
        )

    _final_wc  = clean_word_count(ar_data.get("script", ""))
    _final_min = estimate_arabic_duration(ar_data.get("script", ""))

    # ── Runtime estimate log — no blocking; pipeline always renders if script is non-empty ──
    ar_data.pop("script_too_short", None)   # never set script_too_short based on word count
    if _final_wc < 50:
        ar_data["script_too_short"] = True  # only flag truly empty scripts
        print(f"[AR EMPTY] Script has {_final_wc}w — marking as empty, will skip render")

    ar_data["estimated_runtime_min"] = round(_final_min, 1)

    print(f"[AR RUNTIME] Estimated duration: ~{_final_min:.1f}min (WPM estimate only — real validation on rendered audio)")
    print(
        f"[Script] Arabic done ({ar_data['arabic_path']} path): "
        f"'{ar_data['title']}' | {_final_wc}w | ~{_final_min:.1f}min"
    )
    return ar_data


_AR_SHORT_SCRIPT_SYSTEM = """أنت تكتب تعليقاً صوتياً مدته 60 ثانية لفيديو جريمة حقيقية قصير (يوتيوب شورتس / تيك توك / إنستغرام ريلز).

الهيكل (ضمني — لا عناوين ولا تسميات في المخرجات):
- الثواني 0-3 (الخطّاف): ابدأ مباشرة بالفعل. حقيقة صادمة أو سر مفاجئ. الصدمة فوراً.
- الثواني 3-20 (البناء): من كان متورطاً. ما الذي كان على المحك. جملتان أو ثلاث، مضغوطة.
- الثواني 20-45 (الكشف): الحقيقة، الانقلاب، اللحظة التي تغير كل شيء. أكثف جزء في النص.
- آخر 5 ثوانٍ (خاتمة + CTA): اختم بسؤال مفتوح أو حقيقة مقلقة. ثم: "تابع Dark Crime Decoded لمزيد."

نبرة الصوت: هادئة. تقصيرية. مقلقة قليلاً. الراوي يعرف أكثر مما يقول.
ليس: متحمساً، أكاديمياً، رسمياً، أو مثيراً للإحساس.

قواعد الجمل:
- الحد الأقصى: 12 كلمة لكل جملة. الأقصر أقوى.
- كل جملتين يجب أن ترفعا التوتر — ممنوع البقاء على مستوى واحد.
- تنويع الإيقاع: اخلط جمل الخمس كلمات مع جمل الاثنتي عشرة.
- لا حشو وصفي. كل جملة يجب أن تُحرك القصة للأمام.

كثافة المعلومات — إلزامي:
كل جملة يجب أن تفعل إحدى هذه: تكشف معلومة جديدة، تذكر حقيقة محددة، أو تُصعّد التوتر.
جمل حشو محظورة: "ساد الصمت." / "تزايد الخوف." / "لم يصدق أحد ما حدث."
صحيح: "ظهر اسمها في ثلاث قضايا قبل أن يربطها المحققون."
الحد الأقصى جملة واحدة جوية في الفيديو كله. كل جملة أخرى = حقيقة محددة.

الطول: الهدف 230-260 كلمة. المقبول 210-280. الحد الأدنى المطلق 200.

افتتاحيات محظورة: "هذا الفيديو يشرح...", "في هذه القصة...", "في عام...", "كانت هي...", "كانت...", "هذا عن..."
تنسيق محظور: أي عناوين أو تسميات أو علامات مقاطع في المخرجات.
أسلوب محظور: ملخصات، نبرة تعليمية، عبارات جوية مكررة، تكرار "كانت" كبداية للجمل، قوائم نقطية.

تنويع الكلمة الأولى — قاعدة إلزامية:
لا تبدأ جملتان متتاليتان بنفس الكلمة الأولى.
لا تُستخدم نفس الكلمة الافتتاحية أكثر من مرتين في النص كله.
محظور تاماً: "يتم" في أي صيغة — الفاعل يجب أن يكون صريحاً في كل جملة.
محظور تاماً: "كانت" أو "كان" كبداية لأي جملة.
النمط المحظور: كانت X. كانت Y. كانت Z. كانت... (تكرار نفس البداية في كل سطر)
النمط الصحيح: بدأت X. قررت Y. اكتشف المحققون Z. رفض القاضي..."""


_SHORT_SCRIPT_SYSTEM = """You are writing a 60-second spoken voiceover for a true crime short video (YouTube Shorts / TikTok / Instagram Reels).

STRUCTURE (implicit — NO labels, NO headings in output):
- Seconds 0-3 (Hook): Drop straight into the action. Contradiction, secret, or rule-break. Shock immediately.
- Seconds 3-20 (Build): Who was involved. What was at stake. Keep tight — 2-3 sentences max.
- Seconds 20-45 (Reveal): The truth, the twist, the moment that changes everything. Most intense section.
- Last 5 seconds (Cliffhanger + CTA): End on an open question or disturbing fact. Then ONE of:
  "Full story in the main video." / "The truth is in the full video." / "Follow Dark Crime Decoded for more."

VOICE TONE: Calm. Investigative. Slightly unsettling. The narrator knows more than they are saying.
NOT: excited, academic, formal, or sensationalist.

SENTENCE RULES:
- Maximum 14 words per sentence. Shorter is stronger.
- Every 2 sentences must increase tension — never flat.
- Mix 5-word punches with 12-word builds. Vary the rhythm.
- No descriptive filler. Every sentence must move the story forward.

INFORMATION DENSITY — mandatory:
Every sentence must do ONE of: reveal new information, name a specific fact, or escalate specific tension.
BANNED FILLER SENTENCES: "The silence grew." / "The darkness deepened." / "Fear spread." / "Nobody could believe it."
GOOD: "His name appeared in three unsolved cases before investigators connected the dots."
GOOD: "She had been reported missing six hours before the body was found."
Maximum 1 atmospheric sentence in the entire short. Every other sentence = a specific fact.

LENGTH: Target 190-215 words. Acceptable range 170-230. Hard minimum 170.

BANNED OPENERS: "This video explains...", "In this story...", "In an era...", "Throughout history...", "This is the story of...", "He was...", "This is about..."
BANNED FORMAT: Any headings, labels, or section markers in the output.
BANNED STYLE: Summaries, educational tone, generic atmosphere phrases, repetitive suspense loops."""


def estimate_short_duration_secs(text: str, language: str = "english") -> float:
    """Estimate spoken duration of a short video script in seconds."""
    wpm = _TTS_WPM.get(language.lower(), 145)
    return (clean_word_count(text) / wpm) * 60.0


def expand_short_script(script_text: str, language: str, topic: str, target_words: int) -> str:
    """Expand a short video script to reach target word count while preserving structure."""
    current_wc = clean_word_count(script_text)
    need_extra = target_words - current_wc
    if need_extra <= 0:
        return script_text

    lang_note = (
        "Arabic script — expand by deepening each beat with specific sensory details. "
        "Add 2-3 extra sentences to Beat 3 (Main Reveal). Keep RTL Arabic."
        if language == "arabic" else
        "Expand Beat 3 (Main Reveal) and Beat 2 (Fast Setup) with more specific facts. "
        "Keep all sentences under 14 words."
    )

    prompt = f"""You wrote this {language} short video voiceover. It is {current_wc} words.
It needs at least {target_words} words to fill the minimum 60-second runtime.

Add approximately {need_extra} more words by:
- Deepening the Main Reveal section with 2-3 extra specific sentences
- Adding one more detail to the Fast Setup
- Do NOT add a new section or change the ending CTA
- Keep every sentence under 14 words
- {lang_note}

Topic: {topic}

CURRENT SCRIPT:
{script_text}

Return ONLY the expanded spoken script. No headings. No labels."""

    system = (
        "You are expanding a short crime documentary voiceover. "
        "Preserve tone, structure, and all existing content. Only add, never remove. "
        "Output only the final spoken words."
    )

    result = _ai_script_call(prompt, max_tokens=700, temperature=0.7, system_prompt=system, premium=True).strip()
    result_wc = clean_word_count(result)
    if result_wc >= current_wc:
        print(f"[SHORT EXPANSION] {language}: {current_wc} → {result_wc} words")
        return result
    print(f"[SHORT EXPANSION] Expansion shrunk script ({current_wc} → {result_wc}) — keeping original")
    return script_text


def _check_repeated_sentence_starters(text: str, max_allowed: int = 3) -> tuple[bool, str, int]:
    """Return (is_bad, offending_word, count) if any word opens more than max_allowed sentences."""
    import re
    from collections import Counter
    sentences = re.split(r'[.!؟\n]+', text)
    first_words = [
        s.strip().split()[0]
        for s in sentences
        if s.strip() and s.strip().split()
    ]
    if not first_words:
        return False, "", 0
    word, count = Counter(first_words).most_common(1)[0]
    if count > max_allowed:
        return True, word, count
    return False, "", 0


_AR_SHORT_OPENING_STYLES = [
    "ابدأ بحقيقة صادمة أو رقم غير متوقع.",
    "ابدأ بالفعل — ضع المستمع داخل اللحظة مباشرةً.",
    "ابدأ بسؤال يكسر التوقعات، ثم أجب عنه فوراً.",
    "ابدأ بالاسم والجريمة في جملة واحدة مضغوطة.",
    "ابدأ بالنتيجة أولاً، ثم اعد المستمع إلى البداية.",
    "ابدأ بجملة يقولها أحد الشخصيات — كلمة حقيقية موثقة.",
]


def _translate_arabic_short_script(english_text: str, topic: str, series_name: str = "") -> str:
    """Rewrite English short into natural spoken Arabic — skips Google Translate entirely.
    Uses GPT-4o with explicit يتم/passive ban. Groq fallback."""
    import random as _random
    import hashlib as _hashlib
    _series_line = f"المسلسل/الفيلم: {series_name}\n" if series_name else ""
    # Pick opening style deterministically per topic so re-runs stay consistent
    # but different topics get different openings
    _style_idx = int(_hashlib.md5(topic.encode()).hexdigest(), 16) % len(_AR_SHORT_OPENING_STYLES)
    _opening_style = _AR_SHORT_OPENING_STYLES[_style_idx]
    prompt = (
        f"المهمة: النص الإنجليزي أدناه هو نص فيديو قصير عن جريمة حقيقية. "
        f"أعد كتابته بالعربية الفصحى الحديثة كتعليق صوتي احترافي لفيديو 60-90 ثانية. "
        f"هذه إعادة كتابة إبداعية وليست ترجمة حرفية — احتفظ بالوقائع والأحداث لكن اجعل النص "
        f"يبدو كما كتبه صحفي عربي متمرس.\n\n"
        f"الموضوع: {topic}\n"
        f"{_series_line}"
        f"أسلوب الافتتاح المطلوب لهذا الفيديو تحديداً: {_opening_style}\n\n"
        f"الهيكل — اكتبه كنص متصل بلا عناوين:\n"
        f"- الخطّاف (2-3 جمل): {_opening_style} لا مقدمات.\n"
        f"- البناء (2-3 جمل): من كان متورطاً؟ ما الذي كان على المحك؟\n"
        f"- الكشف (4-5 جمل): اللحظة المحورية. الحقيقة التي غيّرت كل شيء.\n"
        f"- الخاتمة (2 جملة): اختم بـ: \"تابع Dark Crime Decoded لمزيد.\"\n\n"
        f"قواعد صارمة — الانتهاك يُبطل النص:\n"
        f"- محظور تاماً: \"يتم\" في أي صيغة (يتم القبض، يتم ربطه، يتم إدانته، إلخ)\n"
        f"- محظور تاماً: المبني للمجهول — لكل فعل فاعل صريح\n"
        f"- الصحيح: \"اعتقله المحققون\" لا \"تم اعتقاله\" / \"أصدر القاضي حكماً\" لا \"صدر بحقه حكم\"\n"
        f"- محظور تاماً: بدء الجملة بـ \"كانت\" أو \"كان\"\n"
        f"- تنويع الكلمة الأولى: لا تبدأ جملتان متتاليتان بنفس الكلمة. لا تكرر كلمة افتتاحية أكثر من مرتين.\n"
        f"- النمط المحظور: كانت X. كانت Y. كانت Z. — هذا فشل فوري.\n"
        f"- النمط الصحيح: اعتقله المحققون. قرر القاضي. كشف التحقيق. رفض الاستئناف.\n"
        f"- الجملة القصوى: 12 كلمة. الأقصر أقوى.\n"
        f"- الهدف: 230-260 كلمة. الحد الأدنى المطلق: 200 كلمة.\n\n"
        f"النص الإنجليزي المصدر:\n{english_text}\n\n"
        f"اكتب التعليق الصوتي العربي فقط. لا عناوين. لا شرح."
    )

    import time as _time

    def _fix_note(text: str) -> str:
        """Build a correction instruction when repeated starters are detected."""
        _bad, _word, _cnt = _check_repeated_sentence_starters(text)
        if not _bad:
            return ""
        return (
            f"\n\nخطأ في المحاولة السابقة: الكلمة \"{_word}\" تفتح {_cnt} جمل — الحد الأقصى 3. "
            f"أعد الكتابة بالكامل. كل جملة يجب أن تبدأ بكلمة مختلفة عن الجملة السابقة."
        )

    ar_text = ""
    best_text = ""

    for attempt in range(2):
        _p = prompt
        if attempt > 0 and ar_text:
            _wc_prev = clean_word_count(ar_text)
            _size_note = ("وسّع القسمين الثاني والثالث بحقائق محددة." if _wc_prev < 200 else "احذف الحشو.")
            _p += f"\n\nالمحاولة السابقة: {_wc_prev} كلمة — الهدف 230-260. {_size_note}"
            _p += _fix_note(ar_text)
        _result = _ai_script_call(
            _p, max_tokens=700, temperature=0.82,
            system_prompt=_AR_SHORT_SCRIPT_SYSTEM, premium=True,
        ).strip()
        _wc = clean_word_count(_result)
        _bad_r, _word_r, _cnt_r = _check_repeated_sentence_starters(_result)
        print(f"[AR Trans] GPT-4o attempt {attempt + 1}: {_wc}w | starter repeat: {'BAD ('+_word_r+' ×'+str(_cnt_r)+')' if _bad_r else 'OK'}")
        if _wc > clean_word_count(best_text) and not _bad_r:
            best_text = _result
        elif not best_text:
            best_text = _result
        ar_text = _result
        if _wc >= 200 and not _bad_r:
            break

    if clean_word_count(ar_text) < 200 or _check_repeated_sentence_starters(ar_text)[0]:
        print("[AR Trans] GPT-4o issues — Groq fallback")
        for attempt in range(2):
            try:
                _suffix = "\n\nاكتب على الأقل 200 كلمة. الهدف 230-260."
                _suffix += _fix_note(ar_text)
                _r = _groq_call(
                    messages=[{"role": "user", "content": prompt + _suffix}],
                    max_tokens=700,
                    temperature=0.85,
                )
                _result = _r.choices[0].message.content.strip()
                _wc = clean_word_count(_result)
                _bad_r, _word_r, _cnt_r = _check_repeated_sentence_starters(_result)
                print(f"[AR Trans] Groq attempt {attempt + 1}: {_wc}w | starter repeat: {'BAD ('+_word_r+' ×'+str(_cnt_r)+')' if _bad_r else 'OK'}")
                if _wc > clean_word_count(best_text) and not _bad_r:
                    best_text = _result
                ar_text = _result
                if _wc >= 200 and not _bad_r:
                    break
            except Exception as _e:
                print(f"[AR Trans] Groq error (attempt {attempt + 1}): {_e}")
                _time.sleep(8)

    if not ar_text and best_text:
        ar_text = best_text
    # Always prefer a clean (non-repeated) version even if shorter
    if _check_repeated_sentence_starters(ar_text)[0] and best_text and not _check_repeated_sentence_starters(best_text)[0]:
        ar_text = best_text

    _final_bad, _final_word, _final_cnt = _check_repeated_sentence_starters(ar_text)
    _final_wc = clean_word_count(ar_text)
    if _final_bad:
        print(f"[AR Trans] WARNING: repeated starter '{_final_word}' ×{_final_cnt} in final output")
    print(f"[AR Trans] Final: {_final_wc}w → ready")
    return ar_text


def write_short_script(en_long_script: dict) -> dict:
    """Extract the strongest moment from the long script and rewrite it as a 60-90 second viral short."""
    topic        = en_long_script.get("topic", "")
    long_script  = en_long_script.get("script", "")
    _angle_hook  = en_long_script.get("angle_hook", "")
    _angle_title = en_long_script.get("angle_title", "")
    series_name  = en_long_script.get("series_name", "")
    niche        = en_long_script.get("niche", "")
    show_chars   = en_long_script.get("show_characters", [])

    _series_line = f"Show/Series: {series_name}\n" if series_name else ""
    _niche_line  = f"Context: {niche}\n"           if niche        else ""
    _chars_line  = (
        f"Real figures in this story: {', '.join(str(c) for c in show_chars[:5])}\n"
        if show_chars else ""
    )

    _hook_instruction = (
        f"Open with EXACTLY this sentence: \"{_angle_hook}\"\n"
        if _angle_hook else
        "Open with the most shocking fact or unanswered question from the story. No setup. Drop straight in.\n"
    )

    _short_active_entity = build_active_entity(topic) if is_single_subject(topic) else {}
    _short_entity_lock   = entity_lock_instruction(_short_active_entity)

    prompt = f"""You are writing a spoken voiceover for a 60-90 second crime documentary short video.

TASK: Read the SOURCE SCRIPT below. Find the single most gripping moment — a shocking reveal, confession, twist, or hidden truth. Rewrite it as a standalone viral voiceover. Do NOT summarize the whole story. Tell one moment, fast and hard.
{_short_entity_lock}
TOPIC LOCK — this short is specifically about:
Topic: {topic}
{_series_line}{_niche_line}{_chars_line}{f"Angle: {_angle_title}" if _angle_title else ""}
STRICT RULE: Every fact you write must come directly from the SOURCE SCRIPT below. Do NOT invent events, names, dates, or details not in the script. Do NOT write generic crime content — stay on this specific story.

{_hook_instruction}
FLOW (4 beats — write them as continuous prose, NO headings or labels):
- Beat 1 — HOOK: 2-3 sentences. Jump straight into the action or fact. No "In [year]...", no "This is the story of...", no setup.
- Beat 2 — FAST SETUP: 2-3 sentences. Who was involved? What was at stake? Use specific names from the script.
- Beat 3 — MAIN REVEAL: 4-5 sentences. The truth, the twist, the thing that changes everything. Use specific facts from the script.
- Beat 4 — STRONG ENDING: 2 sentences. A line that lingers. End with: "Follow Dark Crime Decoded for more."

STYLE:
- Maximum 14 words per sentence. Shorter is stronger.
- Conversational, NOT documentary. Direct. Urgent. Like exposing a secret.
- No formal transitions. No long paragraphs. No academic tone.
- No ellipsis (...). No mid-sentence dashes. No parentheses.
- Every 2 sentences must increase tension — never flat.
- NO summaries. NO "He was..." openers. NO educational tone.

LENGTH: Target 190-215 words. Acceptable 170-230. Hard minimum 170. Count every word.

SOURCE SCRIPT (extract the best moment from inside):
{long_script[:2000]}

Write ONLY the spoken words. No headings. No labels. No explanations."""

    # ── Phase 1: OpenAI gpt-4o primary — 3 attempts, accept >= 150 ───────────
    script_text = ""
    best_text   = ""
    for attempt in range(3):
        _p = prompt
        if attempt > 0 and script_text:
            wc = clean_word_count(script_text)
            _p += (f"\n\nPREVIOUS ATTEMPT: {wc} words — target 190-215, minimum 170. "
                   f"{'Expand beats 2 and 3 with more specific facts to reach 190 words.' if wc < 170 else 'Trim to 190-215 words.'}")
        result = _ai_script_call(_p, max_tokens=600, temperature=0.85,
                                  system_prompt=_SHORT_SCRIPT_SYSTEM, premium=True).strip()
        words   = clean_word_count(result)
        seconds = round(words / 2.5)
        print(f"[Script] Short gpt-4o attempt {attempt + 1}: {words} words = ~{seconds}s")
        if words > clean_word_count(best_text):
            best_text = result
        script_text = result
        print(f"[SHORT RUNTIME] EN attempt {attempt + 1}: {words} words → ~{round(words / _TTS_WPM['english'] * 60)}s")
        if words >= 170:
            break
        print(f"[Script] Short under 170w ({words} words) — retrying with gpt-4o...")

    # ── Phase 2: Groq fallback — only if all gpt-4o attempts < 170w ──────────
    if clean_word_count(script_text) < 170:
        print("[Script] gpt-4o under 170w after 3 attempts — Groq fallback...")
        _p = prompt + "\n\nIMPORTANT: Write at least 170 words. Target 190-215. Count every word."
        result = _ai_script_call(_p, max_tokens=700, temperature=0.85,
                                  system_prompt=_SHORT_SCRIPT_SYSTEM, premium=False).strip()
        words = clean_word_count(result)
        print(f"[SHORT RUNTIME] EN Groq fallback: {words} words → ~{round(words / _TTS_WPM['english'] * 60)}s")
        if words > clean_word_count(best_text):
            best_text = result
        script_text = result if words >= 170 else (best_text or result)

    # Keep best result if current is still too short
    if clean_word_count(script_text) < 100 and best_text:
        script_text = best_text
        print(f"[Script] Using best result: {clean_word_count(script_text)} words")

    # ── Entity contamination guard ────────────────────────────────────────────
    if _short_active_entity:
        _seg_passed, _seg_offending = validate_entity_consistency(script_text, _short_active_entity)
        if not _seg_passed:
            print(f"[EntityGuard] Short script contaminated — sanitising")
            script_text = sanitize_script(script_text, _short_active_entity)

    # ── Topic lock validation: must mention show name + real person ───────────
    _tl_series = series_name or niche
    _tl_topic  = topic
    _tl_words_series = [w for w in _tl_series.lower().split() if len(w) > 3]
    _tl_words_topic  = [w for w in _tl_topic.lower().split()  if len(w) > 3]
    _tl_text = script_text.lower()
    _tl_series_ok = any(w in _tl_text for w in _tl_words_series) if _tl_words_series else True
    _tl_topic_ok  = any(w in _tl_text for w in _tl_words_topic)  if _tl_words_topic  else True
    if not (_tl_series_ok and _tl_topic_ok):
        print(f"[Script] Short failed topic lock (series={_tl_series_ok}, topic={_tl_topic_ok}) — regenerating")
        _lock_p = (
            prompt
            + f"\n\nCRITICAL: You MUST mention '{series_name or topic}' (the show) "
            + f"and '{topic}' (the real person) and the show-vs-reality angle. "
            + "Stay strictly on topic. No generic crime content."
        )
        _lock_r = _ai_script_call(_lock_p, max_tokens=600, temperature=0.85,
                                   system_prompt=_SHORT_SCRIPT_SYSTEM, premium=True).strip()
        if _lock_r and clean_word_count(_lock_r) >= 170:
            script_text = _lock_r
            print(f"[Script] Topic-locked short: {clean_word_count(script_text)} words")

    # ── Hook scoring: improve if score < 8 ──────────────────────────────────
    _hook_score = _score_hook(" ".join(script_text.split(".")[:2]))
    print(f"[Script] Short hook score: {_hook_score}/10")
    if _hook_score < 8:
        print("[Script] Hook score < 8 — running pick_best_hook with gpt-4o...")
        script_text = pick_best_hook(script_text, topic=topic, series=series_name)
        _hook_score = _score_hook(" ".join(script_text.split(".")[:2]))
        print(f"[Script] Hook score after improvement: {_hook_score}/10")

    # ── Word budget gated by hook score ─────────────────────────────────────
    _max_short = 230 if _hook_score >= 9 else 215
    if clean_word_count(script_text) > _max_short:
        script_text = _trim_plain_text_to_words(script_text, _max_short)
        print(f"[Script] Short trimmed to {_max_short} words (hook score {_hook_score}/10)")

    script_text = evaluate_and_fix_script(script_text)

    # ── Arabic short: GPT-4o rewrite (no Google Translate) ───────────────────
    ar_script_text = _translate_arabic_short_script(script_text, topic, series_name) if script_text else ""
    if ar_script_text:
        _ar_short_wc = clean_word_count(ar_script_text)
        _ar_short_secs = estimate_short_duration_secs(ar_script_text, "arabic")
        print(f"[SHORT RUNTIME] AR: {_ar_short_wc} words → ~{_ar_short_secs:.0f}s")
        if _ar_short_wc < 200:
            print(f"[SHORT EXPANSION] AR short under 200 words ({_ar_short_wc}) — expanding to 230")
            ar_script_text = expand_short_script(ar_script_text, "arabic", topic, 230)
            _ar_short_wc = clean_word_count(ar_script_text)
            _ar_short_secs = estimate_short_duration_secs(ar_script_text, "arabic")
            print(f"[SHORT RUNTIME] AR after expansion: {_ar_short_wc} words → ~{_ar_short_secs:.0f}s")
        else:
            print(f"[SHORT PASSED] AR: {_ar_short_wc} words → ~{_ar_short_secs:.0f}s")

    short_data = {
        "title":            en_long_script.get("title", ""),
        "hook":             en_long_script.get("hook", script_text[:100]),
        "script":           script_text,
        "short_script_en":  script_text,
        "short_script_ar":  ar_script_text,
        "on_screen_texts":  en_long_script.get("on_screen_texts", [])[:2],
        "caption":          en_long_script.get("caption", ""),
        "hashtags":         en_long_script.get("hashtags", ""),
        "thumbnail_text":   en_long_script.get("thumbnail_text", ""),
        "topic":            en_long_script.get("topic", ""),
        "niche":            en_long_script.get("niche", en_long_script.get("topic", "")),
        "search_query":     en_long_script.get("search_query", en_long_script.get("topic", "")),
        "keywords":         en_long_script.get("keywords", [en_long_script.get("topic", "")] if en_long_script.get("topic") else []),
        "language":         "english",
        "manual_topic":     bool(en_long_script.get("manual_topic")),
    }
    _short_title = add_short_title(short_data)
    short_data["title"]       = _short_title
    short_data["short_title"] = _short_title
    if short_data.get("script"):
        short_data["script"] = evaluate_and_fix_script(short_data["script"])
    _en_secs = estimate_short_duration_secs(script_text, "english")
    _ar_secs = estimate_short_duration_secs(ar_script_text, "arabic")
    print(
        f"[SHORT RUNTIME] Final EN: {clean_word_count(script_text)} words → ~{_en_secs:.0f}s | "
        f"AR: {clean_word_count(ar_script_text)} words → ~{_ar_secs:.0f}s"
    )
    print(f"[Script] Short script done: '{short_data['title']}' ({clean_word_count(script_text)} words EN, {clean_word_count(ar_script_text)} words AR)")
    return short_data


# ── Cinematic Shorts Pipeline ─────────────────────────────────────────────────

def _extract_scene_beat_map(script_text: str, topic: str, max_beats: int = 6) -> list[dict]:
    """Extract the strongest cinematic beat candidates from a long script."""
    snippet = " ".join(script_text.split()[:3000])
    prompt = (
        f"Read this crime documentary script about: {topic}\n\n"
        f"Identify the {max_beats} strongest cinematic moments that could each stand alone "
        f"as a 60-90 second micro-story. Each moment must be:\n"
        f"- ONE specific event (a raid, testimony, discovery, betrayal, or reveal)\n"
        f"- Emotionally focused — not a case summary\n"
        f"- Rich enough for atmosphere + tension in 90 seconds\n\n"
        f"For each moment return a JSON object with these exact keys:\n"
        f"  label      — short name for this beat (e.g. 'The FBI Raid', 'The Missing Tape')\n"
        f"  hook_line  — ONE shocking opening sentence (no setup, drop straight in)\n"
        f"  event      — what specifically happens (2-3 sentences)\n"
        f"  setting    — where and when (specific)\n"
        f"  escalation — the disturbing implication this event reveals\n"
        f"  mystery    — what remains unresolved or unanswered\n\n"
        f"Return ONLY a JSON array of {max_beats} objects. No explanation. No markdown fences.\n\n"
        f"SCRIPT:\n{snippet}"
    )
    try:
        raw = _ai_script_call(prompt, max_tokens=1800, temperature=0.55, premium=True)
        if not raw:
            return []
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            return []
        beats = json.loads(m.group(0))
        if not isinstance(beats, list):
            return []
        return [
            b for b in beats
            if isinstance(b, dict) and b.get("label") and b.get("hook_line")
        ][:max_beats]
    except Exception as _e:
        print(f"[Cinematic Shorts] Beat map extraction failed: {_e}")
        return []


def write_cinematic_short(
    beat: dict,
    topic: str,
    series_name: str = "",
    entity_anchor: str = "",
) -> dict:
    """Generate a standalone EN + AR cinematic micro-short for one scene beat (60-90s each)."""
    label      = beat.get("label", "")
    hook_line  = beat.get("hook_line", "")
    event      = beat.get("event", "")
    setting    = beat.get("setting", "")
    escalation = beat.get("escalation", "")
    mystery    = beat.get("mystery", "")
    _series_line = f"Series/Show: {series_name}\n" if series_name else ""

    _fact_lock_en = (
        f"FACT LOCK — only use names and events from this reference:\n"
        f"{entity_anchor[:300]}\n"
        f"Do NOT invent new people, witnesses, investigators, or organizations.\n\n"
        if entity_anchor else ""
    )

    en_prompt = (
        f"You are writing a spoken voiceover for a 60-90 second cinematic crime short video.\n"
        f"Topic: {topic}\n"
        f"{_series_line}"
        f"{_fact_lock_en}"
        f"SCENE BEAT:\n"
        f"  Label:      {label}\n"
        f"  Event:      {event}\n"
        f"  Setting:    {setting}\n"
        f"  Escalation: {escalation}\n"
        f"  Mystery:    {mystery}\n\n"
        f"STRICT 5-PHASE STRUCTURE (continuous prose — NO headings or labels in the output):\n"
        f"[0-5s]   SHOCK HOOK   — Begin with EXACTLY: \"{hook_line}\" (1 sentence, no setup)\n"
        f"[5-20s]  ATMOSPHERE   — 2-3 sentences: place, time, visual tension. Zero facts yet.\n"
        f"[20-50s] ONE EVENT    — 4-5 sentences: this single event only. Specific names. Specific action.\n"
        f"[50-75s] ESCALATION   — 2-3 sentences: disturbing new implication, deeper layer.\n"
        f"[75-90s] PUNCH ENDING — 1-2 sentences: emotional gut-punch, leave it unresolved.\n\n"
        f"RULES:\n"
        f"- Max 14 words per sentence. Shorter is stronger.\n"
        f"- No channel intros. No subscribe CTAs. No broad context dumps.\n"
        f"- One story beat only — NOT a summary of the whole case.\n"
        f"- Every sentence escalates tension. Never flat.\n"
        f"- Target 155-200 words total.\n\n"
        f"Write ONLY the spoken words. No headings. No labels."
    )

    en_text = ""
    for _att in range(3):
        _r = _ai_script_call(en_prompt, max_tokens=550, temperature=0.82, premium=True).strip()
        _wc = clean_word_count(_r)
        if _wc > clean_word_count(en_text):
            en_text = _r
        if _wc >= 140:
            break
        print(f"[Cinematic Short] EN '{label}' attempt {_att+1}: {_wc}w — retrying...")

    if not en_text:
        print(f"[Cinematic Short] EN '{label}' failed — skipping")
        return {}

    en_wc   = clean_word_count(en_text)
    en_secs = round(en_wc / _TTS_WPM.get("english", 145) * 60)
    print(f"[Cinematic Short] EN '{label}': {en_wc}w → ~{en_secs}s")

    # Arabic: generate standalone from the same beat for cinematic quality
    _ar_entity_lock = (
        f"[قفل الشخصيات] استخدم فقط الأشخاص والأحداث من المرجع التالي. "
        f"لا تخترع أشخاصاً أو شهوداً أو محققين جدداً:\n"
        f"{entity_anchor[:300]}\n\n"
        if entity_anchor else ""
    )

    ar_prompt = (
        f"أنت راوٍ وثائقي سينمائي. اكتب مقطعاً قصيراً بالعربية الفصحى (60-90 ثانية).\n"
        f"الموضوع: {topic}\n"
        f"{_ar_entity_lock}"
        f"المشهد المحدد:\n"
        f"  الحدث:         {event}\n"
        f"  المكان والزمان: {setting}\n"
        f"  التصعيد:       {escalation}\n"
        f"  الغموض:        {mystery}\n\n"
        f"الهيكل الصارم (نثر متواصل — بلا عناوين في النص):\n"
        f"[0-5ث]   الخطاف: جملة واحدة صادمة. لا مقدمة.\n"
        f"[5-20ث]  الجو: 2-3 جمل. توتر بصري ومكاني. لا حقائق بعد.\n"
        f"[20-50ث] حدث واحد: 4-5 جمل عن هذا المشهد تحديداً. أسماء حقيقية. فعل محدد.\n"
        f"[50-75ث] التصعيد: 2-3 جمل. الدلالة المقلقة. طبقة جديدة.\n"
        f"[75-90ث] النهاية المعلقة: 1-2 جمل. الضربة العاطفية. اتركها مفتوحة.\n\n"
        f"القواعد:\n"
        f"- لا جملة تتجاوز 18 كلمة. الأقصر أقوى.\n"
        f"- لا مقدمات. لا CTAs. لا سياق عام.\n"
        f"- ابدأ فوراً بالتوتر — بلا تمهيد.\n"
        f"- مشهد واحد فقط — ليس ملخص القضية.\n"
        f"- الهدف: 250-330 كلمة.\n\n"
        f"اكتب النص المنطوق فقط."
    )

    ar_text = ""
    for _att in range(2):
        _r = _ai_script_call(ar_prompt, max_tokens=800, temperature=0.82, premium=True).strip()
        _wc = clean_word_count(_r)
        if _wc > clean_word_count(ar_text):
            ar_text = _r
        if _wc >= 220:
            break

    if not ar_text:
        ar_text = translate_to_arabic(en_text)

    ar_wc   = clean_word_count(ar_text)
    ar_secs = round(ar_wc / _TTS_WPM.get("arabic", 250) * 60)
    print(f"[Cinematic Short] AR '{label}': {ar_wc}w → ~{ar_secs}s")

    return {
        "beat_label":         label,
        "short_script_en":    en_text,
        "short_script_ar":    ar_text,
        "hook_line":          hook_line,
        "en_duration_secs":   en_secs,
        "ar_duration_secs":   ar_secs,
        "is_cinematic_short": True,
    }


def generate_cinematic_shorts(en_long_script: dict, count: int = 5) -> list[dict]:
    """Generate 3-8 standalone cinematic shorts from the strongest scene beats of the long script."""
    topic       = en_long_script.get("topic", "")
    script_text = en_long_script.get("script", "")
    series_name = en_long_script.get("series_name", "")

    if not script_text or not topic:
        print("[Cinematic Shorts] No script or topic — skipping")
        return []

    count         = max(3, min(8, count))
    entity_anchor = " ".join(script_text.split()[:200])

    print(f"[Cinematic Shorts] Extracting up to {count} beat candidates from long script...")
    beats = _extract_scene_beat_map(script_text, topic, max_beats=count)

    if not beats:
        print("[Cinematic Shorts] Beat extraction returned no results — skipping")
        return []

    print(f"[Cinematic Shorts] {len(beats)} beats found: {[b.get('label','?') for b in beats]}")

    shorts = []
    for i, beat in enumerate(beats):
        print(f"[Cinematic Shorts] Generating short {i+1}/{len(beats)}: '{beat.get('label','?')}'")
        result = write_cinematic_short(beat, topic, series_name, entity_anchor)
        if result and result.get("short_script_en"):
            result.update({
                "topic":       topic,
                "series_name": series_name,
                "language":    "english",
            })
            shorts.append(result)

    print(f"[Cinematic Shorts] Complete: {len(shorts)}/{count} shorts generated")
    return shorts


def write_scripts(topics: list[dict]) -> list[dict]:
    """Write English script then generate Arabic for each topic."""
    scripts = []
    for topic in topics:
        en_script = write_script(topic, language="english")
        ar_script = translate_script(en_script, research=topic.get("research"))
        scripts.append(ar_script)
        scripts.append(en_script)
    return scripts


# ══════════════════════════════════════════════════════════════════════════════
#  Language-isolated Arabic pipeline
#  These functions generate Arabic output WITHOUT a finished English script,
#  enabling full failure isolation between the two language pipelines.
# ══════════════════════════════════════════════════════════════════════════════

def write_arabic_script(topic: dict, research: dict | None = None) -> dict:
    """
    Generate a full Arabic documentary script INDEPENDENTLY.

    Does not require a finished English script as input.  Drives from
    topic metadata + research directly, applying identical post-processing
    (expansion, runtime validation, cleanup) as translate_script().

    Fallback chain:
      1. Native research path (_write_arabic_from_research + Arabic research)
      2. Groq direct (same function, empty research facts)
      3. script_too_short=True flagged → render blocked
    """
    topic_str   = topic.get("topic", "")
    series_name = topic.get("series_name", "")
    series_type = topic.get("series_type", "") or (
        topic.get("niche", "").split("behind")[-1].strip()
        if "behind" in topic.get("niche", "") else ""
    )
    part_number = topic.get("part_number")

    # ── Arabic title ──────────────────────────────────────────────────────────
    _is_hemedti = any(k in topic_str.lower() for k in ["hemedti", "حميدتي", "dagalo"])
    if _is_hemedti:
        ar_title = _build_hemedti_arabic_title(part_number)
    else:
        ar_title = _generate_arabic_title_llm(
            topic_str=topic_str,
            angle_title=topic.get("angle_title", ""),
            series_name=series_name,
            series_type=series_type,
        )

    # ── Runtime target ────────────────────────────────────────────────────────
    _ar_target_minutes = float(os.getenv("AR_TARGET_MINUTES", "0") or "0")
    if _ar_target_minutes <= 0:
        _ar_target_minutes = {
            "fast": 30.0, "animation": 35.0, "full": 45.0,
        }.get(os.getenv("PIPELINE_MODE", "fast").lower(), 30.0)
    print(f"[AR] Independent pipeline | topic='{topic_str}' | target={_ar_target_minutes}min")

    # Minimal metadata dict for _write_arabic_from_research (no English body needed)
    _meta = {
        "topic":       topic_str,
        "series_name": series_name,
        "series_type": series_type,
        "script":      "",
    }

    # ── Generate Arabic body ──────────────────────────────────────────────────
    _ar_script_body = ""
    _used_path      = "groq_direct"

    # Path A: native research
    if research:
        print("[AR] Writing from research (independent path)...")
        try:
            ar_research     = translate_research_to_arabic(research)
            _ar_script_body = _write_arabic_from_research(
                _meta, ar_research, target_minutes=_ar_target_minutes,
            )
            if _ar_script_body and clean_word_count(_ar_script_body) >= 300:
                _core_present = any(
                    m in _ar_script_body
                    for m in ["القصة الحقيقية", "الرواية مقابل الواقع", "حقائق صادمة"]
                )
                if _core_present:
                    _ar_script_body = fix_first_mention(_ar_script_body, is_arabic=True)
                    _used_path      = "research"
                    print(f"[AR] Research path: {clean_word_count(_ar_script_body)}w ✅")
                else:
                    print("[AR] Missing mandatory section — Groq direct fallback")
                    _ar_script_body = ""
            else:
                print("[AR] Research path short/empty — Groq direct fallback")
                _ar_script_body = ""
        except Exception as _e:
            print(f"[AR] Research path failed ({_e}) — Groq direct fallback")
            _ar_script_body = ""

    # Path B: Groq direct (same LLM path, empty research facts)
    if not _ar_script_body:
        print("[AR] Groq direct generation...")
        try:
            _empty_ar_research = {
                "ar_research_facts": [], "ar_research_inaccuracies": [],
                "ar_research_shocking": [], "ar_user_discovery": "",
            }
            _ar_script_body = _write_arabic_from_research(
                _meta, _empty_ar_research, target_minutes=_ar_target_minutes,
            )
            if _ar_script_body:
                _ar_script_body = fix_first_mention(_ar_script_body, is_arabic=True)
                _used_path = "groq_direct"
                print(f"[AR] Groq direct: {clean_word_count(_ar_script_body)}w")
        except Exception as _e2:
            print(f"[AR] Groq direct failed ({_e2}) — script empty")
            _ar_script_body = ""

    # ── Build ar_data ─────────────────────────────────────────────────────────
    ar_data = {
        "title":           ar_title,
        "hook":            "",
        "script":          _ar_script_body,
        "on_screen_texts": [],
        "caption":         "",
        "hashtags":        "",
        "thumbnail_text":  "",
        "chapters":        "",
        "topic":           topic_str,
        "niche":           topic.get("niche", topic_str),
        "search_query":    topic.get("search_query", topic_str),
        "keywords":        topic.get("keywords", [topic_str] if topic_str else []),
        "language":        "arabic",
        "manual_topic":    bool(topic.get("manual_topic")),
        "series_name":     series_name,
        "series_type":     series_type,
        "arabic_path":     _used_path,
    }

    # ── Post-processing (mirrors translate_script exactly) ────────────────────
    _ar_wc         = clean_word_count(ar_data.get("script", ""))
    _pipeline_mode = os.getenv("PIPELINE_MODE", "fast").lower()
    if _pipeline_mode not in _WORD_FLOORS:
        _pipeline_mode = "fast"
    _AR_WORD_FLOOR = _WORD_FLOORS[_pipeline_mode]["arabic"]
    _AR_WORD_TGT   = int(_AR_WORD_FLOOR * 1.1)
    if _ar_wc < _AR_WORD_TGT:
        print(f"[AR RUNTIME] {_ar_wc}w below target {_AR_WORD_TGT}w — expanding...")
        ar_data["script"] = _expand_arabic_script_to_min(ar_data["script"], target_min=_AR_WORD_TGT)

    _ar_contract_ref = get_runtime_contract(_pipeline_mode, "arabic")
    _ar_min  = estimate_arabic_duration(ar_data.get("script", ""))
    _ar_wc_n = clean_word_count(ar_data.get("script", ""))
    _ar_tgt  = max(_ar_contract_ref["min_minutes"], _ar_target_minutes)
    print(f"[AR RUNTIME] {_ar_wc_n}w | ~{_ar_min:.1f}min | target={_ar_tgt:.1f}min")
    if _ar_min < _ar_tgt:
        print(f"[AR EXPANSION] {_ar_min:.1f}min < {_ar_tgt:.1f}min — expanding...")
        ar_data["script"] = expand_arabic_runtime(ar_data["script"], target_min=_ar_tgt, topic=topic_str)

    if ar_data.get("script"):
        _locked_script = ar_data["script"]
        _locked_wc     = clean_word_count(_locked_script)
        _locked_min    = _locked_wc / _TTS_WPM["arabic"]
        print(f"[AR] Locked baseline: {_locked_wc}w | {_locked_min:.1f}min")

        ar_data["script"] = upgrade_arabic_script(ar_data["script"])
        _post_up_wc = clean_word_count(ar_data.get("script", ""))
        _regress    = " WARNING=REGRESSION" if _post_up_wc < _locked_wc * 0.90 else ""
        print(f"[AR] Post-upgrade: {_post_up_wc}w{_regress}")

        ar_data["script"] = evaluate_and_fix_script(ar_data["script"])
        from agents.script_quality import (
            normalize_arabic_documentary_text, normalize_arabic_tts,
            enforce_arabic_purity, remove_arabic_filler_phrases,
            validate_information_density,
        )
        ar_data["script"] = normalize_arabic_documentary_text(ar_data["script"])
        ar_data["script"] = normalize_arabic_tts(ar_data["script"])
        ar_data["script"] = tts_readability_pass(ar_data["script"])
        ar_data["script"] = enforce_arabic_purity(ar_data["script"])
        ar_data["script"] = remove_arabic_filler_phrases(ar_data["script"])
        ar_data["script"] = apply_mishkal_tashkeel(ar_data["script"])

        # Section-marker guard — ensure all 3 structural sections survive cleanup
        import re as _re_ar
        _body = ar_data.get("script", "")
        if _body:
            if not _re_ar.search(r'\[SECTION:\s*المقدمة', _body):
                ar_data["script"] = f"[SECTION: المقدمة]\n{_body.lstrip()}"
                _body = ar_data["script"]
                print("[AR] Marker guard: prepended [SECTION: المقدمة]")
            _main_pats = [
                r'\[SECTION:\s*القصة\s*الحقيقية', r'\[SECTION:\s*التصعيد',
                r'\[SECTION:\s*الرواية\s*مقابل', r'\[SECTION:\s*حقائق\s*صادمة',
                r'\[SECTION:\s*الخلفية',
            ]
            if not any(_re_ar.search(p, _body) for p in _main_pats):
                _cm = _re_ar.search(r'\[SECTION:\s*الخاتمة', _body)
                if _cm:
                    ar_data["script"] = _body[:_cm.start()].rstrip() + "\n\n[SECTION: القصة الحقيقية]\n\n" + _body[_cm.start():]
                    _body = ar_data["script"]
                    print("[AR] Marker guard: inserted [SECTION: القصة الحقيقية] before conclusion")
            if not _re_ar.search(r'\[SECTION:\s*الخاتمة', _body):
                _lns = _body.split("\n")
                _sp  = max(int(len(_lns) * 0.85), len(_lns) - 30)
                ar_data["script"] = "\n".join(_lns[:_sp]).rstrip() + "\n\n[SECTION: الخاتمة]\n" + "\n".join(_lns[_sp:]).lstrip()
                print("[AR] Marker guard: inserted [SECTION: الخاتمة]")

        # Runtime preservation guard
        _post_cl_wc  = clean_word_count(ar_data.get("script", ""))
        _post_cl_min = _post_cl_wc / _TTS_WPM["arabic"]
        _floor_min   = _ar_contract_ref["min_minutes"]
        if _post_cl_wc < _locked_wc * 0.90:
            ar_data["script"] = _locked_script
            print(f"[RUNTIME GUARD] Post-cleanup regression — restoring locked ({_locked_wc}w)")
        elif _post_cl_min < _floor_min:
            ar_data["script"] = _locked_script
            print(f"[RUNTIME GUARD] Below contract floor — restoring locked ({_locked_min:.1f}min)")

        _density = validate_information_density(ar_data["script"], language="arabic")
        print(f"[AR] density={_density.get('density_pct',0):.0f}% [{_density.get('verdict','?')}]")

    # ── Runtime estimate — no blocking; pipeline always renders if non-empty ─────
    _final_wc  = clean_word_count(ar_data.get("script", ""))
    _final_min = estimate_arabic_duration(ar_data.get("script", ""))

    ar_data.pop("script_too_short", None)   # never block based on word count
    if _final_wc < 50:
        ar_data["script_too_short"] = True  # only flag truly empty scripts
        print(f"[AR EMPTY] Script has {_final_wc}w — marking as empty, will skip render")

    ar_data["estimated_runtime_min"] = round(_final_min, 1)

    # Arabic chapters — generated from actual script content for cinematic titles
    if _final_wc > 0 and ar_data.get("script"):
        ar_data["chapters"] = generate_chapters_from_script(
            ar_data["script"], topic_str, "arabic",
        )

    print(
        f"[Script] Arabic (independent): '{ar_title}' | "
        f"{_final_wc}w | ~{_final_min:.1f}min | path={_used_path}"
    )
    return ar_data


def write_arabic_short(ar_long_script: dict) -> dict:
    """
    Generate an Arabic short script (60-90 s) directly from the Arabic long script.

    Independent of the English pipeline — no translation from English short.
    Picks the strongest moment from the Arabic long script and rewrites it
    as a standalone viral voiceover in native Arabic.
    """
    import re as _re

    topic       = ar_long_script.get("topic", "")
    long_script = ar_long_script.get("script", "")
    series_name = ar_long_script.get("series_name", "")

    if not long_script:
        print("[AR Short] No Arabic long script — returning empty")
        return {"short_script_ar": ""}

    # Extract the best window: skip the intro, use the middle/climax of the script
    # Split by [SECTION:] markers and skip the first section (intro)
    _sections = _re.split(r'\[SECTION:[^\]]+\]', long_script)
    _sections = [s.strip() for s in _sections if s.strip()]
    if len(_sections) >= 3:
        # Skip intro (first section), use sections 2-4 where climax usually lives
        _source_window = " ".join(_sections[1:4])[:3000]
    elif len(_sections) == 2:
        _source_window = _sections[1][:3000]
    else:
        # No markers — skip first 20% (intro) and take 3000 chars from middle
        _skip = max(0, len(long_script) // 5)
        _source_window = long_script[_skip:_skip + 3000]

    _series_line = f"المسلسل/الفيلم: {series_name}\n" if series_name else ""

    prompt = f"""المهمة: اقرأ النص المصدر أدناه. اعثر على اللحظة الأكثر إثارة — اعتراف، انقلاب، حكم، أو حقيقة مخفية. أعد كتابتها كتعليق صوتي مستقل وفيروسي. لا تلخّص القصة كلها. أخبر لحظة واحدة، بسرعة وقوة.

الموضوع: {topic}
{_series_line}
الهيكل — اكتبه كنص متصل بلا عناوين:
- الخطّاف (2-3 جمل): ابدأ بالفعل أو الحقيقة الصادمة. لا مقدمات. لا "في عام..."
- البناء (2-3 جمل): من كان متورطاً؟ ما الذي كان على المحك؟ أسماء وتفاصيل محددة.
- الكشف الرئيسي (4-5 جمل): الحقيقة التي غيّرت كل شيء. الحادثة المحورية من النص.
- الخاتمة (2 جملة): جملة تبقى في الذهن. اختم بـ: "تابع Dark Crime Decoded لمزيد."

قيود صارمة:
- الحد الأقصى: 12 كلمة لكل جملة. الأقصر أقوى.
- كل جملة يجب أن تكشف معلومة جديدة أو تُصعّد التوتر.
- محظور تاماً: "يتم" في أي صيغة — الفاعل يجب أن يكون صريحاً.
- محظور تاماً: "كانت" أو "كان" كبداية لأي جملة.
- تنويع الكلمة الأولى: لا تبدأ جملتان متتاليتان بنفس الكلمة. لا تكرر كلمة افتتاحية أكثر من مرتين.
- النمط المحظور: كانت X. كانت Y. كانت Z. — هذا فشل فوري.
- النمط الصحيح: اعتقله. فتح المحقق. رفض القاضي. كشف التحقيق.
- محظور: القوائم النقطية، الملخصات، النبرة الأكاديمية.
- محظور: أي عناوين أو تسميات في المخرجات.
- الطول المستهدف: 230-260 كلمة. الحد الأدنى المطلق: 200 كلمة.

النص المصدر (استخرج منه أفضل لحظة):
{_source_window}

اكتب التعليق الصوتي فقط. لا عناوين. لا تسميات. لا شرح."""

    ar_text = ""
    best_text = ""

    def _ar_short_fix_note(text: str) -> str:
        _bad, _word, _cnt = _check_repeated_sentence_starters(text)
        if not _bad:
            return ""
        return (
            f"\n\nخطأ في المحاولة السابقة: الكلمة \"{_word}\" تفتح {_cnt} جمل — الحد الأقصى 3. "
            f"أعد الكتابة بالكامل. كل جملة يجب أن تبدأ بكلمة مختلفة عن الجملة السابقة."
        )

    # Phase 1: OpenAI GPT-4o primary (2 attempts)
    for attempt in range(2):
        _p = prompt
        if attempt > 0 and ar_text:
            _wc_prev = clean_word_count(ar_text)
            _size_note = "وسّع القسمين الثاني والثالث بحقائق محددة." if _wc_prev < 200 else "احذف الحشو واحتفظ بالحقائق فقط."
            _p += f"\n\nالمحاولة السابقة: {_wc_prev} كلمة — الهدف 230-260. {_size_note}"
            _p += _ar_short_fix_note(ar_text)
        _result = _ai_script_call(_p, max_tokens=700, temperature=0.85,
                                   system_prompt=_AR_SHORT_SCRIPT_SYSTEM, premium=True).strip()
        _wc = clean_word_count(_result)
        _bad_r, _word_r, _cnt_r = _check_repeated_sentence_starters(_result)
        print(f"[AR Short] GPT-4o attempt {attempt + 1}: {_wc}w | starter repeat: {'BAD ('+_word_r+' ×'+str(_cnt_r)+')' if _bad_r else 'OK'}")
        if _wc > clean_word_count(best_text) and not _bad_r:
            best_text = _result
        elif not best_text:
            best_text = _result
        ar_text = _result
        if _wc >= 200 and not _bad_r:
            break

    # Phase 2: Groq fallback if GPT-4o under 200 words or has repeated starters
    if clean_word_count(ar_text) < 200 or _check_repeated_sentence_starters(ar_text)[0]:
        print("[AR Short] GPT-4o issues — Groq fallback")
        for attempt in range(2):
            try:
                _suffix = "\n\nاكتب على الأقل 200 كلمة. الهدف 230-260."
                _suffix += _ar_short_fix_note(ar_text)
                _r = _groq_call(
                    messages=[{"role": "user", "content": prompt + _suffix}],
                    max_tokens=700,
                    temperature=0.85,
                )
                _result = _r.choices[0].message.content.strip()
                _wc = clean_word_count(_result)
                _bad_r, _word_r, _cnt_r = _check_repeated_sentence_starters(_result)
                print(f"[AR Short] Groq attempt {attempt + 1}: {_wc}w | starter repeat: {'BAD ('+_word_r+' ×'+str(_cnt_r)+')' if _bad_r else 'OK'}")
                if _wc > clean_word_count(best_text) and not _bad_r:
                    best_text = _result
                ar_text = _result
                if _wc >= 200 and not _bad_r:
                    break
            except Exception as _e:
                print(f"[AR Short] Groq error (attempt {attempt + 1}): {_e}")
                import time as _time; _time.sleep(8)

    if not ar_text and best_text:
        ar_text = best_text
    # Always prefer a clean version over a repeated-starter one
    if _check_repeated_sentence_starters(ar_text)[0] and best_text and not _check_repeated_sentence_starters(best_text)[0]:
        ar_text = best_text

    # Enforce minimum via expand_short_script
    if ar_text and clean_word_count(ar_text) < 200:
        ar_text = expand_short_script(ar_text, "arabic", topic, 230)

    _final_bad, _final_word, _final_cnt = _check_repeated_sentence_starters(ar_text)
    _final_wc   = clean_word_count(ar_text)
    _final_secs = estimate_short_duration_secs(ar_text, "arabic") if ar_text else 0.0
    if _final_bad:
        print(f"[AR Short] WARNING: repeated starter '{_final_word}' ×{_final_cnt} in final output")
    print(f"[AR Short] Final: {_final_wc}w → ~{_final_secs:.0f}s")

    return {
        "short_script_ar":  ar_text,
        "ar_duration_secs": _final_secs,
        "topic":            topic,
        "series_name":      series_name,
        "language":         "arabic",
    }
