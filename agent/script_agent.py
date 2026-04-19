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
                        json_mode: bool = False) -> str | None:
    import os
    import requests
    import json

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("[OpenAI] No API key")
        return None

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": "You are a professional true crime documentary scriptwriter."
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


def _write_documentary_script(topic: dict, research: dict) -> str:
    """Write a documentary-style script for topics where no movie/series exists."""
    name = topic.get("topic", "")
    facts = "\n".join(f"- {f}" for f in (
        research.get("research_facts") or research.get("real_facts", [])
    )[:5]) or "(research the documented events)"
    shocking = "\n".join(f"- {s}" for s in (
        research.get("research_shocking") or research.get("shocking_real_facts", [])
    )[:4]) or "(include documented allegations)"

    prompt = f"""You are a documentary scriptwriter covering under-reported world events.
Write a 1800-2000 word documentary script about: {name}

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
Legacy and ongoing impact.
End with: "Follow Dark Crime Decoded for stories Hollywood has not told yet."

RULES:
- 1800-2000 words total
- Every sentence = one specific documented fact
- Never state as confirmed what is only alleged — say "allegedly" or "accused of"
- No vague phrases — specific dates, numbers, names
- Write like a serious investigative documentary narrator

Start immediately with the HOOK. Spoken words only."""

    result = _ai_script_call(prompt, max_tokens=4000, temperature=0.75)
    words = len(result.split()) if result else 0
    print(f"[Script] Documentary script: {words} words")
    return result or ""


def write_long_script_split(topic: dict, research: dict, series_info: tuple | None) -> str:
    """Write 1800+ word script via 3 separate OpenAI calls (opening/middle/closing)."""
    import time

    series = series_info[0] if series_info else topic.get("niche", topic.get("topic", ""))
    stype  = series_info[1] if series_info else "Movie"
    name   = topic.get("topic", "")

    base_context = f"""Topic: {name}
Series/Movie: {series} ({stype})
Network: {research.get('network', 'unknown')}
Real person: {research.get('real_person', name)}
Key facts: {(research.get('research_facts') or research.get('what_show_got_right', []))[:3]}
"""

    # CALL 1 — Opening 500 words
    prompt1 = f"""{base_context}
Write the OPENING of a true crime documentary script.
Exactly 500 words.

HOOK (80 words):
Most shocking fact about {name} to open with.
Make viewer unable to stop watching.
Start with a specific date, number or shocking event.

SERIES INTRO (150 words):
What {series} showed the world.
Why millions watched it.
Celebrate the show then say:
"But the real story is even more extraordinary..."

REAL BACKGROUND (270 words):
Who was {name} before everything happened.
Specific dates, places, family background.
First criminal involvement with exact year.
What shaped them into who they became.

RULES:
- Exactly 500 words
- Every sentence has ONE specific fact
- No two sentences start with same word
- No vague phrases
- Write like a Netflix documentary narrator"""

    part1 = _openai_direct_call(prompt1, max_tokens=800)
    if not part1:
        print("[Script] Split call 1 failed")
        return ""
    time.sleep(3)

    # CALL 2 — Main story 800 words
    prompt2 = f"""{base_context}
Write the MAIN STORY of a true crime documentary.
Exactly 800 words.

MAIN STORY (800 words):
Full chronological story of {name}.
Start from their first major crime.
Include specific dates, amounts, names.
Key events that {series} depicted.
What the {stype} got right vs reality.
Real quotes from people involved.
Specific numbers — money, victims, dates.
Dramatic turning points in the story.

RULES:
- Exactly 800 words
- Chronological order with specific years
- Every paragraph introduces new information
- No repetition from opening section
- Include at least 8 specific dates or numbers"""

    part2 = _openai_direct_call(prompt2, max_tokens=1200)
    if not part2:
        print("[Script] Split call 2 failed")
        return ""
    time.sleep(3)

    # CALL 3 — Closing 500 words
    prompt3 = f"""{base_context}
Write the CLOSING of a true crime documentary.
Exactly 500 words.

SHOCKING FACTS (200 words):
3-4 facts about {name} that {series} never showed.
Things that would shock even fans of the show.
Real impact on real people.

REAL VS SCREEN (200 words):
Direct comparison format:
"In {series}, they showed X. In reality, Y happened."
3 specific scene comparisons.
What Hollywood changed for drama.
Celebrate both the real story and the {stype}.

CONCLUSION (100 words):
What happened to {name} after the story ended.
Where are they now or how did they die.
Legacy and impact on history.
End with: "Follow Dark Crime Decoded for more real stories behind your favourite crime {stype}s."

RULES:
- Exactly 500 words
- Specific facts only — no vague statements
- End with exact CTA phrase above"""

    part3 = _openai_direct_call(prompt3, max_tokens=800)
    if not part3:
        print("[Script] Split call 3 failed")
        return ""

    parts = [p for p in [part1, part2, part3] if p]
    full_script = "\n\n".join(parts)

    words   = len(full_script.split())
    minutes = words / 130
    print(f"[Script] Split combined: {words} words = ~{minutes:.1f} minutes ✅")
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
        print(f"[Script] Documentary angle detected for: {topic['topic']}")
        script_text      = validate_script(_write_documentary_script(topic, research))
        _series_name_raw = _si_long[0] if _si_long else topic.get("niche", topic["topic"])
        _series_type_raw = "Documentary"
        doc_title = (
            f"The Untold Story of {topic['topic']}: "
            f"What The World Needs To Know | Dark Crime Decoded"
        )
        script_data = {
            "title":           doc_title,
            "hook":            script_text[:120] if script_text else "",
            "script":          script_text,
            "on_screen_texts": [],
            "caption":         f"The real untold story of {topic['topic']}. Follow Dark Crime Decoded.",
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

    # Primary: 3-call split (opening 500 + middle 800 + closing 500 = 1800 words)
    script_text = write_long_script_split(topic, research, _si_long)
    if script_text and len(script_text.split()) >= 1200:
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


def _fix_arabic(text: str) -> str:
    """Apply all Arabic post-processing fixes in one call."""
    text = fix_arabic_prison_terms(text)
    text = fix_arabic_cta(text)
    return text


def translate_to_arabic(text: str) -> str:
    """Translate English text to Arabic using Google Translate free REST API."""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": "en",
        "tl": "ar",
        "dt": "t",
        "q": text,
    }
    import requests as _requests
    response = _requests.get(url, params=params)
    response.raise_for_status()
    result = response.json()
    translated = "".join([item[0] for item in result[0]])
    return _fix_arabic(translated)


def translate_script(en_script: dict) -> dict:
    """Translate an English script_data dict into Arabic using Google Translate."""
    ar_data = {
        "title":          _build_arabic_title(
                              en_script.get("title", ""),
                              en_script.get("series_name"),
                              en_script.get("series_type"),
                          ),
        "hook":           translate_to_arabic(en_script.get("hook", "")),
        "script":         _fix_arabic(translate_to_arabic(en_script["script"])),
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
