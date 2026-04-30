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

_groq = Groq(api_key=GROQ_API_KEY)

_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",   # primary
    "llama-3.1-8b-instant",      # fallback
]


def _groq_call(**kwargs):
    """Try each model with one 40-second retry on rate limit before moving to fallback."""
    import time
    last_err = None
    for model in _FALLBACK_MODELS:
        for attempt in range(2):
            try:
                time.sleep(3)
                return _groq.chat.completions.create(model=model, **kwargs)
            except groq_lib.RateLimitError as e:
                last_err = e
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



_SCRIPT_SYSTEM_PROMPT = """You are a professional true crime documentary scriptwriter. Write like a BBC/Netflix narrator — measured, cinematic, authoritative.

DOCUMENTARY VOICE — NOT AN ESSAY:
- This is SPOKEN narration, not text to be read on a screen. Every sentence must sound natural when read aloud.
- Never open with generic lines: "In the world of crime...", "Throughout history...", "This story is about..."
- Never write like a Wikipedia article, a blog post, or an essay. No thesis statements. No topic sentences announcing what you will cover.
- Write the scene, not the summary. Show the moment, then draw meaning from it.
- Every paragraph must EARN its place — no padding, no throat-clearing, no restatements of what was just said.

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

TRANSITION PHRASES — use a DIFFERENT one for each chapter, never repeat:
- "What nobody expected was..."
- "The truth was far more disturbing..."
- "Behind closed doors, however..."
- "What the cameras never showed..."
- "Decades later, the full picture finally emerged..."
- "The official story was only half the truth..."
- "What they never spoke about publicly was..."
- "The case files told a different story..."
- "What would later emerge changed everything..."
- "The reality was far darker than anyone knew..."
FORBIDDEN: Never repeat the same transition phrase twice in the same script.

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

NARRATIVE TENSION — sustained across every chapter:
- Each chapter must introduce a new unresolved question, hidden conflict, or suppressed truth.
- Do NOT fully resolve tension within the same chapter — leave something open that pulls into the next.
- The viewer must always feel: there is something I still do not know.

HOOK QUALITY — first 1-2 sentences of every chapter:
- Must contain ONE of: a contradiction, a hidden truth, a shocking omission, or an open question.
- Strong examples: "Everyone knew his name. Nobody knew his real one." / "The police had the evidence. They buried it."
- NEVER open with background, dates, or scene-setting. Start with the tension, not the context.

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
- This structure is MANDATORY for any topic based on true events."""


def clean_word_count(text: str) -> int:
    """Count only real vocabulary words — strips punctuation, ellipses, line breaks."""
    import re
    cleaned = re.sub(r'[^\w\s]', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return len([w for w in cleaned.split() if w.strip()])


LONG_SCRIPT_MIN_WORDS = 1450
LONG_SCRIPT_MAX_WORDS = 1900


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


def _groq_fallback(prompt: str, max_tokens: int, json_mode: bool,
                   system_prompt: str | None = None) -> str:
    """Groq with aggressive prompt truncation and 3-model retry chain."""
    import os
    import time

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key:
        print("[Script] No Groq key available")
        return ""

    from groq import Groq
    groq_client = Groq(api_key=groq_key)

    # For JSON mode: instruct in the prompt instead of using response_format
    # (response_format is only supported on select Groq models)
    if json_mode and "valid JSON" not in prompt:
        prompt = prompt + "\n\nRespond with valid JSON only, no markdown, no explanation."

    # Keep beginning + end to preserve context within 3000-char limit
    max_chars = 3000
    if len(prompt) > max_chars:
        half   = max_chars // 2
        prompt = prompt[:half] + "\n...\n" + prompt[-half:]
        print(f"[Script] Prompt truncated to {max_chars} chars for Groq")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

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
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[Script] Groq {model} failed: {e}")
            time.sleep(5)

    return ""


_OPENAI_QUOTA_EXCEEDED = False


def _ai_script_call(prompt: str, max_tokens: int = 1000,
                    json_mode: bool = False, temperature: float = 0.7,
                    system_prompt: str | None = None,
                    premium: bool = False) -> str:
    """Route script calls by quality tier.

    premium=True  → OpenAI gpt-4o (primary) → Groq fallback  — long-form sections
    premium=False → Groq (primary) → OpenAI gpt-4o-mini fallback — cheap helpers
    """
    import requests as _req
    global _OPENAI_QUOTA_EXCEEDED

    if premium:
        # ── Premium path: OpenAI gpt-4o → Groq fallback ─────────────────────
        api_key = os.getenv('OPENAI_API_KEY', '').strip()
        if api_key and not _OPENAI_QUOTA_EXCEEDED:
            try:
                messages = []
                if system_prompt:
                    messages.append({'role': 'system', 'content': system_prompt})
                messages.append({'role': 'user', 'content': prompt})
                r = _req.post(
                    'https://api.openai.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {api_key}',
                             'Content-Type': 'application/json'},
                    json={'model': 'gpt-4o', 'messages': messages,
                          'max_tokens': max_tokens, 'temperature': temperature},
                    timeout=120,
                )
                if r.status_code == 200:
                    print('[Script] OpenAI gpt-4o ✅')
                    return r.json()['choices'][0]['message']['content'].strip()
                if r.status_code == 429:
                    _OPENAI_QUOTA_EXCEEDED = True
                    print('[Script] OpenAI quota exceeded — falling back to Groq')
                else:
                    print(f'[Script] OpenAI gpt-4o HTTP {r.status_code} — falling back to Groq')
            except Exception as e:
                print(f'[Script] OpenAI gpt-4o failed: {e} — falling back to Groq')
        # Groq fallback for premium path
        try:
            result = _groq_fallback(prompt, max_tokens, json_mode, system_prompt=system_prompt)
            if result:
                print('[Script] Groq fallback used (premium path)')
                return result
        except Exception as e:
            print(f'[Script] Groq fallback failed: {e}')
        return ""

    # ── Standard path: Groq primary → OpenAI gpt-4o-mini fallback ───────────
    try:
        result = _groq_fallback(prompt, max_tokens, json_mode, system_prompt=system_prompt)
        if result:
            return result
    except Exception as e:
        print(f'[Script] Groq failed: {e}')

    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if api_key and not _OPENAI_QUOTA_EXCEEDED:
        try:
            messages = []
            if system_prompt:
                messages.append({'role': 'system', 'content': system_prompt})
            messages.append({'role': 'user', 'content': prompt})
            r = _req.post(
                'https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                json={'model': 'gpt-4o-mini', 'messages': messages,
                      'max_tokens': max_tokens, 'temperature': temperature},
                timeout=60,
            )
            if r.status_code == 200:
                print('[Script] Used OpenAI gpt-4o-mini fallback')
                return r.json()['choices'][0]['message']['content'].strip()
            elif r.status_code == 429:
                _OPENAI_QUOTA_EXCEEDED = True
                print('[Script] OpenAI quota exceeded')
        except Exception as e:
            print(f'[Script] OpenAI failed: {e}')

    return ""


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

_HOOK_GEN_PROMPT = """You are a YouTube retention expert writing opening hooks for a true crime documentary.

Read this script excerpt and generate 3 different opening hook variations.

Rules:
- Each hook is 1-2 sentences only
- Strong curiosity, contradiction, or mystery
- Spoken tone — sounds natural when read aloud by a narrator
- No labels or headings inside the hook text itself

Return EXACTLY this format:
HOOK 1: [your hook here]
HOOK 2: [your hook here]
HOOK 3: [your hook here]

SCRIPT EXCERPT:
{script_excerpt}"""

_HOOK_SCORE_PROMPT = """Score this documentary opening hook for YouTube retention.

Score 0-10 based on:
- Curiosity: does it make the viewer need to know more?
- Clarity: is it immediately clear what the story is about?
- Emotional impact: does it create tension, shock, or intrigue?
- Mystery / contradiction: does it plant an unanswered question?

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
        result = _ai_script_call(prompt, max_tokens=50, temperature=0.3, premium=False)
        return _extract_score(result)
    except Exception:
        return 0


def pick_best_hook(script: str) -> str:
    try:
        import re as _re
        excerpt = _re.sub(r'\[SECTION:[^\]]*\]', '', script).strip()[:500]
        prompt = _HOOK_GEN_PROMPT.replace("{script_excerpt}", excerpt)
        raw = _ai_script_call(prompt, max_tokens=300, temperature=0.85, premium=False)
        hooks = _parse_hooks(raw)
        if not hooks:
            print("[Hook] No hooks parsed — keeping original")
            return script

        print(f"[Hook] Generated {len(hooks)} candidates")
        best_hook, best_score = hooks[0], 0
        for h in hooks:
            s = _score_hook(h)
            print(f"[Hook] {s}/10: {h[:70]}")
            if s > best_score:
                best_score, best_hook = s, h
        print(f"[Hook] Best score: {best_score}/10")

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
    "btk":             ("Mindhunter",            "Series"),
    "ted bundy":       ("Extremely Wicked",      "Movie"),
    "ed gein":         ("Psycho",                "Movie"),
    "lucky luciano":   ("The Godfather",         "Movie"),
    "frank lucas":     ("American Gangster",     "Movie"),
    "henry hill":      ("Goodfellas",            "Movie"),
    "whitey bulger":   ("Black Mass",            "Movie"),
    "dexter morgan":   ("Dexter",                "Series"),
    "dexter":          ("Dexter",                "Series"),
    "btk killer":      ("BTK",                   "Series"),
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
    """Return (series_name, type) tuple or None if no match."""
    topic_lower = topic_text.lower()
    for person, info in PERSON_TO_SERIES.items():
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


def _build_arabic_title(en_title: str, series_name: str | None, series_type: str | None) -> str:
    """Return clean Arabic title, falling back to Google Translate + noise cleanup."""
    ar_entry = SERIES_ARABIC.get(series_name or "")
    if ar_entry:
        ar_series, ar_type = ar_entry
        return f"القصة الحقيقية وراء {ar_type} {ar_series} | Dark Crime Decoded"
    # No dict entry — use type word with original English series name
    if series_name:
        ar_type = "فيلم" if series_type == "Movie" else "مسلسل" if series_type == "Series" else ""
        if ar_type:
            return f"القصة الحقيقية وراء {ar_type} {series_name} | Dark Crime Decoded"
    # Fallback: translate the angle-based English title then clean noise
    raw = translate_to_arabic(en_title)
    return _clean_arabic_title(raw)


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
    words_per_minute = 156
    total_seconds = (total_words / words_per_minute) * 60

    if language == "arabic":
        angle_label = angle_title or "الحقيقة الخفية"
        labels = [
            "🎬 مقدمة",
            f"🔍 {angle_label}",
            "📖 القصة الحقيقية",
            "⚡ المسلسل مقابل الواقع",
            "🎯 الخاتمة",
        ]
    else:
        angle_label = angle_title or "The Hidden Truth"
        labels = [
            "Introduction",
            angle_label,
            "The Real Story",
            "What They Got Wrong",
            "The Truth Revealed",
        ]

    chapters = []
    for ratio, title in zip(_CHAPTER_PROPORTIONS_5, labels):
        seconds = int(total_seconds * ratio)
        mins = seconds // 60
        secs = seconds % 60
        chapters.append(f"{mins:02d}:{secs:02d} {title}")

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


def write_script(topic: dict, language: str = "english") -> dict:
    if _is_shopmart():
        return _write_shopmart_script(topic)
    return _write_darkcrimed_script(topic)


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

    meta = json.loads(_ai_script_call(part2_prompt, max_tokens=600, temperature=0.3, json_mode=True).strip())
    script_data = {
        "title":           meta.get("title", f"Shopmart: {topic['topic']}"),
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
    """Return 'documentary' for topics with no movie/series, else 'series'."""
    topic_lower = topic_text.lower()
    for doc_topic in DOCUMENTARY_ONLY_TOPICS:
        if doc_topic in topic_lower:
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
        entry = json.loads(queue_path.read_text(encoding="utf-8"))
        queue_path.unlink()
        print(f"[Script] Loaded queued Part 2: {entry.get('topic', '')}")
        return entry
    except Exception as e:
        print(f"[Script] Failed to load Part 2 queue: {e}")
        return None


def _is_hemedti_topic(topic_text: str) -> bool:
    """Return True if the topic is about Hemedti / RSF Sudan."""
    t = topic_text.lower()
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

    prompt = f"""You are a documentary scriptwriter covering under-reported world events.
Write a 1800-2000 word documentary script about: {name}{part_label}

This is a DOCUMENTARY style — no movie or series exists for this topic.

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

Good examples:
- The psychological breakdown that almost ended the FBI Behavioral Science Unit
- The woman whose contribution was completely erased from the Netflix show
- The serial killer interview that nobody was supposed to know about
- Why the FBI leadership tried to shut down the entire unit
- What really happened off camera that changed everything

Return JSON only, no extra text:
{{"angle_title": "...", "angle_hook": "...", "angle_content": "..."}}

angle_title: 5-8 word punchy title (e.g. "The Interview That Broke John Douglas")
angle_hook: One shocking sentence that opens the chapter — the most arresting fact
angle_content: 2-3 sentences of specific detail to build the chapter around"""

    try:
        result = _ai_script_call(prompt, max_tokens=350, temperature=0.85, json_mode=True)
        data = json.loads(result.strip())
        if all(k in data for k in ('angle_title', 'angle_hook', 'angle_content')):
            print(f"[Script] Untold angle: {data['angle_title']}")
            return data
    except Exception as e:
        print(f"[Script] Angle generation failed: {e}")

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
    """Write 1,450–1,900 real-word script via 5 OpenAI calls → ~10–14 min runtime."""
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
    _mandatory_instruction = ""
    if _show_chars:
        sc_lines = [
            f"  - {c['character']} (played by {c.get('actor','?')}) → real person: {c.get('based_on','?')} — {c.get('real_role','')}"
            for c in _show_chars
        ]
        _show_chars_block = "SHOW CAST (cover EVERY character below):\n" + "\n".join(sc_lines) + "\n"
        char_names = ", ".join(c['character'] for c in _show_chars)
        real_names = ", ".join(c.get('based_on','?') for c in _show_chars if c.get('based_on') and c.get('based_on','').lower() not in ('null','none','various'))
        _mandatory_instruction = (
            f"\nMANDATORY — THIS VIDEO IS ABOUT A TV SHOW BASED ON TRUE EVENTS:\n"
            f"You MUST cover ALL {len(_show_chars)} main characters: {char_names}\n"
            f"AND their real counterparts: {real_names}\n"
            f"Give each character at least one full paragraph.\n"
            f"Include: what the show got right vs what really happened.\n"
            f"Never focus on just one character or just the real story — show BOTH worlds.\n"
        )

    # Topic facts visible to every chapter (no coverage instruction — that goes in Ch3 only)
    _topic_context = f"""Topic: {name}
Series/Movie: {series} ({stype})
Real person: {research.get('real_person', name)}
Key facts: {(research.get('research_facts') or research.get('what_show_got_right', []))[:3]}
{_show_chars_block}{_real_people_block}{_chars_block}{_rvs_block}{_time_loc}"""

    # Full character-coverage instruction — belongs ONLY in Chapter 3
    _ch3_mandatory = _mandatory_instruction

    base_context = _topic_context  # kept for any legacy references

    # Resolve angle — use passed-in angle or generate one now
    _angle = angle or generate_untold_angle(name, f"{series} {stype}")
    _angle_title   = _angle.get("angle_title", f"The Hidden Truth Behind {name}")
    _angle_hook    = _angle.get("angle_hook", "")
    _angle_content = _angle.get("angle_content", "")

    # (label, min_words, max_words, is_final)
    _SECTIONS_META = [
        ("Opening Atmosphere",    300,  380,  False),
        ("Untold Angle",          350,  420,  False),
        ("Background & Real Story", 420, 560, False),
        ("Show vs Reality",       350,  420,  False),
        ("Final Insight",         200,  260,  True),
    ]

    _SECTION_LABELS = [
        "[SECTION: Introduction]",
        "[SECTION: Untold Angle]",
        "[SECTION: The Real Story]",
        "[SECTION: Show vs Reality]",
        "[SECTION: Conclusion]",
    ]

    def _section_instruction(min_w: int, max_w: int, is_final: bool) -> str:
        conclude = (
            "This is the final section — wrap up the story, deliver final thoughts, "
            "call to action for viewers."
            if is_final else
            "Do not summarize or conclude — the next section will continue the story. "
            "End mid-story."
        )
        return (
            f"Write exactly {min_w}–{max_w} real words for this section. "
            "Real words only — do not count punctuation, ellipses, or line breaks. "
            "No filler. No repetition. Every sentence adds new information. "
            + conclude
        )

    # 10 distinct transition phrases — one picked per section to avoid repetition
    _TRANSITION_PHRASES = [
        "What nobody expected was...",
        "The truth was far more disturbing...",
        "Behind closed doors, however...",
        "What the cameras never showed...",
        "Decades later, the full picture finally emerged...",
        "The official story, however, was only half the truth...",
        "What the case files revealed changed everything...",
        "The reality they faced was far darker than anyone knew...",
        "But something else was happening that the world never saw...",
        "What happened next would shock even the most seasoned investigators...",
    ]

    def _call_section(prompt: str, label: str, min_w: int, max_w: int,
                      call_num: int) -> str | None:
        # Conclusion gets more tokens to prevent mid-sentence cutoff
        _max_tok = 800 if call_num == 5 else 1200
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
        # One retry if below minimum
        if real < min_w:
            print(f"[Script] Section {call_num}: below minimum — retrying once")
            time.sleep(4)
            retry = _ai_script_call(prompt, max_tokens=_max_tok,
                                     system_prompt=_SCRIPT_SYSTEM_PROMPT, premium=True)
            if retry:
                r_real = clean_word_count(retry)
                r_raw  = len(retry.split())
                emoji2 = "✅" if r_real >= min_w else "⚠️"
                print(f"[Script] Section {call_num} retry: {r_real} real words {emoji2} "
                      f"(raw {r_raw})")
                if r_real >= real:
                    result = retry
                    real = r_real

        # Hard cap per section to stop runaway outputs from pushing total runtime.
        if real > max_w:
            result = _trim_plain_text_to_words(result, max_w)
            print(f"[Script] Section {call_num} trimmed to max {max_w} words")
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
        _outline_prompt = f"""You are outlining a documentary script about: {name} (related to {series} {stype}).

Task: Distribute 10–15 unique specific facts across exactly 5 chapters.
Each fact must appear in EXACTLY ONE chapter — never repeated elsewhere.
Every fact must be SPECIFIC: include a real name, date, number, or location.

RESEARCH MATERIAL:
Facts: {_rf}
Shocking details: {_sh}
Show inaccuracies: {_in}

UNTOLD ANGLE (reserved for Chapter 2):
Title: {_angle_title}
Detail: {_angle_content}

Assign facts to chapters using these STRICT roles:
- ch1: 2 facts — (a) the specific scene/moment that made {series} famous; (b) ONE unanswered question it raises
- ch2: 3–4 facts — the hidden truth details ONLY, all expanding on the untold angle above
- ch3: 4–5 facts — chronological documented history in order (earliest first, each with a year)
- ch4: 3–4 facts — direct comparisons only, each formatted as "Show depicted X, reality was Y"
- ch5: 2 facts — aftermath and present-day legacy ONLY (consequences after the main story ended)

Return ONLY valid JSON, no explanation:
{{"ch1": ["...", "..."], "ch2": ["...", "...", "..."], "ch3": ["...", "...", "...", "..."], "ch4": ["...", "...", "..."], "ch5": ["...", "..."]}}"""

        try:
            raw = _ai_script_call(_outline_prompt, max_tokens=900, json_mode=True, temperature=0.4)
            data = json.loads(raw.strip())
            if all(k in data for k in ("ch1", "ch2", "ch3", "ch4", "ch5")):
                total = sum(len(v) for v in data.values())
                print(f"[Script] Master outline: {total} unique facts pre-assigned across 5 chapters")
                return data
            print("[Script] Master outline: incomplete keys — proceeding without outline")
        except Exception as e:
            print(f"[Script] Master outline failed ({e}) — proceeding without outline")
        return {}

    _outline = _generate_master_outline()
    time.sleep(2)  # brief pause before chapter writing begins

    def _facts_block(ch_key: str) -> str:
        """Inject pre-assigned facts as a hard writing directive for one chapter."""
        facts = _outline.get(ch_key, [])
        if not facts:
            return ""
        lines = "\n".join(f"  • {f}" for f in facts)
        return (
            f"📋 YOUR PRE-ASSIGNED FACTS — write ONLY about these specific points "
            f"(they were reserved exclusively for this chapter and appear nowhere else):\n"
            f"{lines}\n"
            "Stay within this list. Do NOT introduce other events or facts from the research."
        )

    section_prompts = [
        # ── Chapter 1: Hook Intro ─────────────────────────────────────────────
        lambda: f"""{_topic_context}
Write CHAPTER 1 — OPENING ATMOSPHERE for a documentary about {name}.

YOUR EXCLUSIVE JOB in this chapter:
1. Open with a SPECIFIC vivid moment from the real story — a year, a place, one person in the middle of an action. Make the viewer see it. Do NOT open with a question or with "You think you know..."
2. In 2–3 sentences: establish the atmosphere, the era, the stakes. What was the world like then?
3. In 1 paragraph: explain what made {series} famous — the one moment that millions remember — then immediately contrast it: "But the real story was never that simple."
4. End with ONE sharp unanswered question that the rest of the video will resolve.

{_facts_block("ch1")}

STRICT SCOPE — this chapter does NOT:
- Cover the real history or biography in detail (that is Chapter 3)
- Reveal the hidden truth or untold angle (that is Chapter 2)
- Make show-vs-reality comparisons (that is Chapter 4)
Set the mood and plant the hook. Nothing more.

NARRATION RULES:
- First sentence: a specific scene, not a statement. Put the viewer in the moment.
- No generic openers: "In the world of crime...", "This is the story of...", "One man..."
- Short punchy sentences to open, longer narrative sentences to build atmosphere.

Write flowing documentary narration — no lists, no bullet points, paragraphs only. Minimum 3 sentences per paragraph. Always complete sentences.
{_section_instruction(300, 380, False)}""",

        # ── Chapter 2: Untold Angle ───────────────────────────────────────────
        lambda: f"""{_topic_context}
Write CHAPTER 2 — THE UNTOLD ANGLE for a documentary about {name}.

YOUR EXCLUSIVE JOB in this chapter:
Build the ENTIRE chapter around this single hidden truth — the one thing {series} never showed:

ANGLE TITLE: {_angle_title}
ANGLE HOOK — open the chapter with EXACTLY this sentence: {_angle_hook}
ANGLE DETAIL — expand ONLY these 2–3 sentences into the full chapter: {_angle_content}

{_facts_block("ch2")}

STRICT SCOPE — this chapter does NOT:
- Re-introduce the show or describe what it depicted (Chapter 1 did that)
- Cover the general real history or biography (Chapter 3 does that)
- Compare show scenes to real events (Chapter 4 does that)
Every sentence must add NEW specific information about this ONE hidden truth only.

{_used_facts_block(1)}

Open with the ANGLE HOOK sentence exactly as written. Then expand with specific names, dates, and decisions — never vague. Minimum 3 sentences per paragraph.
{_section_instruction(350, 420, False)}

PREVIOUS CHAPTER (context only — do NOT repeat anything from it):
{sections[0]}""",

        # ── Chapter 3: The Real Story ─────────────────────────────────────────
        lambda: f"""{_topic_context}{_ch3_mandatory}
Write CHAPTER 3 — THE REAL STORY for a documentary about {name}.

YOUR EXCLUSIVE JOB in this chapter:
Deliver the full documented history in chronological order. This is the FIRST TIME viewers hear the complete real biography and timeline — not summaries, the full story.

{_facts_block("ch3")}

WHAT THIS CHAPTER MUST COVER (and ONLY this chapter covers):
- Who each real person was before everything began: family, background, first crime
- The key events in documented chronological order with exact years
- Real victims, real locations, real consequences
- Every named person in the research gets their own dedicated paragraph

STRICT SCOPE — this chapter does NOT:
- Re-describe what the show depicted (Chapter 1 did that)
- Re-state the hidden angle from Chapter 2 (already covered)
- Make show vs reality comparisons (Chapter 4 does that)

{_used_facts_block(2)}

Write flowing documentary narration — no lists, no bullet points. Minimum 3 sentences per paragraph. Always complete sentences.
{_section_instruction(420, 560, False)}

PREVIOUS CHAPTERS (context only — do NOT repeat anything from them):
{sections[0]}

{sections[1]}""",

        # ── Chapter 4: Show vs Reality ────────────────────────────────────────
        lambda: f"""{_topic_context}
Write CHAPTER 4 — SHOW VS REALITY for a documentary about {name}.

YOUR EXCLUSIVE JOB in this chapter:
Make direct comparisons between what {series} depicted and what the documented record shows.
This is the ONLY chapter that compares screen to reality — do it thoroughly.

{_facts_block("ch4")}

REQUIRED STRUCTURE:

PART A — start with EXACTLY: "Here is what {series} got RIGHT:"
Cover 3 or more specific things the show accurately depicted — reference specific scenes, episodes, or character decisions by name.

PART B — start with EXACTLY: "Here is what they completely changed or left out:"
Cover 3 or more specific things — invented scenes, erased characters, compressed timelines, reversed facts. Be precise: name the specific change and what actually happened.

STRICT SCOPE — this chapter does NOT:
- Re-tell the real history (Chapter 3 already did that)
- Re-introduce people who were fully covered in Chapter 3
- Re-state the untold angle from Chapter 2
Every comparison must reference NEW specific details not yet stated in Chapters 1, 2, or 3.

{_used_facts_block(3)}

Write flowing documentary narration — minimum 3 sentences per paragraph.
{_section_instruction(350, 420, False)}

PREVIOUS CHAPTERS (context only — do NOT repeat anything from them):
{sections[0]}

{sections[1]}

{sections[2]}""",

        # ── Chapter 5: Conclusion ─────────────────────────────────────────────
        lambda: f"""{_topic_context}
Write CHAPTER 5 — FINAL INSIGHT for a documentary about {name}.

YOUR EXCLUSIVE JOB in this chapter:
1. Open with ONE final fact from your pre-assigned list below — something that has NOT appeared anywhere in Chapters 1–4. Make it land hard: one or two short sentences, then silence.
2. In 2–3 sentences: what does the story of {name} reveal about the world — about power, about crime, about the gap between what we are shown and what is real?
3. In 1 sentence: what changed because of this story? What is different today?
4. Close with: "Follow Dark Crime Decoded for more real stories behind your favourite crime series and films."

{_facts_block("ch5")}

STRICT SCOPE — this chapter does NOT:
- Recap or summarize Chapters 1–4
- Repeat ANY fact from earlier chapters (see fence below)
- Use words like "In conclusion", "To summarize", "As we have seen"
This chapter delivers a final truth and lets it echo. It does not wrap things up neatly.

NARRATION RULES:
- The final fact should be delivered like a verdict — short, direct, no softening.
- The insight paragraph should feel like the narrator is speaking directly to the viewer.
- The closing sentence should have weight. Not a tagline — a thought that stays with the viewer.

{_used_facts_block(4)}

CRITICAL: End with a fully complete sentence. Never end mid-thought.
Write flowing documentary narration — no lists, no bullet points.
{_section_instruction(200, 260, True)}

PREVIOUS CHAPTERS (context only — do NOT repeat anything from them):
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
    if total_real > LONG_SCRIPT_MAX_WORDS:
        full_script = _cap_script_max_words(full_script, LONG_SCRIPT_MAX_WORDS)
        total_real = clean_word_count(full_script)
    total_raw  = len(full_script.split())
    minutes    = total_real / 163  # ~163 wpm for documentary English narration
    print(f"[Script] Total English: {total_real} real words (raw {total_raw}) "
          f"→ Est. runtime: ~{minutes:.0f} min")

    if total_real < LONG_SCRIPT_MIN_WORDS:
        print(f"[Script] WARNING: English total {total_real} real words — below {LONG_SCRIPT_MIN_WORDS:,} target, may be short")
    elif total_real > LONG_SCRIPT_MAX_WORDS:
        print(f"[Script] WARNING: English total {total_real} real words — above {LONG_SCRIPT_MAX_WORDS:,} cap, may run long")

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
    _si_long = get_series_for_person(topic["topic"])
    _angle   = get_script_angle(topic["topic"], _si_long)

    # Documentary-only topics: use investigative prompt, skip series comparison, early return
    if _angle == "documentary":
        user_note    = research.get("user_discovery", "") or topic.get("user_note", "")
        part_number  = detect_part_number(user_note)
        print(f"[Script] Documentary angle detected for: {topic['topic']} (part={part_number})")

        _raw_doc         = _write_documentary_script(topic, research, part_number)
        _raw_doc         = check_hallucination(_raw_doc)
        _raw_doc         = fix_first_mention(_raw_doc, is_arabic=False)
        script_text      = validate_script(_raw_doc)
        _series_name_raw = _si_long[0] if _si_long else topic.get("niche", topic["topic"])
        _series_type_raw = "Documentary"

        # Hemedti-specific title overrides
        _topic_lower = topic["topic"].lower()
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
            # Generic documentary title with optional part label
            part_suffix = f" — Part {part_number}" if part_number else ""
            doc_title = (
                f"The Untold Story of {topic['topic']}{part_suffix}: "
                f"What The World Needs To Know | Dark Crime Decoded"
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
            "chapters":        generate_chapters(clean_word_count(script_text)),
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
        _rvf_fb_block += "REAL PEOPLE (cover ALL of them):\n" + "\n".join(_rp_lines) + "\n"
    if _rvf_fb.get("fictional_characters"):
        _fc_lines = [f"  - {c['name']} (played by {c.get('played_by','?')}) → real person: {c.get('based_on','?')}" for c in _rvf_fb["fictional_characters"][:6]]
        _rvf_fb_block += "FICTIONAL→REAL CHARACTER MAP:\n" + "\n".join(_fc_lines) + "\n"
    if _rvf_fb.get("real_vs_show"):
        _rvs_lines = [f"  - {r['aspect']}: reality='{r.get('reality','')}' vs show='{r.get('show','')}'" for r in _rvf_fb["real_vs_show"][:4]]
        _rvf_fb_block += "SHOW VS REALITY (use at least one of these):\n" + "\n".join(_rvs_lines) + "\n"

    # Inject show_characters (populated by research_agent STEP 0)
    _sc_fb = research.get("show_characters") or []
    _mandatory_fb = ""
    if _sc_fb:
        sc_lines_fb = [
            f"  - {c['character']} ({c.get('actor','?')}) → {c.get('based_on','?')}: {c.get('real_role','')}"
            for c in _sc_fb
        ]
        _rvf_fb_block += "SHOW CAST — cover EVERY character:\n" + "\n".join(sc_lines_fb) + "\n"
        char_names_fb = ", ".join(c['character'] for c in _sc_fb)
        real_names_fb = ", ".join(c.get('based_on','?') for c in _sc_fb if c.get('based_on','').lower() not in ('null','none','various',''))
        _mandatory_fb = (
            f"\nMANDATORY — THIS VIDEO IS ABOUT A TV SHOW BASED ON TRUE EVENTS:\n"
            f"You MUST cover ALL {len(_sc_fb)} main characters: {char_names_fb}\n"
            f"AND their real counterparts: {real_names_fb}\n"
            f"Give each character at least one full paragraph. Show BOTH worlds — the show and reality.\n"
            f"Include what the show got right vs what really happened.\n"
        )

    part1_prompt = f"""You are a top true crime documentary writer for YouTube.
Write a 1450-1900 word 10-14 minute documentary script about: {topic['topic']}
The related series/movie is: {series_label}

NARRATION STYLE: Write like Morgan Freeman narrating a documentary. Flowing paragraphs, no lists, no bullet points. Minimum 3 sentences per paragraph. Use transition phrases like "But what happened next shocked everyone...", "What nobody knew at the time was...", "Years later, the truth finally emerged..."

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

HOOK (100 words = ~46 seconds):
- Most fascinating single fact about this real story
- Something that makes the viewer want to know more
- Example: "{series_label} introduced millions of people to this incredible true story. But the real events were even more extraordinary than anything the show could portray."

SERIES INTRO (220 words = ~1.4 minutes):
- Celebrate what {series_label} showed the world — it is great television
- Why millions of people loved it and why it matters
- Build excitement: the real story that inspired it is even more incredible
- Name {series_label} directly and what made it famous

REAL BACKGROUND (320 words = ~2.1 minutes):
- Real person's early life with specific facts
- Family, childhood, origins — real dates, real places, real names
- The fascinating true events BEFORE the series timeline begins

MAIN STORY (520 words = ~3.3 minutes):
- Full chronological real story
- Key events the series captured — what {series_label} got RIGHT with evidence
- How history inspired {series_label} and why filmmakers made their creative choices
- Real quotes from people involved
- Specific dates and facts throughout

SHOCKING REVELATIONS (220 words = ~1.4 minutes):
- 3-4 fascinating real facts that make the true story even more incredible than {series_label}
- Remarkable real details the show's runtime couldn't fully capture
- Things that would amaze even the biggest fans of the show
- Real impact on real people and real history

REAL STORY VS SCREEN STORY (80 words = ~0.5 minutes):
ONLY write a comparison if you have a VERIFIED, SPECIFIC difference with different facts or numbers.
Format: "In {series_label}, they showed X. In reality, Y happened."
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

TOTAL TARGET: 1450 words minimum, 1900 words maximum.
SECTION TOTALS: 100+220+320+520+220+80+120 = 1580 words = ~10-12 minutes at 150-160 wpm.

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

    # Primary: 5-call split targeting 2,500–3,050 real words
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

    # Final hard cap for YouTube-safe runtime in draft/publish workflows.
    script_text = _cap_script_max_words(script_text, LONG_SCRIPT_MAX_WORDS)

    # ── PART 2: Generate metadata only (title, hook, captions, etc.) ────────
    _series_info    = get_series_for_person(topic["topic"])
    _related_series = f"{_series_info[0]} {_series_info[1]}" if _series_info else series
    part2_prompt = f"""You are a content packaging assistant.
Based on this voiceover script about "{topic['topic']}", generate the metadata fields.

TITLE FORMAT (mandatory):
Use the untold angle as the title hook: "{_angle_data.get('angle_title', topic['topic'])} | Dark Crime Decoded"
Examples of good angle-based titles:
"The Interview That Broke John Douglas | Dark Crime Decoded"
"The Woman Netflix Erased From Mindhunter | Dark Crime Decoded"
"The Confession That Should Never Have Happened | Dark Crime Decoded"
"The Real Pablo Escobar Was Even Darker Than Narcos | Dark Crime Decoded"
The real person for this topic is: {topic['topic']}
The related series/movie is: {_related_series}
The untold angle for this video is: {_angle_data.get('angle_title', '')}
TONE: Gripping and revelatory. The title teases the hidden truth.
Max 90 chars total.

Return ONLY this JSON with no extra text:
{{
  "title": "Dark Crime Decoded: [Real Person] & [Movie/Series Type] — [hook]",
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

    meta = json.loads(_ai_script_call(part2_prompt, max_tokens=1000, temperature=0.3, json_mode=True).strip())
    _series_name = _series_info[0] if _series_info else _related_series
    _fallback_title = (
        f"The Real Story Behind {_series_name}: {topic['topic']}'s True Life | Dark Crime Decoded"
        if _series_info else f"The True Story of {topic['topic']}: The Real Events Behind The Legend | Dark Crime Decoded"
    )
    script_data = {
        "title":          meta.get("title", _fallback_title),
        "hook":           meta.get("hook", ""),
        "script":         script_text,
        "on_screen_texts": meta.get("on_screen_texts", []),
        "caption":        meta.get("caption", ""),
        "hashtags":       _build_darkcrimed_hashtags(meta.get("hashtags", ""), _series_info),
        "thumbnail_text": meta.get("thumbnail_text", ""),
        "chapters":       generate_chapters(
            clean_word_count(script_text),
            angle_title=_angle_data.get("angle_title", ""),
        ),
    }
    script_data["topic"]              = topic["topic"]
    script_data["niche"]              = topic["niche"]
    script_data["search_query"]       = topic["search_query"]
    script_data["keywords"]           = topic["keywords"]
    script_data["language"]           = "english"
    script_data["series_name"]        = _series_name_raw
    script_data["series_type"]        = _series_type_raw
    script_data["angle_title"]        = _angle_data.get("angle_title", "")
    script_data["angle_hook"]         = _angle_data.get("angle_hook", "")
    # Carry discovery fields so Telegram preview can show them
    script_data["user_discovery"]          = user_discovery
    script_data["user_discovery_expanded"] = discovery_expanded
    # Carry show_characters forward so write_short_script can use them
    script_data["show_characters"]         = research.get("show_characters", [])
    _s = script_data["script"]
    _s = pick_best_hook(_s)
    _s = evaluate_and_fix_script(_s)
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


def _fix_arabic(text: str) -> str:
    """Apply all Arabic post-processing fixes in one call."""
    text = fix_arabic_prison_terms(text)
    text = fix_arabic_cta(text)
    text = fix_arabic_rsf(text)
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
    return resp.choices[0].message.content.strip()


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

        # Cleanup pass: Groq → OpenAI → as-is
        if clean_word_count(part) > 20:
            try:
                cleaned = _groq_clean_arabic(part)
            except Exception:
                try:
                    cleaned = _clean_arabic_with_openai(part)
                except Exception:
                    cleaned = part
        else:
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
    response = _requests.get(url, params=params)
    response.raise_for_status()
    result     = response.json()
    translated = "".join([item[0] for item in result[0]])
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
    return _fix_arabic(resp.choices[0].message.content.strip())


def try_translate_arabic(text: str, topic: str = "") -> str:
    """Translate to Arabic: OpenAI → Groq → Google. Accept first success, never retry twice."""
    # 1. OpenAI (primary — best quality)
    try:
        result = translate_to_arabic_openai(text, topic=topic)
        if result:
            return result
    except Exception as e:
        print(f"[Script] OpenAI translation unavailable: {e}")

    # 2. Groq (fallback — faster, no per-word ratio enforcement)
    try:
        result = _groq_translate_arabic(text, topic=topic)
        if result:
            print("[Script] Arabic translation via Groq ✅")
            return result
    except Exception as e:
        print(f"[Script] Groq translation failed: {e}")

    # 3. Google Translate (final fallback)
    try:
        result = translate_to_arabic_google(text)
        if result:
            print("[Script] Arabic translation via Google ✅")
            return result
    except Exception as e:
        print(f"[Script] Google translation failed: {e}")

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
                                "You are a professional Arabic translator specialising in "
                                "true crime and investigative documentary narration. "
                                "Translate into natural spoken Modern Standard Arabic — "
                                "not formal Classical Arabic. "
                                "When an English sentence is long, break it into two shorter "
                                "Arabic sentences for better spoken rhythm. "
                                "Short punchy English sentences must stay short in Arabic. "
                                "Adapt idioms and sarcasm naturally — never translate them literally. "
                                "Preserve every fact, name, and date — never summarise or skip content. "
                                "Maintain dramatic tension: if the English builds suspense, "
                                "the Arabic must build the same suspense."
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


def translate_script(en_script: dict) -> dict:
    """Translate an English script_data dict into Arabic with stable section markers."""
    _topic_lower = en_script.get("topic", "").lower()
    _is_hemedti  = any(k in _topic_lower for k in ["hemedti", "حميدتي", "dagalo"])

    if _is_hemedti:
        ar_title = _build_hemedti_arabic_title(en_script.get("part_number"))
    else:
        ar_title = _build_arabic_title(
            en_script.get("title", ""),
            en_script.get("series_name"),
            en_script.get("series_type"),
        )

    ar_data = {
        "title":          ar_title,
        "hook":           translate_to_arabic(en_script.get("hook", "")),
        "script":         fix_first_mention(_translate_script_preserve_sections(en_script["script"]), is_arabic=True),
        "on_screen_texts": [translate_to_arabic(t) for t in en_script["on_screen_texts"]],
        "caption":        translate_to_arabic(en_script["caption"]),
        "hashtags":       translate_to_arabic(en_script["hashtags"]),
        "thumbnail_text": translate_to_arabic(en_script["thumbnail_text"]),
        "chapters":       en_script.get("chapters", ""),  # keep English timestamps
    }
    ar_data["topic"]        = en_script["topic"]
    ar_data["niche"]        = en_script["niche"]
    ar_data["search_query"] = en_script["search_query"]
    ar_data["keywords"]     = en_script["keywords"]
    ar_data["language"]     = "arabic"
    ar_data["series_name"]  = en_script.get("series_name", "")
    ar_data["series_type"]  = en_script.get("series_type", "")
    if ar_data.get("script"):
        ar_data["script"] = evaluate_and_fix_script(ar_data["script"])
    print(f"[Script] Translated (arabic): '{ar_data['title']}'")
    return ar_data


_SHORT_SCRIPT_SYSTEM = """You are writing spoken narration for viral short-form video (YouTube Shorts / TikTok / Instagram Reels).

VOICE TONE: Calm. Investigative. Slightly unsettling. The narrator knows more than they are saying.
NOT: academic, formal, excited, or sensationalist.

SENTENCES: Short and varied. Mix punchy 5-word lines with 15-word builds. Never over 22 words per sentence.
PARAGRAPHS: Maximum 3 sentences. No walls of text.
FLOW: Every sentence must pull the listener forward to the next one.

HOOK: The first 2 sentences must contain a contradiction, hidden truth, or shocking omission. Never open with context or background.
FACTS: Lead with the most controversial or psychologically revealing detail. Skip generic background.
ENDING: The last 1-2 sentences must leave impact — a disturbing implication, an open question, or a fact that reframes everything heard before.

BANNED WORDS: "Furthermore", "In conclusion", "As we can see", "It is important to note", "Throughout history", "In summary".
BANNED FORMAT: Any headings, labels, or section markers in the output (no "HOOK:", "SETUP:", "REVEAL:", etc.)."""


def write_short_script(en_long_script: dict) -> dict:
    """Extract the strongest moment from the long script and rewrite it as a 45-90 second viral short."""
    topic       = en_long_script.get("topic", "")
    long_script = en_long_script.get("script", "")
    _angle_hook  = en_long_script.get("angle_hook", "")
    _angle_title = en_long_script.get("angle_title", "")

    _hook_instruction = (
        f"Open with EXACTLY this sentence: \"{_angle_hook}\"\n"
        if _angle_hook else
        "Open with the most shocking fact or unanswered question from the story. No setup. Drop straight in.\n"
    )

    prompt = f"""You are writing a spoken voiceover for a 45-90 second crime documentary short video.

TASK: Read the script below. Find the single most gripping moment — a shocking reveal, confession, twist, or hidden truth. Rewrite it as a standalone viral voiceover. Do NOT summarize the whole story. Tell one moment, fast and hard.

Topic: {topic}
{f"Angle: {_angle_title}" if _angle_title else ""}

{_hook_instruction}
FLOW (4 beats — write them as continuous prose, NO headings or labels):
- Beat 1 — HOOK: 2 sentences. Jump straight into the action or fact. No "In [year]...", no "This is the story of...", no setup.
- Beat 2 — FAST SETUP: 2 sentences. Who was involved? What was at stake? Keep it tight.
- Beat 3 — MAIN REVEAL: 3-4 sentences. The truth, the twist, the thing that changes everything.
- Beat 4 — STRONG ENDING: 1-2 sentences. A line that lingers. End with: "Follow Dark Crime Decoded for more."

STYLE:
- Short sentences mixed with medium ones. Never over 22 words.
- Conversational — like you're telling someone something they cannot believe.
- No formal transitions. No long paragraphs. No academic tone.
- No ellipsis (...). No mid-sentence dashes. No parentheses.

LENGTH: Exactly 120–180 words. Count every word.

SOURCE SCRIPT (find the best moment inside):
{long_script[:1800]}

Write ONLY the spoken words. No headings. No labels. No explanations."""

    script_text = ""
    for attempt in range(2):
        _p = prompt
        if attempt > 0:
            _p += f"\n\nPREVIOUS ATTEMPT: {clean_word_count(script_text)} words — need 120-180. {'Expand the reveal with more specific detail.' if clean_word_count(script_text) < 120 else 'Cut filler sentences to hit the target.'}"
        script_text = _ai_script_call(_p, max_tokens=450, temperature=0.85,
                                       system_prompt=_SHORT_SCRIPT_SYSTEM).strip()
        words   = clean_word_count(script_text)
        seconds = round(words / 2.5)
        print(f"[Script] Short attempt {attempt + 1}: {words} words = ~{seconds}s")
        if words >= 120:
            break
        print(f"[Script] Short too short ({words} words) — retrying...")

    if clean_word_count(script_text) > 190:
        script_text = _trim_plain_text_to_words(script_text, 180)
        print("[Script] Short trimmed to 180 words")

    script_text = evaluate_and_fix_script(script_text)
    ar_script_text = translate_to_arabic(script_text) if script_text else ""

    short_data = {
        "title":            en_long_script.get("title", ""),
        "hook":             en_long_script.get("hook", script_text[:100]),
        "script":           script_text,
        "short_script_en":  script_text,
        "short_script_ar":  ar_script_text,
        "on_screen_texts":  en_long_script.get("on_screen_texts", [])[:2],
        "caption":          en_long_script["caption"],
        "hashtags":         en_long_script["hashtags"],
        "thumbnail_text":   en_long_script["thumbnail_text"],
        "topic":            en_long_script["topic"],
        "niche":            en_long_script["niche"],
        "search_query":     en_long_script["search_query"],
        "keywords":         en_long_script["keywords"],
        "language":         "english",
    }
    _short_title = add_short_title(short_data)
    short_data["title"]       = _short_title
    short_data["short_title"] = _short_title
    if short_data.get("script"):
        short_data["script"] = evaluate_and_fix_script(short_data["script"])
    print(f"[Script] Short script done: '{short_data['title']}' ({clean_word_count(script_text)} words EN, {clean_word_count(ar_script_text)} words AR)")
    return short_data


def write_scripts(topics: list[dict]) -> list[dict]:
    """Write English script then translate to Arabic for each topic."""
    scripts = []
    for topic in topics:
        en_script = write_script(topic, language="english")   # YouTube
        ar_script = translate_script(en_script)                # TikTok + X
        scripts.append(ar_script)
        scripts.append(en_script)
    return scripts
