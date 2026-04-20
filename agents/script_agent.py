# ============================================================
#  agents/script_agent.py  —  Writes bilingual video scripts
#  English for YouTube, Arabic is a direct translation
# ============================================================
import json
import os
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


def _openai_direct_call(prompt: str, max_tokens: int = 4000,
                        json_mode: bool = False,
                        system_prompt: str | None = None) -> str | None:
    import os
    import requests
    import json

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("[OpenAI] No API key")
        return None

    _system = system_prompt or "You are a professional true crime documentary scriptwriter."

    print("=== SYSTEM PROMPT ===")
    print(_system)
    print("=== END SYSTEM PROMPT ===")

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": _system,
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "python-requests/2.31.0",
    }

    # Attempt 1: Standard requests
    try:
        print("[OpenAI] Attempt 1: standard requests")
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
            verify=True,
        )
        print(f"[OpenAI] Status: {r.status_code}")
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        else:
            print(f"[OpenAI] Error: {r.text[:200]}")
    except Exception as e:
        print(f"[OpenAI] Attempt 1 failed: {e}")

    # Attempt 2: No SSL verification
    try:
        print("[OpenAI] Attempt 2: no SSL verify")
        import urllib3
        urllib3.disable_warnings()
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
            verify=False,
        )
        print(f"[OpenAI] Status: {r.status_code}")
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OpenAI] Attempt 2 failed: {e}")

    # Attempt 3: urllib with custom SSL context
    try:
        print("[OpenAI] Attempt 3: urllib")
        import urllib.request
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=data,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            result = json.loads(resp.read().decode())
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OpenAI] Attempt 3 failed: {e}")

    print("[OpenAI] All connection attempts failed")
    return None


_SCRIPT_SYSTEM_PROMPT = """You are a professional true crime documentary scriptwriter.

Tone rules:
- 85% dark, serious, documentary tone
- 15% dry humor and sarcasm — especially when describing:
  * How stupid a criminal's mistake was
  * Ironic twists in the story
  * Moments where the subject embarrassed themselves
  * Unexpected plot twists

Examples of good dry humor in crime scripts:
- 'He planned the perfect crime. Except he left his wallet at the scene.'
- 'For a man who controlled an entire militia, he somehow forgot that cameras exist.'
- 'Genius move. Truly. A masterclass in how not to be a warlord.'

Rules:
- Never make fun of victims
- Only humor directed at criminals, corrupt officials, or ironic situations
- One or two lines max per section — don't overdo it"""


def _groq_fallback(prompt: str, max_tokens: int, json_mode: bool) -> str:
    """Groq fallback with aggressive prompt truncation and 3-model retry chain."""
    import os
    import time

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key:
        print("[Script] No Groq key available")
        return ""

    from groq import Groq
    groq_client = Groq(api_key=groq_key)

    # Keep beginning + end to preserve context within 3000-char limit
    max_chars = 3000
    if len(prompt) > max_chars:
        half   = max_chars // 2
        prompt = prompt[:half] + "\n...\n" + prompt[-half:]
        print(f"[Script] Prompt truncated to {max_chars} chars for Groq")

    for model, model_max in [
        ("llama-3.3-70b-versatile", 2000),
        ("llama-3.1-8b-instant",    1000),
    ]:
        try:
            resp = groq_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=min(max_tokens, model_max),
                temperature=0.7,
            )
            print(f"[Script] Groq {model} success ✅")
            return resp.choices[0].message.content
        except Exception as e:
            print(f"[Script] Groq {model} failed: {e}")
            time.sleep(5)

    return ""


def _ai_script_call(prompt: str, max_tokens: int = 1000,
                    json_mode: bool = False, temperature: float = 0.7) -> str:
    """OpenAI gpt-4o-mini first (requests-based), fall back to Groq."""
    result = _openai_direct_call(prompt, max_tokens=max_tokens, json_mode=json_mode)
    if result:
        return result
    return _groq_fallback(prompt, max_tokens, json_mode)


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


def _build_arabic_title(en_title: str, series_name: str | None, series_type: str | None) -> str:
    """Return Arabic title with فيلم/مسلسل label, falling back to Google Translate."""
    ar_entry = SERIES_ARABIC.get(series_name or "")
    if ar_entry:
        ar_series, ar_type = ar_entry
        return f"القصة الحقيقية وراء {ar_type} {ar_series} | Dark Crime Decoded"
    # No dict entry — use type word with original English series name
    if series_name:
        ar_type = "فيلم" if series_type == "Movie" else "مسلسل" if series_type == "Series" else ""
        if ar_type:
            return f"القصة الحقيقية وراء {ar_type} {series_name} | Dark Crime Decoded"
    return translate_to_arabic(en_title)


def generate_chapters(script, total_duration_seconds=LONG_VIDEO_DURATION):
    """Return YouTube chapter timestamps for an 18-minute documentary."""
    chapters = [
        (0,    "🎬 Introduction"),
        (90,   "📺 What The Series Showed"),
        (210,  "🔍 The Real Background"),
        (420,  "😱 The Real Story"),
        (780,  "💀 Shocking Facts"),
        (960,  "⚖️ Real Story vs Screen"),
        (1050, "🎯 Conclusion"),
    ]
    chapter_text = ""
    for seconds, title in chapters:
        mins = seconds // 60
        secs = seconds % 60
        chapter_text += f"{mins:02d}:{secs:02d} {title}\n"
    return chapter_text


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

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75)
    words = len(result.split()) if result else 0
    print(f"[Script] Hemedti Part 1: {words} words")
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

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75)
    words = len(result.split()) if result else 0
    print(f"[Script] Hemedti Part 2: {words} words")
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

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75)
    words = len(result.split()) if result else 0
    print(f"[Script] Documentary script{part_label}: {words} words")
    return result or ""


def write_long_script_split(topic: dict, research: dict, series_info: tuple | None) -> str:
    """Write 4100–5500 word script via 5 separate OpenAI calls (one section each)."""
    import time

    series = series_info[0] if series_info else topic.get("niche", topic.get("topic", ""))
    stype  = series_info[1] if series_info else "Movie"
    name   = topic.get("topic", "")

    base_context = f"""Topic: {name}
Series/Movie: {series} ({stype})
Real person: {research.get('real_person', name)}
Key facts: {(research.get('research_facts') or research.get('what_show_got_right', []))[:3]}
"""

    _no_truncate = """
You must write between 800–1400 words for this section.
Do not summarize. Do not truncate. Write in full detail.
If you are approaching the end, do not conclude — the next section will continue the story."""

    _no_truncate_conclusion = """
You must write between 500–700 words for this section.
Do not summarize. Do not truncate. Write in full detail."""

    sections: list[str] = []

    # CALL 1 — Hook + Intro (800–1000 words)
    prompt1 = f"""{base_context}
Write the hook and introduction for a crime documentary script about {name}.
Hook must grab attention immediately. Intro must build suspense.
Write 800–1000 words. Do not conclude. End mid-story.
{_no_truncate}"""

    s1 = _openai_direct_call(prompt1, max_tokens=2000, system_prompt=_SCRIPT_SYSTEM_PROMPT)
    if not s1:
        print("[Script] 5-call split: call 1 failed")
        return ""
    sections.append(s1)
    print(f"[Script] Section 1 (Hook+Intro): {len(s1.split())} words")
    time.sleep(3)

    # CALL 2 — Background & Context (900–1200 words)
    prompt2 = f"""{base_context}
Continue the script. Write the background and context section.
Cover history, key players, timeline of events.
Write 900–1200 words. Do not conclude. End mid-story.
{_no_truncate}

PREVIOUS SECTION:
{s1}"""

    s2 = _openai_direct_call(prompt2, max_tokens=2000, system_prompt=_SCRIPT_SYSTEM_PROMPT)
    if not s2:
        print("[Script] 5-call split: call 2 failed")
        return ""
    sections.append(s2)
    print(f"[Script] Section 2 (Background): {len(s2.split())} words")
    time.sleep(3)

    # CALL 3 — Main Events Deep Dive (1000–1400 words)
    prompt3 = f"""{base_context}
Continue the script. Write the main events section in full detail.
Every key moment, dialogue, scene description.
Write 1000–1400 words. Do not conclude.
{_no_truncate}

PREVIOUS SECTIONS:
{s1}

{s2}"""

    s3 = _openai_direct_call(prompt3, max_tokens=2000, system_prompt=_SCRIPT_SYSTEM_PROMPT)
    if not s3:
        print("[Script] 5-call split: call 3 failed")
        return ""
    sections.append(s3)
    print(f"[Script] Section 3 (Main Events): {len(s3.split())} words")
    time.sleep(3)

    # CALL 4 — Analysis & Aftermath (900–1200 words)
    prompt4 = f"""{base_context}
Continue the script. Write the analysis and aftermath section.
What happened next, investigations, consequences, expert opinions.
Write 900–1200 words. Do not conclude.
{_no_truncate}

PREVIOUS SECTIONS:
{s1}

{s2}

{s3}"""

    s4 = _openai_direct_call(prompt4, max_tokens=2000, system_prompt=_SCRIPT_SYSTEM_PROMPT)
    if not s4:
        print("[Script] 5-call split: call 4 failed")
        return ""
    sections.append(s4)
    print(f"[Script] Section 4 (Analysis): {len(s4.split())} words")
    time.sleep(3)

    # CALL 5 — Conclusion (500–700 words)
    prompt5 = f"""{base_context}
Continue the script. Write the final conclusion.
Wrap up the story, final thoughts, call to action for viewers.
Write 500–700 words. This is the final section.
{_no_truncate_conclusion}

PREVIOUS SECTIONS:
{s1}

{s2}

{s3}

{s4}"""

    s5 = _openai_direct_call(prompt5, max_tokens=2000, system_prompt=_SCRIPT_SYSTEM_PROMPT)
    if not s5:
        print("[Script] 5-call split: call 5 failed")
        return ""
    sections.append(s5)
    print(f"[Script] Section 5 (Conclusion): {len(s5.split())} words")

    _SECTION_LABELS = [
        "[SECTION: Introduction]",
        "[SECTION: Background]",
        "[SECTION: Main Story]",
        "[SECTION: Shocking Facts]",
        "[SECTION: Conclusion]",
    ]
    full_script = "\n\n".join(
        f"{_SECTION_LABELS[i]}\n{section}"
        for i, section in enumerate(sections)
    )
    total_words = len(full_script.split())
    minutes     = total_words / 130
    print(f"[Script] 5-call split total: {total_words} words = ~{minutes:.1f} minutes ✅")

    if total_words < 3500:
        print(f"[Script] WARNING: English script below 3500 words ({total_words}) — consider regenerating")

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
            "chapters":        generate_chapters(script_text),
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

    part1_prompt = f"""You are a top true crime documentary writer for YouTube.
Write a 2000-2500 word 16-18 minute documentary script about: {topic['topic']}
The related series/movie is: {series_label}

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

SERIES INTRO (300 words = ~2.3 minutes):
- Celebrate what {series_label} showed the world — it is great television
- Why millions of people loved it and why it matters
- Build excitement: the real story that inspired it is even more incredible
- Name {series_label} directly and what made it famous

REAL BACKGROUND (500 words = ~3.8 minutes):
- Real person's early life with specific facts
- Family, childhood, origins — real dates, real places, real names
- The fascinating true events BEFORE the series timeline begins

MAIN STORY (800 words = ~6.2 minutes):
- Full chronological real story
- Key events the series captured — what {series_label} got RIGHT with evidence
- How history inspired {series_label} and why filmmakers made their creative choices
- Real quotes from people involved
- Specific dates and facts throughout

SHOCKING REVELATIONS (400 words = ~3.1 minutes):
- 3-4 fascinating real facts that make the true story even more incredible than {series_label}
- Remarkable real details the show's runtime couldn't fully capture
- Things that would amaze even the biggest fans of the show
- Real impact on real people and real history

REAL STORY VS SCREEN STORY (250 words = ~1.9 minutes):
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

CONCLUSION (150 words = ~1.2 minutes):
- What happened after the events {series_label} depicted
- Where the real people are now
- One question to tease the next video
- End with: "Follow Dark Crime Decoded for more real stories behind your favourite crime series"

TOTAL TARGET: 2000 words minimum, 2500 words maximum.
SECTION TOTALS: 100+300+500+800+400+250+150 = 2500 words = ~19.2 minutes at 130 wpm.

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

    # Primary: 5-call split targeting 3600–5000 words
    script_text = write_long_script_split(topic, research, _si_long)
    if script_text and len(script_text.split()) >= 3000:
        script_text = validate_script(script_text)
        print(f"[Script] ✅ Split method OK: {len(script_text.split())} words")
    else:
        if script_text:
            print(f"[Script] Split too short ({len(script_text.split())} words) — falling back to single call")
        else:
            print("[Script] Split method failed — falling back to single call")
        script_text = ""
        for attempt in range(2):
            _prompt = part1_prompt
            if attempt > 0:
                _prompt += f"""

CRITICAL: Previous attempt was only {len(script_text.split())} words. MINIMUM REQUIRED: 1200 words.
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
            words   = len(script_text.split())
            minutes = words / 130
            print(f"[Script] Attempt {attempt + 1}: {words} words = ~{minutes:.1f} minutes")
            if words >= 1200:
                print(f"[Script] ✅ Length OK: {words} words")
                break
            print(f"[Script] WARNING: Too short ({words} words) — retrying...")

    # ── PART 2: Generate metadata only (title, hook, captions, etc.) ────────
    _series_info    = get_series_for_person(topic["topic"])
    _related_series = f"{_series_info[0]} {_series_info[1]}" if _series_info else series
    part2_prompt = f"""You are a content packaging assistant.
Based on this voiceover script about "{topic['topic']}", generate the metadata fields.

TITLE FORMAT (mandatory):
"The Real Story Behind [Series]: [Real Person]'s True Life | Dark Crime Decoded"
Example: "The Real Story Behind Boardwalk Empire: Al Capone's True Life | Dark Crime Decoded"
Example: "The True Story That Inspired Narcos: Pablo Escobar's Real Life | Dark Crime Decoded"
Example: "Before Breaking Bad: The Real Chemistry Teacher Who Inspired Walter White | Dark Crime Decoded"
Example: "The Real Godfather: The True Story Behind The Greatest Crime Movie | Dark Crime Decoded"
Example: "Who Was The Real Al Capone? The Story Behind Boardwalk Empire | Dark Crime Decoded"
The real person for this topic is extracted from: {topic['topic']}
The related series/movie is: {_related_series}
If no series is known, use: "The True Story of [Real Person]: The Real Events Behind The Legend | Dark Crime Decoded"
TONE: Informative and celebratory — never accusatory. Celebrate both the show and the real story.
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
        "chapters":       generate_chapters(script_text),
    }
    script_data["topic"]              = topic["topic"]
    script_data["niche"]              = topic["niche"]
    script_data["search_query"]       = topic["search_query"]
    script_data["keywords"]           = topic["keywords"]
    script_data["language"]           = "english"
    script_data["series_name"]        = _series_name_raw
    script_data["series_type"]        = _series_type_raw
    # Carry discovery fields so Telegram preview can show them
    script_data["user_discovery"]          = user_discovery
    script_data["user_discovery_expanded"] = discovery_expanded
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
        return section_text

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


def format_for_tts_arabic(text: str) -> str:
    """
    Format Arabic script for natural TTS delivery.
    - OpenAI cleanup pass (fusha, no filler).
    - Each sentence on its own line.
    - Shocking facts / numbers → trailing "..."
    - Short punchy clauses each on own line.
    - Breathing-room blank lines every 2-3 lines.
    """
    import re

    # Section markers go through unchanged; process section bodies separately
    section_marker_re = re.compile(r'(\[SECTION:[^\]]+\])')
    parts = section_marker_re.split(text)
    out_parts: list[str] = []

    for part in parts:
        if section_marker_re.match(part):
            out_parts.append(part)
            continue
        if not part.strip():
            out_parts.append(part)
            continue

        # OpenAI cleanup per section (skip tiny fragments)
        cleaned = _clean_arabic_with_openai(part) if len(part.split()) > 20 else part

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


def translate_to_arabic_openai(english_text: str, topic: str = "") -> str:
    """Translate to Arabic via OpenAI gpt-4o-mini with correct RSF terminology. Falls back to Google."""
    import os as _os
    import requests as _req

    api_key = _os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return translate_to_arabic_google(english_text)

    prompt = f"""Translate this English true crime script to Arabic.

CRITICAL RULES:
1. First mention of RSF: "قوات الدعم السريع (RSF)" — subsequent mentions: "قوات الدعم السريع" or "RSF"
2. RSF is NEVER "مراسلون بلا حدود" — RSF = Rapid Support Forces = قوات الدعم السريع ALWAYS
3. First mention of SAF: "القوات المسلحة السودانية (SAF)"
4. First mention of ICC: "محكمة الجنايات الدولية (ICC)"
5. Keep all proper names in original language (Hemedti, Dagalo, Khartoum, Darfur, etc.)
6. Keep "Dark Crime Decoded" in English
7. Keep series/movie names in English
8. NEVER add content not in the original
9. Maintain the same paragraph structure
10. This is serious investigative journalism — translate formally and accurately

English text to translate:
{english_text}

Return ONLY the Arabic translation. No explanations, no notes."""

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
                            "true crime and investigative journalism. You translate "
                            "accurately with correct military and legal terminology. "
                            "When the source text contains dry humor or sarcasm, adapt "
                            "it naturally into Arabic — do not translate literally. "
                            "Arabic humor should feel native, not like a translated joke."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4000,
                "temperature": 0.3,
            },
            timeout=60,
        )
        if r.status_code == 200:
            result = r.json()["choices"][0]["message"]["content"].strip()
            result = _fix_arabic(result)
            print("[Script] OpenAI Arabic translation ✅")
            return result
    except Exception as e:
        print(f"[Script] OpenAI translation failed: {e}")

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

    total_en = len(english_text.split())
    print(f"[Script] Translating {total_en}-word script in {len(chunks)} chunks")

    translated: list[str] = []
    for i, chunk in enumerate(chunks):
        print(f"[Script] Translating chunk {i + 1}/{len(chunks)}...")
        ar_chunk = translate_to_arabic_openai(chunk, topic=topic)
        translated.append(ar_chunk)
        if i < len(chunks) - 1:
            _time.sleep(2)

    result    = "\n\n".join(translated)
    ar_words  = len(result.split())
    print(f"[Script] Arabic translation total: {ar_words} words")
    if ar_words < 3200:
        print(f"[Script] WARNING: Arabic script below 3200 words ({ar_words}) — consider regenerating")
    return result


def translate_to_arabic(text: str) -> str:
    """Public entry point — chunked for long scripts, otherwise single OpenAI call."""
    if len(text.split()) > 1000:
        return translate_long_script_arabic(text)
    return translate_to_arabic_openai(text)


def _build_hemedti_arabic_title(part_number: int | None) -> str:
    """Return the correct Arabic title for Hemedti videos."""
    if part_number == 1:
        return "حميدتي الجزء الأول: من تاجر الإبل إلى أمير الحرب | فك رموز الجريمة المظلمة"
    if part_number == 2:
        return "حميدتي الجزء الثاني: المجزرة والإمارات والمرتزقة الكولومبيون | فك رموز الجريمة المظلمة"
    return "حميدتي: أخطر رجل لم تسمع عنه | فك رموز الجريمة المظلمة"


def translate_script(en_script: dict) -> dict:
    """Translate an English script_data dict into Arabic using Google Translate."""
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
        "script":         fix_first_mention(translate_to_arabic(en_script["script"]), is_arabic=True),
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
    print(f"[Script] Translated (arabic): '{ar_data['title']}'")
    return ar_data


def write_short_script(en_long_script: dict) -> dict:
    """Generate a ~130-word two-part script for a 55-second short video."""
    topic  = en_long_script.get("topic", "")
    _si    = get_series_for_person(topic)
    series = f"{_si[0]} {_si[1]}" if _si else en_long_script.get("niche", "the series")

    prompt = f"""Write a 60-90 second true crime short script about: {topic}
Related series/movie: {series}

MUST BE 150-180 WORDS TOTAL. Count every single word before finishing.

PART 1 — REAL PERSON (90 words):
- Open with most shocking fact + specific number
- 4-5 facts with real dates and dollar amounts
- Short sentences — maximum 12 words each
- No vague phrases like "rose to infamy" or "criminal mastermind"

PART 2 — SERIES CONNECTION (70 words):
- Name "{series}" directly
- One specific difference real events vs screen
- What the movie/show got right
- End with exactly: "Follow Dark Crime Decoded for the full story"

EXAMPLE (Jordan Belfort):
Jordan Belfort was born July 9, 1962 in Queens.
By age 26 he made 49 million dollars in one year.
He ran Stratton Oakmont with 1,000 employees.
The FBI estimated he defrauded investors of 200 million dollars.
He served only 14 months in Otisville Prison.
He drove a Ferrari and owned a 167-foot yacht.
His wife left him while he was in prison.

Wolf of Wall Street showed Leonardo DiCaprio as Belfort.
The film said he served 22 months — actually it was 14.
Margot Robbie played his real wife Nadine Caridi.
The film captured his excess perfectly.
But the real story has even more shocking twists.
Follow Dark Crime Decoded for the full story.

PRISON SENTENCE RULE: Always write "served X years in prison" — never just "served X years".
TOTAL TARGET: 150-180 words for 70-83 seconds.
STRICT RULES:
- Count words — output must be 150-180 words total
- Every sentence = one specific fact (name, number, date, or place)
- Never start two consecutive sentences with the same word
- No headers, no bullet points — spoken words only
- Series name stays in English

Use this context from the full script:
{en_long_script.get('script', '')[:500]}

Output ONLY the spoken script text, nothing else."""

    script_text = ""
    for attempt in range(2):
        _short_prompt = prompt
        if attempt > 0:
            _short_prompt += f"\n\nCRITICAL: Previous attempt was only {len(script_text.split())} words. Write MORE. Need 150-180 words."
        script_text = _ai_script_call(_short_prompt, max_tokens=500, temperature=0.85).strip()
        words   = len(script_text.split())
        seconds = (words / 130) * 60
        print(f"[Script] Short attempt {attempt + 1}: {words} words = ~{seconds:.0f}s")
        if words >= 130:
            print(f"[Script] Short length OK: {words} words")
            break
        print(f"[Script] Short too short ({words} words) — retrying...")

    # Trim if over 200 words
    words_list = script_text.split()
    if len(words_list) > 200:
        script_text = " ".join(words_list[:180])
        print(f"[Script] Short trimmed to 180 words")

    short_data = {
        "title":           en_long_script.get("title", ""),  # overwritten below
        "hook":            en_long_script.get("hook", script_text[:100]),
        "script":          script_text,
        "on_screen_texts": en_long_script.get("on_screen_texts", [])[:2],
        "caption":         en_long_script["caption"],
        "hashtags":        en_long_script["hashtags"],
        "thumbnail_text":  en_long_script["thumbnail_text"],
        "topic":           en_long_script["topic"],
        "niche":           en_long_script["niche"],
        "search_query":    en_long_script["search_query"],
        "keywords":        en_long_script["keywords"],
        "language":        "english",
    }
    _short_title = add_short_title(short_data)
    short_data["title"]       = _short_title
    short_data["short_title"] = _short_title
    print(f"[Script] Written (english short): '{short_data['title']}'")
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
