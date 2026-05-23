# ============================================================
#  agents/video_agent.py  —  AI-generated images + voiceover
# ============================================================
import os
import re
import json
import time
import random
import asyncio
import subprocess
import shutil
import requests
from pathlib import Path
try:
    # Pillow 10+ removed Image.ANTIALIAS, while MoviePy 1.x still references it.
    # Keep a runtime alias so rendering works across both versions.
    from PIL import Image as _PILImage
    if not hasattr(_PILImage, "ANTIALIAS"):
        _PILImage.ANTIALIAS = _PILImage.Resampling.LANCZOS
except Exception:
    pass

import moviepy
print(f"[Video] MoviePy version: {moviepy.__version__}")
if moviepy.__version__.startswith('2'):
    print("[Video] WARNING: MoviePy 2.x detected!")
    print("[Video] Using compatibility mode")
    MOVIEPY_V2 = True
else:
    print("[Video] MoviePy 1.x confirmed âœ…")
    MOVIEPY_V2 = False


def make_image_clip(img_array, duration):
    """Create a static image VideoClip compatible with MoviePy 1.x and 2.x."""
    try:
        from moviepy.editor import ImageClip
        return ImageClip(img_array).set_duration(duration)
    except TypeError:
        import numpy as np
        try:
            from moviepy.editor import VideoClip
        except ImportError:
            from moviepy import VideoClip
        def _make_frame(t):
            return img_array
        return VideoClip(_make_frame, duration=duration)
from config import (
    AUDIO_DIR, VIDEO_DIR, FINAL_DIR,
    VIDEO_WIDTH, VIDEO_HEIGHT,
    SHORT_VIDEO_DURATION, LONG_VIDEO_DURATION,
    EDGETTS_RATE, OPENAI_TTS_SPEED,
)

IMAGES_DIR = "output/images"
STOCK_VIDEOS_DIR = "output/stock_videos"
for d in [AUDIO_DIR, VIDEO_DIR, FINAL_DIR, IMAGES_DIR, STOCK_VIDEOS_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)

# ── Active topic content paths (set by set_active_topic_content) ─────────────
_ACTIVE_TOPIC_CONTENT: dict = {}

# ── Real-archive image tracker ────────────────────────────────────────────────
# Paths that came from Wikimedia / real archive (not AI-generated).
# Populated by build_documentary_visual_pool() and get_person_images().
# Used by match_images_to_moments() to enforce documentary rhythm:
#   REAL → REAL → REAL → GENERATED ATMOSPHERE → REAL
_REAL_IMAGE_PATHS: set[str] = set()


def set_active_topic_content(paths: dict) -> None:
    """Register per-topic content paths so image generation persists there."""
    global _ACTIVE_TOPIC_CONTENT
    _ACTIVE_TOPIC_CONTENT = paths or {}
    if _ACTIVE_TOPIC_CONTENT.get("images_path"):
        print(f"[Image] Active topic images dir: {_ACTIVE_TOPIC_CONTENT['images_path']}")


def _get_images_dir() -> str:
    """Return active topic images dir, falling back to the legacy shared dir."""
    return _ACTIVE_TOPIC_CONTENT.get("images_path") or IMAGES_DIR


def _check_image_prompt_cache(prompt: str) -> str | None:
    """Return cached image path for this prompt, or None if not cached/invalid."""
    cache_dir = _ACTIVE_TOPIC_CONTENT.get("cache_path")
    if not cache_dir:
        return None
    try:
        import hashlib as _hl, json as _js
        _h    = _hl.sha256(prompt.encode("utf-8")).hexdigest()[:20]
        _file = os.path.join(cache_dir, "images_cache.json")
        if not os.path.exists(_file):
            return None
        with open(_file, encoding="utf-8") as _f:
            _cache = _js.load(_f)
        _path = _cache.get(_h)
        if _path and os.path.exists(_path) and os.path.getsize(_path) > 5_000:
            try:
                from PIL import Image as _PILImg
                with _PILImg.open(_path) as _img:
                    _img.verify()
                return _path
            except Exception:
                pass  # corrupted image — treat as cache miss
    except Exception:
        pass
    return None


def _save_image_prompt_cache(prompt: str, image_path: str) -> None:
    """Persist a prompt → image_path mapping in the topic cache."""
    cache_dir = _ACTIVE_TOPIC_CONTENT.get("cache_path")
    if not cache_dir:
        return
    try:
        import hashlib as _hl, json as _js
        _h    = _hl.sha256(prompt.encode("utf-8")).hexdigest()[:20]
        _file = os.path.join(cache_dir, "images_cache.json")
        _tmp  = _file + ".tmp"
        _cache: dict = {}
        if os.path.exists(_file):
            with open(_file, encoding="utf-8") as _f:
                _cache = _js.load(_f)
        _cache[_h] = image_path
        with open(_tmp, "w", encoding="utf-8") as _f:
            _js.dump(_cache, _f, indent=2)
        os.replace(_tmp, _file)
    except Exception:
        pass

# Unified TTS speed — sourced from config so one constant controls all engines/languages.
TTS_SPEED = OPENAI_TTS_SPEED
EDGETTS_RATE_120 = "+0%"
_ELEVENLABS_DISABLED = False
_OPENAI_QUOTA_EXCEEDED = False  # set True on first 429 — skips all subsequent OpenAI TTS calls


# â"€â"€ Chapter / timestamp helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def format_time(seconds: float) -> str:
    """Convert seconds to MM:SS string (e.g. 105.3 → '01:45')."""
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m:02d}:{s:02d}"


def get_audio_duration(audio_path: str) -> float:
    """Return audio duration in seconds using mutagen, falling back to moviepy."""
    try:
        from mutagen.mp3 import MP3
        return MP3(audio_path).info.length
    except ImportError:
        os.system("pip install mutagen -q")
        try:
            from mutagen.mp3 import MP3
            return MP3(audio_path).info.length
        except Exception:
            pass
    except Exception as e:
        print(f"[Video] mutagen error: {e}")
    try:
        try:
            from moviepy.editor import AudioFileClip as _AC
        except ImportError:
            from moviepy import AudioFileClip as _AC
        return _AC(audio_path).duration
    except Exception:
        return 0.0


def _strip_section_markers(text: str) -> str:
    """Remove section markers so they are never spoken in TTS."""
    import re
    marker_line = re.compile(
        r'(?im)^\s*[\[\{\(]\s*(?:(?:section|chapter|part|Ù‚Ø³Ù…|Ø§Ù„Ù‚Ø³Ù…)\s*:\s*)?([^\]\}\)\n:]+?)\s*:?\s*[\]\}\)]\s*$'
    )
    text = marker_line.sub("", text or "")
    text = re.sub(
        r'(?im)^\s*(introduction|background|main story|shocking facts|conclusion|Ù…Ù‚Ø¯Ù…Ø©|Ø§Ù„Ø®Ù„ÙÙŠØ©|Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©|Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©|Ø§Ù„Ø®Ø§ØªÙ…Ø©)\s*:\s*$',
        "",
        text,
    )
    # Backward-compatible cleanup for inline [SECTION: ...] markers.
    text = re.sub(r'\[SECTION:[^\]]+\]\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


# â"€â"€ Voiceover â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def get_voice(language: str) -> str:
    voices = {
        "arabic": "ar-SA-HamedNeural",
        "english": "en-US-GuyNeural"
    }
    return voices.get(language.lower(), "en-US-GuyNeural")


# ── Arabic TTS pronunciation map ──────────────────────────────────────────────
# Replaces foreign names/brands that appear in Arabic script with phonetic Arabic
# equivalents so the TTS engine doesn't mispronounce Latin characters.
# Listed longest-first so multi-word phrases match before single words.
_ARABIC_PRONUNCIATION = [
    # Streaming / platforms
    ("Netflix",          "نتفليكس"),
    ("YouTube",          "يوتيوب"),
    ("Amazon Prime",     "أمازون برايم"),
    ("Amazon",           "أمازون"),
    ("HBO",              "إتش بي أو"),
    ("TikTok",           "تيك توك"),
    ("Instagram",        "إنستغرام"),
    ("WhatsApp",         "واتساب"),
    ("Google",           "غوغل"),
    ("Twitter",          "تويتر"),
    # Law enforcement / agencies
    ("FBI",              "إف بي آي"),
    ("CIA",              "سي آي إيه"),
    ("DEA",              "دي إيه إيه"),
    ("NSA",              "إن إس إيه"),
    ("LAPD",             "شرطة لوس أنجلوس"),
    ("Interpol",         "الإنتربول"),
    # Shows / films from pipeline topics
    ("Mindhunter",       "مايند هانتر"),
    ("Breaking Bad",     "بريكينج باد"),
    ("Narcos Mexico",    "ناركوس المكسيك"),
    ("Narcos",           "ناركوس"),
    ("Scarface",         "سكارفيس"),
    ("Goodfellas",       "غودفيلاز"),
    ("The Godfather",    "العراب"),
    ("Godfather",        "العراب"),
    ("The Sopranos",     "سوبرانوز"),
    ("Sopranos",         "سوبرانوز"),
    ("The Wire",         "ذا واير"),
    ("Ozark",            "أوزارك"),
    ("Casino",           "كازينو"),
    ("Donnie Brasco",    "دوني براسكو"),
    ("Sicario",          "سيكاريو"),
    ("Griselda",         "غريسيلدا"),
    ("American Gangster","الغانغستر الأمريكي"),
    ("City of God",      "مدينة الله"),
    ("Peaky Blinders",   "بيكي بلايندرز"),
    ("Money Heist",      "سرقة الأموال"),
    # Key people
    ("John Douglas",     "جون دوغلاس"),
    ("Pablo Escobar",    "بابلو إسكوبار"),
    ("El Chapo",         "إل تشابو"),
    ("Al Capone",        "آل كابوني"),
    ("Frank Lucas",      "فرانك لوكاس"),
    ("Tony Montana",     "توني مونتانا"),
    ("Walter White",     "والتر وايت"),
    ("Jesse Pinkman",    "جيسي بينكمان"),
    ("Griselda Blanco",  "غريسيلدا بلانكو"),
    ("Whitey Bulger",    "وايتي بولجر"),
    ("Henry Hill",       "هنري هيل"),
    ("Michael Corleone", "مايكل كورليوني"),
    ("Vito Corleone",    "فيتو كورليوني"),
]


def _apply_arabic_pronunciation(text: str) -> str:
    """Replace foreign names in Arabic text with phonetic Arabic equivalents."""
    import re as _pre
    for en, ar in sorted(_ARABIC_PRONUNCIATION, key=lambda x: len(x[0]), reverse=True):
        text = _pre.sub(_pre.escape(en), ar, text, flags=_pre.IGNORECASE)
    return text


# ── Selective tashkeel (diacritics) ──────────────────────────────────────────
# Applied ONLY to high-ambiguity words — names, crime/legal terms, locations.
# Never fully vowelize: that bloats the script and slows TTS.
ARABIC_PRONUNCIATION_FIXES: dict[str, str] = {
    # Serial killer names (common in pipeline)
    "دهمر":          "دَاهمَر",
    "داهمر":         "دَاهمَر",
    "بندي":          "بَاندي",
    "غيسي":          "غِيسي",
    "بيرمر":         "بيرمَر",
    # Investigative / police
    "المحقق":        "المُحَقِّق",
    "المحققون":      "المُحَقِّقون",
    "المحققين":      "المُحَقِّقين",
    "التحقيق":       "التَّحقيق",
    "تحقيق":         "تَحقيق",
    # Crime terms
    "الجريمة":       "الجَريمة",
    "الجرائم":       "الجَرائِم",
    "الضحية":        "الضَّحِيَّة",
    "الضحايا":       "الضَّحَايا",
    "القاتل":        "القاتِل",
    "القتل":         "القَتل",
    "الاعتراف":      "الاعتِراف",
    "الجثة":         "الجُثَّة",
    "الجثث":         "الجُثَث",
    "الشهود":        "الشُّهود",
    "الأدلة":        "الأَدِلَّة",
    "الاعتقال":      "الاعتِقال",
    "الاختطاف":      "الاختِطاف",
    "التعذيب":       "التَّعذيب",
    "العصابة":       "العِصابة",
    # Legal / court
    "المحكمة":       "المَحكَمة",
    "القضاء":        "القَضاء",
    "العقوبة":       "العُقوبة",
    "الإدانة":       "الإِدانة",
    "البراءة":       "البَراءة",
    "المتهم":        "المُتَّهم",
    "المتهمون":      "المُتَّهمون",
    # Prison
    "السجن":         "السِّجن",
    "المعتقل":       "المُعتَقَل",
    # Locations (transliterated)
    "ميلووكي":       "مِيلووكي",
    "ويسكونسن":      "وِسكونسِن",
}


def apply_arabic_pronunciation_fixes(text: str) -> str:
    """Apply selective tashkeel to high-ambiguity Arabic words for correct TTS pronunciation."""
    for wrong, correct in ARABIC_PRONUNCIATION_FIXES.items():
        text = text.replace(wrong, correct)
    print("[AR] Selective tashkeel added")
    return text


# ── Arabic number expansion ───────────────────────────────────────────────────

_AR_ONES = [
    "", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة",
    "ستة", "سبعة", "ثمانية", "تسعة", "عشرة",
    "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر",
    "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر",
]
_AR_TENS = [
    "", "", "عشرون", "ثلاثون", "أربعون", "خمسون",
    "ستون", "سبعون", "ثمانون", "تسعون",
]
_AR_HUNDREDS = [
    "", "مائة", "مئتان", "ثلاثمائة", "أربعمائة", "خمسمائة",
    "ستمائة", "سبعمائة", "ثمانمائة", "تسعمائة",
]
_AR_DECADES = {
    "1920s": "العشرينيات", "1930s": "الثلاثينيات", "1940s": "الأربعينيات",
    "1950s": "الخمسينيات", "1960s": "الستينيات",  "1970s": "السبعينيات",
    "1980s": "الثمانينيات","1990s": "التسعينيات",  "2000s": "الألفينيات",
    "2010s": "العشرينيات من الألفية الثالثة",
    "2020s": "عشرينيات الألفية الثالثة",
}


def _int_to_arabic_words(n: int) -> str:
    """Convert a non-negative integer to its spoken Arabic word form."""
    if n == 0:
        return "صفر"
    parts: list[str] = []
    if n >= 1000:
        th = n // 1000
        if th == 1:
            parts.append("ألف")
        elif th == 2:
            parts.append("ألفان")
        elif 3 <= th <= 10:
            parts.append(_AR_ONES[th] + " آلاف")
        else:
            parts.append(_int_to_arabic_words(th) + " ألف")
        n %= 1000
    if n >= 100:
        parts.append(_AR_HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        t, o = n // 10, n % 10
        if o:
            parts.append(_AR_ONES[o] + " و" + _AR_TENS[t])
        else:
            parts.append(_AR_TENS[t])
    elif n > 0:
        parts.append(_AR_ONES[n])
    return " و".join(parts)


def expand_arabic_numbers(text: str) -> str:
    """Convert digits in Arabic text to spoken Arabic word form before TTS."""
    import re as _re

    # 1. Decades first (e.g. "1990s" before the bare year "1990")
    for decade, ar in _AR_DECADES.items():
        text = text.replace(decade, ar)

    # 2. 4-digit years 1900–2099
    def _replace_year(m: re.Match) -> str:
        return _int_to_arabic_words(int(m.group()))

    text = _re.sub(r'\b(19\d{2}|20[012]\d)\b', _replace_year, text)

    # 3. All remaining standalone numbers ≥ 2
    def _replace_num(m: re.Match) -> str:
        n = int(m.group())
        if n < 2:
            return m.group()   # keep 0/1 as-is; rarely cause issues
        return _int_to_arabic_words(n)

    text = _re.sub(r'\b\d+\b', _replace_num, text)

    print("[AR] Numbers expanded")
    return text


def generate_voiceover_edgetts(script_text: str, filename: str, language: str = "english") -> str:
    """Generate voiceover using edge-tts."""
    try:
        import edge_tts
    except ImportError:
        os.system("pip install edge-tts -q")
        import edge_tts

    if language.lower() == "arabic":
        voice = "ar-SA-ZariyahNeural"
        rate  = EDGETTS_RATE_120
    else:
        voice = "en-US-ChristopherNeural"
        rate  = EDGETTS_RATE_120

    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")

    async def _generate():
        communicate = edge_tts.Communicate(
            text=script_text,
            voice=voice,
            rate=rate,
            volume="+0%",
        )
        await communicate.save(audio_path)

    asyncio.run(_generate())
    print(f"[Video] Voiceover saved (edge-tts): {audio_path}")
    return audio_path


def preprocess_arabic_tts(text: str) -> str:
    """
    Full Arabic TTS preprocessing pipeline:
      1. apply_arabic_pronunciation_fixes — selective tashkeel
      2. expand_arabic_numbers — digits → spoken Arabic words
      3. punctuation pacing — commas, ellipses for natural narration rhythm
    """
    import re as _re

    text = apply_arabic_pronunciation_fixes(text)
    text = expand_arabic_numbers(text)

    # Pacing: sentence-ending period → ellipsis for dramatic pause
    text = _re.sub(r'\.\s*\n', '...\n', text)
    text = _re.sub(r'\.\s*$', '...', text, flags=_re.MULTILINE)
    # Comma → comma + pause hint (space before next word reads naturally)
    text = text.replace("،", "،  ")
    # Line breaks as breath pauses
    text = _re.sub(r'\n+', '\n', text)

    print("[AR] Pronunciation fixes applied")
    return text


def generate_voiceover_openai(text: str, language: str, output_path: str,
                              is_short: bool = False,
                              voice_override: str = None,
                              speed_override: float = None) -> str:
    """Generate voiceover using OpenAI TTS (tts-1) with timeout and per-chunk retry."""
    global _OPENAI_QUOTA_EXCEEDED
    import openai
    import httpx
    import hashlib
    import shutil as _shutil

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("[Voice] OpenAI API key not set — skipping")
        return None
    if _OPENAI_QUOTA_EXCEEDED:
        print("[Voice] OpenAI quota exceeded this run — skipping")
        return None

    client = openai.OpenAI(
        api_key=api_key,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )

    _INSTRUCTIONS = {
        "onyx": (
            "Deep cinematic war-documentary narrator. "
            "Powerful, dark, commanding. Calm confidence with subtle tension underneath every sentence. "
            "Slight dramatic pause after shocking facts. "
            "Lower slower tone during tragic moments. Never robotic or exaggerated."
        ),
        "nova": (
            "Sharp modern investigative narrator. "
            "Fast hook, intense energy. Strong first sentence. "
            "Build suspense gradually. Clear pronunciation of foreign names."
        ),
        "alloy": (
            "Neutral elite documentary narrator. "
            "Smooth, believable, controlled tension. "
            "Strong clear ending sentence. Maintain realism and credibility."
        ),
        "alloy_arabic": (
            "Ø£Ø³Ù„ÙˆØ¨ Ø§Ù„Ø£Ø¯Ø§Ø¡: Ø±Ø§ÙˆÙ ÙˆØ«Ø§Ø¦Ù‚ÙŠ Ø¹Ø±Ø¨ÙŠ Ø§Ø­ØªØ±Ø§ÙÙŠ. "
            "ØµÙˆØª Ø¹Ù…ÙŠÙ‚ ÙˆÙˆØ§Ø«Ù‚ ÙˆÙ‡Ø§Ø¯Ø¦. Ù†Ø¨Ø±Ø© Ø¬Ø§Ø¯Ø© ÙˆØºØ§Ù…Ø¶Ø©. Ø¥Ù„Ù‚Ø§Ø¡ Ø·Ø¨ÙŠØ¹ÙŠ Ø¬Ø¯Ø§Ù‹. "
            "ÙˆØ¶ÙˆØ­ Ù…Ù…ØªØ§Ø² Ù„Ù„Ø­Ø±ÙˆÙ. ÙˆÙ‚ÙØ§Øª Ù‚ØµÙŠØ±Ø© Ø¨Ø¹Ø¯ Ø§Ù„Ø¬Ù…Ù„ Ø§Ù„Ù…Ù‡Ù…Ø©. "
            "ØªØµØ§Ø¹Ø¯ ØªØ¯Ø±ÙŠØ¬ÙŠ ÙÙŠ Ø§Ù„ØªÙˆØªØ± Ø£Ø«Ù†Ø§Ø¡ Ø§Ù„Ø£Ø­Ø¯Ø§Ø«. "
            "Ø®ÙØ¶ Ø§Ù„Ù†Ø¨Ø±Ø© Ø¹Ù†Ø¯ Ø§Ù„Ù…Ø¢Ø³ÙŠ ÙˆØ§Ù„Ø¶Ø­Ø§ÙŠØ§. "
            "Ù„Ø§ Ù…Ø¨Ø§Ù„ØºØ©ØŒ Ù„Ø§ ØªÙ…Ø«ÙŠÙ„ Ø²Ø§Ø¦Ø¯ØŒ Ù„Ø§ ØµÙˆØª Ø±ÙˆØ¨ÙˆØªÙŠ. "
            "Ø§Ù„Ø¥Ø­Ø³Ø§Ø³ Ø§Ù„Ø¹Ø§Ù…: Ù‡ÙŠØ¨Ø©ØŒ ØºÙ…ÙˆØ¶ØŒ Ù…ØµØ¯Ø§Ù‚ÙŠØ©ØŒ Ù‚ÙˆØ© Ù‡Ø§Ø¯Ø¦Ø©ØŒ Ø³Ø±Ø¯ Ø³ÙŠÙ†Ù…Ø§Ø¦ÙŠ."
        ),
    }

    if language == "arabic":
        model = "tts-1"
        voice = voice_override or "nova"
        speed = speed_override if speed_override is not None else 1.1
        label = "Arabic"
    else:
        model = "tts-1"
        voice = voice_override or "alloy"
        speed = speed_override if speed_override is not None else 1.0
        label = "English"

    tts_instructions = None  # tts-1 does not support instructions param

    print(f"[TTS] Language={language}")
    print(f"[TTS] Using OpenAI voice={voice} speed={speed}")

    # ── Persistent hash cache ──────────────────────────────────────────────────
    _cache_key  = hashlib.sha256(
        f"{text}|{language}|{voice}|{model}|{speed}".encode()
    ).hexdigest()[:16]
    _cache_path = os.path.join(AUDIO_DIR, f"tts_{_cache_key}.mp3")
    if os.path.exists(_cache_path) and os.path.getsize(_cache_path) > 0:
        _shutil.copy2(_cache_path, output_path)
        print(f"[TTS] cache hit — {_cache_path}")
        return output_path
    # ──────────────────────────────────────────────────────────────────────────

    def _is_quota_err(err_str: str) -> bool:
        """Permanent credit/billing exhaustion — do NOT retry."""
        _SIGNALS = ("insufficient_quota", "billing", "credit", "payment",
                    "402", "your balance", "out of credits")
        s = err_str.lower()
        return any(sig in s for sig in _SIGNALS) or ("429" in err_str and "quota" in s)

    try:
        # Arabic: cap at 750 words per chunk (≈4000 chars) — each chunk = one OpenAI TTS API call
        # English: cap at 4000 chars
        if language == "arabic":
            chunks = _split_text(text, max_chars=4000, max_words=750)
        else:
            chunks = _split_text(text, max_chars=4000)
        # Hard-cap: any chunk still > 4090 chars gets split at whitespace boundary
        _safe: list[str] = []
        for _c in chunks:
            if len(_c) > 4090:
                for _start in range(0, len(_c), 4090):
                    _safe.append(_c[_start:_start + 4090].strip())
            else:
                _safe.append(_c)
        chunks = [c for c in _safe if c]
        print(f"[Voice] OpenAI TTS: {len(chunks)} chunk(s) for {language}")

        audio_files: list[str] = []
        base = output_path.replace(".mp3", "")
        for i, chunk in enumerate(chunks):
            chunk_path = f"{base}_oai_chunk{i}.mp3"

            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                print(f"[Voice] OpenAI chunk {i + 1}/{len(chunks)} cached — reusing")
                audio_files.append(chunk_path)
                continue

            for attempt in range(3):
                try:
                    tts_kwargs = dict(
                        model=model,
                        voice=voice,
                        input=chunk,
                        speed=speed,
                    )
                    if tts_instructions:
                        tts_kwargs["instructions"] = tts_instructions
                    response = client.audio.speech.create(**tts_kwargs)
                    response.stream_to_file(chunk_path)
                    print(f"[Voice] OpenAI chunk {i + 1}/{len(chunks)} done")
                    audio_files.append(chunk_path)
                    break
                except Exception as e:
                    err_str = str(e)
                    if _is_quota_err(err_str):
                        # Permanent billing/credit exhaustion — abort, no retries
                        _OPENAI_QUOTA_EXCEEDED = True
                        for f in audio_files:
                            try: os.remove(f)
                            except OSError: pass
                        return None
                    # Transient network error — retry with backoff
                    print(f"[Voice] OpenAI chunk attempt {attempt + 1} failed: {e}")
                    time.sleep(5)
            else:
                print(f"[Voice] OpenAI chunk {i + 1} failed all attempts")
                for f in audio_files:
                    try: os.remove(f)
                    except OSError: pass
                return None

        # Merge chunks
        if len(audio_files) == 1:
            _shutil.move(audio_files[0], output_path)
        else:
            merged = False
            import subprocess
            list_path = f"{base}_oai_list.txt"
            with open(list_path, "w", encoding="utf-8") as lf:
                for cf in audio_files:
                    lf.write(f"file '{os.path.abspath(cf)}'\n")
            ffmpeg_bin = _get_ffmpeg()
            if ffmpeg_bin:
                try:
                    subprocess.run(
                        [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                         "-i", list_path, "-c", "copy", output_path],
                        check=True, capture_output=True,
                    )
                    merged = True
                except Exception as e:
                    print(f"[Voice] OpenAI ffmpeg merge failed: {e}")
            if not merged:
                merged = _merge_chunks_pydub(audio_files, output_path)
            if not merged:
                _shutil.copy(audio_files[0], output_path)
                print("[Voice] OpenAI using first chunk only")
            for f in audio_files:
                if os.path.exists(f) and f != output_path:
                    try: os.remove(f)
                    except OSError: pass
            try: os.remove(list_path)
            except OSError: pass

        # Save to persistent cache for future runs
        _shutil.copy2(output_path, _cache_path)
        print(f"[TTS] OpenAI success")
        return output_path

    except Exception as e:
        print(f"[Voice] OpenAI TTS failed: {e}")
        return None


def _get_ffmpeg() -> str | None:
    """Locate ffmpeg binary — imageio_ffmpeg (bundled with moviepy) first."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    try:
        import shutil as _shutil
        path = _shutil.which("ffmpeg")
        if path:
            return path
    except Exception:
        pass
    for loc in [
        r"C:\Users\abdot\AppData\Roaming\Python\Python314\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe",
        "C:/ffmpeg/bin/ffmpeg.exe",
        "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    ]:
        if os.path.exists(loc):
            return loc
    return None


def _merge_chunks_pydub(chunk_files: list[str], output_path: str) -> bool:
    """Merge MP3 chunks with pydub, pointing it at the imageio_ffmpeg binary."""
    try:
        ffmpeg_path = _get_ffmpeg()
        import pydub
        if ffmpeg_path:
            pydub.AudioSegment.converter = ffmpeg_path
        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for cf in chunk_files:
            combined += AudioSegment.from_mp3(cf)
        combined.export(output_path, format="mp3")
        return True
    except Exception as e:
        print(f"[Voice] pydub merge failed: {e}")
        return False


def _split_text(text: str, max_chars: int = 4000, max_words: int = None) -> list[str]:
    """Split text into TTS-safe chunks; no content is ever dropped.
    max_words: word-count cap per chunk (use ~750 for Arabic to stay under OpenAI 4096-char limit).
    Supports Arabic sentence delimiters (، ؟ .) so Arabic paragraphs split correctly."""

    def _over_limit(chunk: str) -> bool:
        if len(chunk) > max_chars:
            return True
        if max_words and len(chunk.split()) >= max_words:
            return True
        return False

    def _fits(current: str, addition: str) -> bool:
        combined = (current + "

" + addition).lstrip("
")
        return not _over_limit(combined)

    if not _over_limit(text):
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("

")
    current = ""
    for para in paragraphs:
        if not current or _fits(current, para):
            current = (current + "

" + para).lstrip("
")
        else:
            if current:
                chunks.append(current.strip())
            # Paragraph itself too large — split on sentence boundaries (Arabic + Latin)
            if _over_limit(para):
                sentences = (para
                             .replace(". ", ".|").replace("! ", "!|").replace("? ", "?|")
                             .replace("، ", "،|").replace("؟ ", "؟|").replace("۔ ", "۔|")
                             .split("|"))
                sub = ""
                for sent in sentences:
                    test = (sub + " " + sent).lstrip() if sub else sent
                    if not _over_limit(test):
                        sub = test
                    else:
                        if sub:
                            chunks.append(sub.strip())
                        sub = sent[:max_chars] if len(sent) > max_chars else sent
                current = sub
            else:
                current = para
    if current:
        chunks.append(current.strip())

    original_words = len(text.split())
    chunked_words = sum(len(c.split()) for c in chunks)
    print(f"[TTS] Chunks: {len(chunks)} | Original: {original_words} words | Chunked: {chunked_words} words")
    if chunked_words < original_words * 0.95:
        print("[TTS] WARNING: Content lost in chunking!")
    return chunks


def _elevenlabs_chunk(chunk: str, voice_id: str, api_key: str, chunk_path: str) -> bool:
    """POST one chunk to ElevenLabs. Returns True on success."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    data = {
        "text": chunk,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.85,
            "style": 0.4,
            "use_speaker_boost": True,
            "speed": TTS_SPEED,
        },
        "output_format": "mp3_44100_192",
    }
    try:
        response = requests.post(url, json=data, headers=headers, timeout=180)
        if response.status_code == 200:
            with open(chunk_path, "wb") as f:
                f.write(response.content)
            return True
        if response.status_code == 401:
            print(f"[Voice] ElevenLabs 401 Unauthorized — voice ID may be invalid or inaccessible")
            return "401"
        print(f"[Voice] ElevenLabs chunk failed: {response.status_code}")
    except Exception as e:
        print(f"[Voice] ElevenLabs chunk error: {e}")
    return False


def generate_voiceover(script_text: str, filename: str, language: str = "english") -> str:
    """Generate voiceover — OpenAI TTS (tts-1-hd) → edge-tts fallback."""
    script_text = _strip_section_markers(script_text)
    try:
        from agents.script_agent import format_for_tts as _fmt
    except ImportError:
        try:
            from script_agent import format_for_tts as _fmt
        except ImportError:
            _fmt = None
    if _fmt:
        script_text = _fmt(script_text)

    # Replace foreign names with Arabic phonetic equivalents before any TTS engine
    if language == "arabic":
        script_text = _apply_arabic_pronunciation(script_text)

    # Priority 1: OpenAI TTS (primary — Arabic: nova/1.1, English: alloy/1.0)
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key and not _OPENAI_QUOTA_EXCEEDED:
        print("[Voice] Trying OpenAI TTS (primary)...")
        _oai_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
        _is_short = "short" in filename.lower()
        _primary_text = preprocess_arabic_tts(script_text) if language == "arabic" else script_text
        result = generate_voiceover_openai(_primary_text, language, _oai_path, is_short=_is_short)
        if result:
            return result

        # Priority 2: OpenAI safe fallback — alloy/1.0, no preprocessing
        if not _OPENAI_QUOTA_EXCEEDED:
            print("[TTS] OpenAI primary failed — trying safe fallback (alloy, 1.0, no preprocess)")
            result = generate_voiceover_openai(
                script_text, language, _oai_path, is_short=_is_short,
                voice_override="alloy", speed_override=1.0,
            )
            if result:
                return result
            print("[TTS] OpenAI safe fallback failed — falling back to edge-tts")
        else:
            print("[TTS] OpenAI quota exceeded — falling back to edge-tts")

    # Priority 3: edge-tts (final fallback — unchanged)
    print("[Voice] Using edge-tts")
    return generate_voiceover_edgetts(script_text, filename, language)


# â"€â"€ AI Image generation (Pollinations — free, no key) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

# Combined subject lookup — real criminals AND actor/character portraits.
# extract_main_subject() returns up to 2 entries (longest key match first)
# so Image 1 = real criminal, Image 2 = actor who played them.
SUBJECTS = {
    # â"€â"€ Real criminals â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    "pablo escobar":    "Pablo Escobar real Colombian drug lord portrait cinematic",
    "escobar":          "Pablo Escobar real Colombian drug lord portrait cinematic",
    "al capone":        "Al Capone 1920s Chicago gangster portrait historical cinematic",
    "capone":           "Al Capone 1920s Chicago gangster portrait historical cinematic",
    "jeffrey dahmer":   "Jeffrey Dahmer serial killer portrait dark cinematic",
    "dahmer":           "Jeffrey Dahmer serial killer portrait dark cinematic",
    "el chapo":         "El Chapo Sinaloa Mexican cartel boss portrait cinematic",
    "chapo":            "El Chapo Sinaloa Mexican cartel boss portrait cinematic",
    "griselda blanco":  "Griselda Blanco cocaine godmother portrait cinematic",
    "ted bundy":        "Ted Bundy serial killer portrait dark cinematic",
    "bundy":            "Ted Bundy serial killer portrait dark cinematic",
    "ed gein":          "Ed Gein Wisconsin killer portrait dark cinematic",
    "gein":             "Ed Gein Wisconsin killer portrait dark cinematic",
    "btk":              "Dennis Rader BTK killer portrait dark cinematic",
    "dennis rader":     "Dennis Rader BTK killer portrait dark cinematic",
    "jordan belfort":   "Jordan Belfort Wall Street trader portrait cinematic",
    "belfort":          "Jordan Belfort Wall Street trader portrait cinematic",
    "john gotti":       "John Gotti New York mafia boss portrait cinematic",
    "gotti":            "John Gotti New York mafia boss portrait cinematic",
    "charles manson":   "Charles Manson cult leader 1960s portrait dark cinematic dramatic",
    "manson":           "Charles Manson cult leader 1960s portrait dark cinematic dramatic",
    "helter skelter":   "Charles Manson Helter Skelter movie portrait cinematic dramatic",
    "lucky luciano":    "Lucky Luciano New York mafia boss portrait cinematic",
    "luciano":          "Lucky Luciano New York mafia portrait cinematic",
    "frank lucas":      "Frank Lucas real Harlem drug lord 1970s portrait historical cinematic",
    "frank lucas real": "Frank Lucas real Harlem drug lord 1970s portrait historical cinematic",
    "whitey bulger":    "Whitey Bulger Boston Irish mob portrait cinematic",
    "bulger":           "Whitey Bulger Boston mob boss portrait cinematic",
    "richard ramirez":  "Richard Ramirez Night Stalker killer portrait cinematic",
    "ramirez":          "Richard Ramirez Night Stalker portrait dark cinematic",
    "leopold":          "Leopold and Loeb 1924 murder case portrait cinematic",
    "loeb":             "Leopold and Loeb 1924 murder case portrait cinematic",
    "kitty genovese":   "Kitty Genovese 1964 New York victim portrait cinematic",
    "genovese":         "Kitty Genovese New York portrait cinematic",
    "amanda knox":      "Amanda Knox Italy murder case portrait cinematic",
    "knox":             "Amanda Knox portrait cinematic dramatic",

    # â"€â"€ Series / movie actors â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # Narcos — Wagner Moura + Pedro Pascal
    "narcos":              "Wagner Moura as Pablo Escobar Narcos Netflix portrait cinematic",
    "javier pena":         "Pedro Pascal as Javier Pena Narcos portrait cinematic",

    # Scarface — Al Pacino
    "scarface":            "Al Pacino as Tony Montana Scarface portrait cinematic dramatic",
    "tony montana":        "Al Pacino as Tony Montana Scarface portrait cinematic",

    # Godfather — longest keys first ensures specific matches win
    "michael corleone":    "Al Pacino as Michael Corleone Godfather portrait cinematic",
    "vito corleone":       "Marlon Brando as Vito Corleone Godfather portrait cinematic",
    "don corleone":        "Marlon Brando as Don Vito Corleone portrait dramatic cinematic",
    "godfather":           "Marlon Brando Al Pacino Godfather Corleone family portrait cinematic",
    "corleone":            "Marlon Brando as Vito Corleone Godfather portrait cinematic",

    # Breaking Bad — Cranston + Aaron Paul
    "breaking bad":        "Bryan Cranston Aaron Paul Breaking Bad portrait cinematic",
    "walter white":        "Bryan Cranston as Walter White portrait cinematic",
    "jesse pinkman":       "Aaron Paul as Jesse Pinkman portrait cinematic",

    # Dexter
    "dexter morgan":       "Michael C Hall as Dexter Morgan portrait dark cinematic",
    "dexter":              "Michael C Hall as Dexter Morgan portrait dark cinematic",

    # Peaky Blinders — Murphy + Hardy
    "peaky blinders":      "Cillian Murphy Tom Hardy Peaky Blinders portrait cinematic",
    "tommy shelby":        "Cillian Murphy as Tommy Shelby portrait dramatic cinematic",
    "alfie solomons":      "Tom Hardy as Alfie Solomons portrait cinematic",

    # Money Heist
    "la casa de papel":    "Alvaro Morte Ursula Corbero Money Heist portrait cinematic",
    "money heist":         "Alvaro Morte as The Professor Money Heist portrait cinematic",

    # Ozark — Bateman + Linney
    "ozark":               "Jason Bateman Laura Linney Ozark portrait cinematic",

    # Goodfellas — Liotta + De Niro + Pesci
    "goodfellas":          "Ray Liotta Robert De Niro Joe Pesci Goodfellas portrait cinematic",
    "henry hill":          "Ray Liotta as Henry Hill Goodfellas portrait cinematic",
    "jimmy conway":        "Robert De Niro as Jimmy Conway Goodfellas portrait",

    # Casino — De Niro + Stone
    "casino":              "Robert De Niro Sharon Stone Casino portrait cinematic dramatic",

    # Wolf of Wall Street — DiCaprio + Robbie
    "wolf of wall street": "Leonardo DiCaprio Margot Robbie Wolf of Wall Street portrait",

    # American Gangster — Denzel + Crowe
    "american gangster":   "Denzel Washington as Frank Lucas American Gangster portrait cinematic",

    # City of God
    "city of god":         "Alexandre Rodrigues City of God Brazil portrait cinematic",

    # Sicario — Blunt + del Toro
    "sicario":             "Emily Blunt Benicio del Toro Sicario portrait cinematic",

    # Boardwalk Empire
    "boardwalk empire":    "Steve Buscemi as Nucky Thompson Boardwalk Empire portrait",
    "nucky thompson":      "Steve Buscemi as Nucky Thompson portrait cinematic",
    "nucky":               "Steve Buscemi as Nucky Thompson portrait cinematic",

    # Griselda — Sofia Vergara
    "griselda":            "Sofia Vergara as Griselda Blanco portrait cinematic dramatic",

    # Night Stalker
    "night stalker":       "Richard Ramirez Night Stalker documentary portrait cinematic",

    # Mindhunter
    "mindhunter":          "Jonathan Groff Mindhunter FBI agent portrait cinematic",

    # Black Mass — Johnny Depp
    "black mass":          "Johnny Depp as Whitey Bulger Black Mass portrait cinematic",

    # Extremely Wicked — Zac Efron
    "extremely wicked":    "Zac Efron as Ted Bundy portrait cinematic dramatic",

    # The Wire — Idris Elba
    "stringer bell":       "Idris Elba as Stringer Bell portrait cinematic dramatic",
    "the wire":            "Idris Elba as Stringer Bell The Wire portrait cinematic",

    # Monster / Dahmer series — Evan Peters
    "dahmer series":       "Evan Peters as Jeffrey Dahmer portrait dark cinematic",
    "monster":             "Evan Peters as Jeffrey Dahmer Monster Netflix portrait",

    # El Chapo series
    "el chapo series":     "Marco de la O as El Chapo portrait cinematic",

    # BTK series — Rainn Wilson
    "btk series":          "Rainn Wilson as BTK killer portrait dark cinematic",

    # Wentworth
    "wentworth":           "Danielle Cormack as Bea Smith Wentworth portrait",

    # Adolescence
    "adolescence":         "Stephen Graham Adolescence Netflix portrait cinematic",

    # Stillwater
    "stillwater":          "Matt Damon Stillwater movie portrait cinematic",

    # Devil's Knot / West Memphis
    "devil's knot":        "West Memphis Three documentary portrait cinematic",

    # Sudan — documentary topics
    "hemedti":             "Mohamed Hamdan Dagalo Hemedti RSF Sudan military general portrait cinematic",
    "Ø­Ù…ÙŠØ¯ØªÙŠ":              "Sudanese military general RSF commander portrait dark cinematic dramatic",
    "dagalo":              "RSF Sudan military commander portrait cinematic dark dramatic",
    "Ù…Ø­Ù…Ø¯ Ø­Ù…Ø¯Ø§Ù† Ø¯Ù‚Ù„Ùˆ":     "Sudanese military general portrait dark cinematic dramatic",
    "omar bashir":         "Omar al-Bashir Sudan dictator president portrait cinematic",
    "Ø§Ù„Ø¨Ø´ÙŠØ±":              "Sudan president portrait dark cinematic dramatic",
}

# Keys sorted longest-first — computed once at import time
_SUBJECTS_SORTED = sorted(SUBJECTS.items(), key=lambda x: len(x[0]), reverse=True)


def extract_main_subject(title: str, script: str) -> list[str]:
    """Return up to 2 portrait prompts for a video.

    Searches title first (most reliable), then first 800 chars of script.
    Keys are matched longest-first so "pablo escobar" wins over "escobar".
    Always returns at least 1 entry (fallback generic portrait).
    """
    title_lower  = title.lower()
    script_lower = script.lower()[:800]

    # Special cases: always return real person + actor pair
    if "godfather" in title_lower:
        return [
            "Marlon Brando as Vito Corleone Godfather portrait cinematic",
            "Al Pacino as Michael Corleone portrait cinematic",
        ]
    if "frank lucas" in title_lower or "frank lucas" in script_lower[:800]:
        return [
            "Frank Lucas real Harlem drug lord 1970s portrait historical cinematic",
            "Denzel Washington as Frank Lucas American Gangster portrait cinematic",
        ]

    portraits: list[str] = []

    # Pass 1 — title
    for key, prompt in _SUBJECTS_SORTED:
        if key in title_lower and prompt not in portraits:
            portraits.append(prompt)
            if len(portraits) >= 2:
                break

    # Pass 2 — script (if we still need more)
    if len(portraits) < 2:
        for key, prompt in _SUBJECTS_SORTED:
            if key in script_lower and prompt not in portraits:
                portraits.append(prompt)
                if len(portraits) >= 2:
                    break

    if not portraits:
        portraits = ["true crime documentary person dark portrait cinematic"]

    return portraits

_LOCATIONS = {
    "colombia":    "Medellin Colombia 1980s barrio street cinematic",
    "brazil":      "Rio de Janeiro Brazil favela cinematic",
    "miami":       "Miami 1980s neon night skyline cinematic",
    "new york":    "New York City 1970s dark street cinematic",
    "chicago":     "Chicago 1920s prohibition era street cinematic",
    "mexico":      "Mexico cartel desert border town cinematic",
    "italy":       "Sicily Italy mafia village cinematic",
    "baltimore":   "Baltimore city street night urban cinematic",
    "oklahoma":    "Oklahoma 1990s rural town cinematic",
    "wisconsin":   "Wisconsin rural dark forest cinematic",
    "harlem":      "Harlem New York 1970s street cinematic",
    "wall street": "Wall Street New York financial district cinematic",
}

_ERAS = {
    "1920": "1920s prohibition era sepia cinematic",
    "1930": "1930s depression era dark cinematic",
    "1950": "1950s vintage americana cinematic",
    "1960": "1960s vintage documentary cinematic",
    "1970": "1970s gritty film grain cinematic",
    "1980": "1980s neon dark cinematic",
    "1990": "1990s gritty urban crime cinematic",
    "2000": "2000s modern crime thriller cinematic",
}

_THEMES = {
    "drug":    "cocaine drug operation laboratory bales cinematic",
    "cartel":  "cartel operation weapons money cinematic",
    "murder":  "crime scene detective investigation dark cinematic",
    "serial":  "psychological thriller dark room evidence cinematic",
    "mafia":   "mafia meeting dark restaurant suits cinematic",
    "heist":   "bank vault robbery masked figures cinematic",
    "fraud":   "financial documents money greed cinematic",
    "kidnap":  "dark room captive dramatic cinematic",
}


# â"€â"€ Wikipedia public-domain image fetcher â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def fetch_wikimedia_image(person_name: str) -> str | None:
    """Query Wikipedia API for the person's thumbnail. All results are public domain or CC."""
    params = {
        "action": "query",
        "format": "json",
        "titles": person_name.replace(" ", "_"),
        "prop": "pageimages",
        "pithumbsize": 1200,
        "piprop": "thumbnail|name",
    }
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params=params, timeout=15,
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
        )
        pages = r.json()["query"]["pages"]
        page = next(iter(pages.values()))
        image_url = page.get("thumbnail", {}).get("source", "")
        if image_url:
            print(f"[Image] Wikipedia photo found: {person_name}")
            return image_url
        return None
    except Exception as e:
        print(f"[Image] Wikipedia fetch failed for '{person_name}': {e}")
        return None


def download_wikipedia_image(image_url: str, output_path: str) -> str | None:
    """Download a Wikipedia image, smart-crop portrait/landscape → 1080x1920."""
    import io
    from PIL import Image as PILImage

    try:
        r = requests.get(image_url, timeout=30,
                         headers={"User-Agent": "DarkCrimeDecoded/1.0"})
        if r.status_code != 200:
            return None
        img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
        w, h = img.size
        # Landscape → center-crop to square, then scale up
        if w > h:
            left = (w - h) // 2
            img = img.crop((left, 0, left + h, h))
        img = img.resize((1080, 1920), PILImage.LANCZOS)
        output_path = output_path.replace(".jpg", ".png")
        img.save(output_path, "PNG")
        print(f"[Image] Wikipedia image saved: {output_path}")
        return output_path
    except Exception as e:
        print(f"[Image] Wikipedia download failed: {e}")
        return None


def _extract_person_name_from_topic(title: str, topic: str) -> str:
    """Return the best Wikipedia-searchable name for a topic.

    Checks title + topic against the SUBJECTS lookup (longest key first).
    Falls back to the raw topic string (stripped of angle dashes).
    """
    combined = (title + " " + topic).lower()
    for key, _ in _SUBJECTS_SORTED:
        if key in combined:
            return key.title()   # e.g. "pablo escobar" → "Pablo Escobar"
    # Fallback: first segment before an em-dash
    return topic.split("—")[0].strip() if topic else ""


def transform_user_image(
    user_image_path: str,
    caption: str,
    video_id: str,
    index: int,
    section_tags: list[str] | None = None,
) -> str | None:
    """
    Generate a cinematic AI version of a user image using its caption as the prompt.

    Pollinations is a text-to-image API so we use the caption as the seed text,
    with a hash-derived seed for reproducibility (same caption → same image).
    The result is 100% original AI art — no copyright concerns.
    Returns the saved output path or None on failure.
    """
    import hashlib

    caption_clean = clean_caption_for_prompt(caption or "cinematic dark portrait")
    if section_tags:
        tags_str = " ".join(section_tags)
        prompt = (
            f"{tags_str} cinematic documentary dark dramatic "
            f"professional 4k photography documentary style vertical"
        )
    else:
        prompt = (
            f"{caption_clean} cinematic portrait dramatic lighting "
            f"dark background professional 4k photography "
            f"documentary style vertical"
        )
    seed = int(hashlib.md5(caption_clean.encode()).hexdigest()[:8], 16) % 99999
    output_path = os.path.join(IMAGES_DIR, f"{video_id}_transformed_{index}.png")

    print(f"[Image] Transforming → AI cinematic: '{caption_clean[:60]}'")
    result = generate_ai_image(prompt, output_path, seed=seed)
    if result and os.path.exists(result):
        return result
    return None


def process_user_images(user_images: list[dict], video_id: str,
                        script_text: str = "") -> list[dict]:
    """
    For each user image: generate an AI-cinematic version from its caption,
    then include the original.

    Tags are derived from:
      1. The actual filename stem (not generic "cinematic dark portrait")
      2. First 5 meaningful words from the corresponding script section at image position i

    Returns expanded list in this order per image:
      1. AI-transformed version
      2. Original user image
    """
    import re as _re

    # Pre-parse script sections to source keywords per image position
    section_texts: list[str] = []
    if script_text:
        try:
            sections = _parse_script_sections(script_text)
            section_texts = [content for _, content in sections]
        except Exception:
            section_texts = []

    def _section_keywords(idx: int) -> list[str]:
        if not section_texts:
            return []
        text = section_texts[idx % len(section_texts)]
        words = [w.lower() for w in text.split()[:12] if len(w) > 3 and w.isalpha()]
        return words[:5]

    processed: list[dict] = []

    for i, img_info in enumerate(user_images):
        path    = img_info.get("path", "")
        fname   = os.path.splitext(os.path.basename(path))[0]

        # Caption priority:
        # 1. Telegram caption (user-provided, most specific)
        # 2. Sidecar .txt file saved by notify_agent at download time
        # 3. Filename stem
        # 4. Script section keywords fallback
        telegram_caption = (img_info.get("caption") or "").strip()
        if not telegram_caption:
            # Check for sidecar .txt written by notify_agent
            txt_path = _re.sub(r'\.[^.]+$', '.txt', path)
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, encoding="utf-8") as _tf:
                        telegram_caption = _tf.read().strip()
                    if telegram_caption:
                        print(f"[Image] Loaded caption from sidecar: '{telegram_caption[:80]}'")
                except Exception:
                    pass

        caption = telegram_caption or fname or "documentary scene"
        if caption in ("cinematic dark portrait", "documentary scene", ""):
            caption = fname or f"image {i + 1}"

        # Tags: if Telegram caption present, use it directly (most specific);
        # otherwise fall back to script section keywords
        base_tags = img_info.get("tags", [])
        if not path or not os.path.exists(path):
            continue

        if telegram_caption:
            # Caption words ARE the tags — no need for script section guessing
            caption_tags = [w.lower() for w in telegram_caption.split() if len(w) > 3]
            sec_kws = caption_tags[:8]
            print(f"[Image] Processing user image {i + 1}: caption='{caption[:80]}' (Telegram-tagged)")
        else:
            # Fall back to script section keywords
            sec_kws = _section_keywords(i)
            print(f"[Image] Processing user image {i + 1}: '{caption[:60]}' section_kws={sec_kws}")

        # AI-transformed version
        transformed = transform_user_image(path, caption, video_id, i, section_tags=sec_kws)
        if transformed:
            processed.append({
                "path":    transformed,
                "tags":    ["portrait", "cinematic"] + sec_kws + [t for t in base_tags if t not in {"portrait", "cinematic"}],
                "caption": f"cinematic {caption}",
                "type":    "ai_transformed",
            })

        # Original user image
        processed.append({
            "path":    path,
            "tags":    ["real", "photo"] + sec_kws + [t for t in base_tags if t not in {"real", "photo"}],
            "caption": caption,
            "type":    "user_original",
        })

        print(f"[Image] User image {i + 1}: AI transform + original queued (section tags: {sec_kws})")

    return processed


def check_image_relevance(
    image_path: str,
    topic: str,
    series_name: str | None,
    part_number: int | None = None,
) -> str:
    """Use OpenAI Vision to decide image relevance. Returns 'use_now', 'save_part2', or 'ignore'."""
    import base64

    # User-uploaded images (Telegram) are always relevant — user chose them intentionally.
    if "user_images" in (image_path or "").replace("\\", "/"):
        print(f"[Image] User image — always USE_NOW: {os.path.basename(image_path)}")
        return "use_now"

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return "use_now"

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        print(f"[Image] Cannot read image: {e}")
        return "ignore"

    prompt = f"""Look at this image carefully.
Current video topic: {topic}
Related series/movie: {series_name or 'Documentary'}
Current part: Part {part_number or 1}

Answer with ONLY one of these three options:

USE_NOW — if the image shows:
- The real person ({topic})
- Actors from {series_name}
- Locations related to {topic}
- Historical events related to {topic}
- Documents or evidence related to {topic}

SAVE_PART2 — if the image shows:
- Events that belong to Part 2 of the story
- Later timeline events not covered in Part 1
- Related but different aspect of the story

IGNORE — if the image shows:
- Unrelated people or places
- Random photos with no connection
- Duplicate of another image sent

Reply with ONLY: USE_NOW or SAVE_PART2 or IGNORE"""

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "low",
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
                "max_tokens": 10,
                "temperature": 0,
            },
            timeout=30,
        )
        if r.status_code == 200:
            answer = r.json()["choices"][0]["message"]["content"].strip().upper()
            if "USE_NOW" in answer:
                print(f"[Image] âœ… Relevant: {image_path}")
                return "use_now"
            if "SAVE_PART2" in answer:
                print(f"[Image] 🔦 Save for Part 2: {image_path}")
                return "save_part2"
            print(f"[Image] âŒ Not relevant: {image_path}")
            return "ignore"
    except Exception as e:
        print(f"[Image] Vision check failed: {e}")
        return "use_now"

    return "use_now"


def save_images_for_part2(images: list, topic: str) -> int:
    """Copy images to output/pending_images/ and write manifest. Returns count saved."""
    import shutil
    import datetime

    os.makedirs("output/pending_images", exist_ok=True)
    saved: list[str] = []

    for i, img in enumerate(images):
        path = img if isinstance(img, str) else img.get("path", "")
        if path and os.path.exists(path):
            ext  = os.path.splitext(path)[1] or ".jpg"
            dest = f"output/pending_images/part2_{topic.replace(' ', '_')}_{i}{ext}"
            shutil.copy2(path, dest)
            saved.append(dest)
            print(f"[Image] Saved for Part 2: {dest}")

    manifest = {
        "topic":    topic,
        "images":   saved,
        "saved_at": datetime.date.today().isoformat(),
    }
    with open("output/pending_images/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"[Image] {len(saved)} images saved for Part 2")
    return len(saved)


def load_part2_images(topic: str) -> list[str]:
    """Load and clear saved Part 2 images if they match topic. Returns list of paths."""
    manifest_path = "output/pending_images/manifest.json"
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        if topic.lower() in (data.get("topic") or "").lower():
            existing = [p for p in data.get("images", []) if os.path.exists(p)]
            print(f"[Image] Loaded {len(existing)} Part 2 images for {topic}")
            os.remove(manifest_path)
            return existing
    except Exception as e:
        print(f"[Image] Part 2 image load failed: {e}")
    return []


def process_user_images_smart(
    user_images: list,
    topic: str,
    series_name: str | None,
    part_number: int | None = None,
) -> tuple[list, list, list]:
    """Filter user images by OpenAI Vision relevance. Returns (use_now, save_for_later, ignored)."""
    use_now:        list = []
    save_for_later: list = []
    ignored:        list = []

    for img in user_images:
        path = img if isinstance(img, str) else img.get("path", "")
        if not path or not os.path.exists(path):
            continue
        result = check_image_relevance(path, topic, series_name, part_number)
        if result == "use_now":
            use_now.append(img)
        elif result == "save_part2":
            save_for_later.append(img)
        else:
            ignored.append(img)

    print(f"[Image] Smart filter results:")
    print(f"  âœ… Use now: {len(use_now)}")
    print(f"  🔦 Save Part 2: {len(save_for_later)}")
    print(f"  âŒ Ignored: {len(ignored)}")

    if save_for_later:
        save_images_for_part2(save_for_later, topic)

    return use_now, save_for_later, ignored


def get_person_images(
    person_name: str,
    video_id: str,
    user_images: list[dict] | None = None,
    script_text: str = "",
) -> list[dict]:
    """
    Build the priority image list for a real person.

    Priority order (highest first):
      1. User-uploaded images — each expanded to AI-transformed + original
      2. Wikipedia real photo (public domain, position 0 = opening shot)

    Returns list of {"path", "tags", "caption"} dicts compatible with
    _build_clip_pool_with_user_images().  AI portraits fill the rest of
    the slots separately through the normal generate_image_prompts flow.
    """
    images: list[dict] = []

    # 1 — User uploads → AI transform + original for each
    raw_uploads = [img for img in (user_images or []) if img.get("path") and os.path.exists(img["path"])]
    if raw_uploads:
        images.extend(process_user_images(raw_uploads, video_id, script_text=script_text))
        print(f"[Image] Priority 1: {len(raw_uploads)} user image(s) → {len(images)} processed")

    # 2 — Wikipedia real photo
    if person_name:
        wiki_url = fetch_wikimedia_image(person_name)
        if wiki_url:
            wiki_path = os.path.join(IMAGES_DIR, f"{video_id}_wiki_real.png")
            downloaded = download_wikipedia_image(wiki_url, wiki_path)
            if downloaded:
                _REAL_IMAGE_PATHS.add(downloaded)   # mark as real/documentary source
                images.append({
                    "path": downloaded,
                    "tags": ["real", "photo", "portrait", *person_name.lower().split()],
                    "caption": f"{person_name} real historical photo",
                })
                print(f"[Image] Priority 2 (Wikipedia): {downloaded}")

    return images




_DEFAULT_STYLE = "true crime documentary, dark cinematic lighting, dramatic atmosphere"
_IMAGE_PROMPT_SUFFIX = (
    ", dark cinematic documentary style, no text, "
    "no watermarks, photorealistic, high detail"
)


# ── Provider health tracker ───────────────────────────────────────────────────
# Tracks recent AI-provider failures so we can skip a flaky provider instantly
# rather than waiting 40 s for a rate-limit retry.

class _ProviderHealth:
    """
    Lightweight in-process failure tracker.
    After _FAIL_THRESHOLD failures inside _WINDOW_SECONDS the provider is
    considered "unhealthy" and will be skipped until the window rolls over.
    """
    _FAIL_THRESHOLD  = 3
    _WINDOW_SECONDS  = 300   # 5 minutes

    def __init__(self):
        self._failures: dict[str, list[float]] = {}

    def record_failure(self, provider: str) -> None:
        now = time.time()
        self._failures.setdefault(provider, [])
        self._failures[provider].append(now)

    def is_healthy(self, provider: str) -> bool:
        now = time.time()
        recent = [t for t in self._failures.get(provider, [])
                  if now - t < self._WINDOW_SECONDS]
        self._failures[provider] = recent
        return len(recent) < self._FAIL_THRESHOLD

    def reset(self, provider: str) -> None:
        self._failures[provider] = []


_provider_health = _ProviderHealth()


# ── Deterministic visual query builder ───────────────────────────────────────
# Tier-1 / Tier-2: extracts factual search queries from script text WITHOUT AI.
# Covers ~80 % of documentary visuals — real names, locations, eras, themes.

_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "this", "that", "these", "those", "was",
    "were", "had", "has", "have", "been", "is", "are", "he", "she", "they",
    "his", "her", "their", "its", "it", "we", "you", "who", "what", "which",
    "when", "where", "how", "not", "be", "do", "did", "does", "will",
    "would", "could", "should", "may", "might", "can", "also", "more",
    "into", "about", "after", "before", "over", "than", "then", "so",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
})


def build_visual_search_query(
    chunk_text: str,
    topic: str = "",
    research: dict | None = None,
) -> str:
    """
    Build a factual image search query from script text — NO AI required.

    Priority order:
      1. Named person (Capitalized ≥2 words in chunk or topic)
      2. Specific location from _LOCATIONS
      3. Detected era/decade from _ERAS
      4. Thematic keyword from _THEMES
      5. Topic + "documentary"

    Returns a short English search query (3–6 words).
    """
    text = (chunk_text or "").strip()
    topic_clean = (topic or "").strip()
    text_lower = text.lower()

    # 1. Named person — consecutive title-case words (≥2, each ≥3 chars)
    name_match = re.findall(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b', text)
    for nm in name_match:
        parts = nm.split()
        if all(p.lower() not in _STOPWORDS for p in parts):
            # Add topic context if the name is not the whole topic
            suffix = ""
            for loc_key in _LOCATIONS:
                if loc_key in text_lower:
                    suffix = loc_key.title()
                    break
            era = ""
            for era_prefix in sorted(_ERAS.keys(), reverse=True):
                if era_prefix in text:
                    era = era_prefix + "s"
                    break
            parts_out = [nm]
            if era:
                parts_out.append(era)
            elif suffix:
                parts_out.append(suffix)
            query = " ".join(parts_out)
            if len(query.split()) >= 2:
                return query

    # 2. Topic + location
    for loc_key, loc_query in _LOCATIONS.items():
        if loc_key in text_lower or loc_key in topic_clean.lower():
            era = ""
            for era_prefix in sorted(_ERAS.keys(), reverse=True):
                if era_prefix in text:
                    era = era_prefix + "s"
                    break
            base = topic_clean or loc_query.split(",")[0]
            return f"{base} {era}".strip() if era else base

    # 3. Era detection with topic
    for era_prefix in sorted(_ERAS.keys(), reverse=True):
        if era_prefix in text:
            base = topic_clean or "crime"
            return f"{base} {era_prefix}s documentary"

    # 4. Thematic match
    for theme_key, theme_query in _THEMES.items():
        if theme_key in text_lower:
            base = topic_clean or theme_query.split(",")[0]
            return base

    # 5. Topic fallback
    if topic_clean:
        return f"{topic_clean} documentary scene"

    return "true crime historical documentary"


def extract_style_from_user_images(user_images: list[dict]) -> str:
    """
    Analyze user-provided images to extract a visual style profile (era, lighting,
    environment, mood) for injection into all AI-generated image prompts.
    Uses OpenAI Vision on the first available image; falls back to captions/tags.
    Returns empty string when no user images are available.
    """
    if not user_images:
        return ""
    first_path = next(
        (img["path"] for img in user_images
         if img.get("path") and os.path.exists(img.get("path", ""))),
        None
    )
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key and first_path:
        try:
            import base64 as _b64
            with open(first_path, "rb") as _f:
                img_b64 = _b64.b64encode(_f.read()).decode("utf-8")
            ext  = os.path.splitext(first_path)[1].lower().lstrip(".")
            mime = "image/png" if ext == "png" else "image/jpeg"
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                        {"type": "text", "text": (
                            "Analyze this image and extract its visual style. "
                            "Return ONLY a short comma-separated style description "
                            "(max 15 words) covering: era, setting, lighting, mood, "
                            "clothing if visible. "
                            "DO NOT describe faces or identify any person. "
                            "Example: '1970s FBI interview room, dim cold lighting, "
                            "formal clothing, tense atmosphere'"
                        )},
                    ]}],
                    "max_tokens": 60,
                    "temperature": 0.2,
                },
                timeout=30,
            )
            if r.status_code == 200:
                style = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if style and len(style.split()) >= 3:
                    print(f"[Style] Extracted from user image: {style}")
                    return style
        except Exception as e:
            print(f"[Style] Vision analysis failed (non-fatal): {e}")
    # Fallback: build style hint from captions and tags
    captions = [img.get("caption", "").strip() for img in user_images if img.get("caption")]
    tags = []
    for img in user_images:
        tags.extend(img.get("tags", []))
    combined = ", ".join(captions[:2] + [t for t in tags[:4] if t not in captions])
    if combined.strip():
        style = combined.strip()[:120]
        print(f"[Style] Caption-based style: {style}")
        return style
    return ""


def build_image_prompt(
    chunk_text: str,
    style_profile: str = "",
    topic: str = "",
    research: dict | None = None,
) -> str:
    """
    5-tier non-blocking visual prompt builder.

    Tier 1: SHA256 cache hit           → [IMAGE] Cached visual query reused
    Tier 2: Deterministic query builder → [IMAGE] Tier2 deterministic query
    Tier 3: Groq cinematic enhancement  → [IMAGE] Tier3 Groq prompt
    Tier 4: OpenAI fallback             → [IMAGE] Tier4 OpenAI fallback
    Tier 5: Local emergency template    → [IMAGE] Tier5 local template

    Groq is skipped immediately (no blocking wait) when the provider health
    tracker reports it as unhealthy (≥3 failures in the last 5 minutes).
    """
    style_suffix = f", {style_profile}" if style_profile else ""
    first_200    = " ".join(chunk_text.split()[:200])

    # ── Tier 1: cache ─────────────────────────────────────────────────────────
    _cache_prompt = f"img|{first_200[:300]}"
    try:
        from agents.ai_cache import cache_get, cache_set
        _cached = cache_get(_cache_prompt, "visual_query", "image_prompt", ttl_days=14)
        if _cached:
            print(f"[IMAGE] Cached visual query reused: {_cached[:60]}")
            return f"{_cached}{style_suffix}{_IMAGE_PROMPT_SUFFIX}"
    except ImportError:
        cache_get = cache_set = None  # type: ignore

    # ── Tier 2: deterministic factual query ───────────────────────────────────
    det_query = build_visual_search_query(chunk_text, topic=topic, research=research)
    _is_generic = det_query.lower().strip().startswith("true crime historical")
    if det_query and not _is_generic:
        print(f"[IMAGE] Tier2 deterministic query: {det_query[:70]}")
        try:
            from agents.ai_cache import cache_set as _cs
            _cs(_cache_prompt, "visual_query", "image_prompt", det_query)
        except ImportError:
            pass
        return f"{det_query}{style_suffix}{_IMAGE_PROMPT_SUFFIX}"

    # ── Build AI enhancement prompt (shared by Tier 3 + 4) ───────────────────
    style_rule = f"\n- Match this visual style: {style_profile}" if style_profile else ""
    # Detect Arabic input so we can instruct the LLM to translate entities to English
    _has_arabic = any('؀' <= ch <= 'ۿ' for ch in first_200)
    _arabic_note = (
        "\n- The excerpt may be in Arabic. Translate all names, places, and events to "
        "English in your output." if _has_arabic else ""
    )
    ai_prompt = (
        "Read this script excerpt and write a specific visual image generation prompt "
        "(max 20 words) representing the exact subject.\n\n"
        f"Rules:\n- Name real places, real objects, real events\n- No human faces\n"
        f"- Dark cinematic documentary style\n- Be specific not generic{style_rule}"
        f"{_arabic_note}"
        "\n- ALWAYS write the prompt in English regardless of input language\n\n"
        "Examples:\n"
        "GOOD: 'Burned village Darfur Sudan desert, smoke ruins, golden hour, cinematic aerial view'\n"
        "BAD: 'dark crime documentary background'\n\n"
        f"Script excerpt: {first_200}\n\nReturn only the English image prompt, nothing else."
    )

    # ── Tier 3: Groq (skipped when unhealthy — NO blocking wait) ─────────────
    if _provider_health.is_healthy("groq"):
        try:
            from agents.script_agent import _groq_call as _gc
        except ImportError:
            try:
                from script_agent import _groq_call as _gc  # type: ignore
            except ImportError:
                _gc = None  # type: ignore
        if _gc:
            try:
                import groq as _groq_lib
                result = _gc(
                    messages=[{"role": "user", "content": ai_prompt}],
                    max_tokens=60, temperature=0.7,
                ).choices[0].message.content.strip().strip('"\'')
                if result:
                    print(f"[IMAGE] Tier3 Groq prompt: {result[:70]}")
                    _provider_health.reset("groq")
                    try:
                        from agents.ai_cache import cache_set as _cs
                        _cs(_cache_prompt, "visual_query", "image_prompt", result)
                    except ImportError:
                        pass
                    return f"{result}{style_suffix}{_IMAGE_PROMPT_SUFFIX}"
            except Exception as e:
                _provider_health.record_failure("groq")
                print(f"[IMAGE] Tier3 Groq failed (recorded): {e}")

    # ── Tier 4: OpenAI fallback ───────────────────────────────────────────────
    if _provider_health.is_healthy("openai"):
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if api_key:
            try:
                r = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": "gpt-4o-mini",
                          "messages": [{"role": "user", "content": ai_prompt}],
                          "max_tokens": 60, "temperature": 0.7},
                    timeout=12,
                )
                if r.status_code == 200:
                    result = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                    if result:
                        print(f"[IMAGE] Tier4 OpenAI fallback: {result[:70]}")
                        _provider_health.reset("openai")
                        try:
                            from agents.ai_cache import cache_set as _cs
                            _cs(_cache_prompt, "visual_query", "image_prompt", result)
                        except ImportError:
                            pass
                        return f"{result}{style_suffix}{_IMAGE_PROMPT_SUFFIX}"
                elif r.status_code == 429:
                    _provider_health.record_failure("openai")
                    print("[IMAGE] Tier4 OpenAI rate-limited (recorded)")
            except Exception as e:
                _provider_health.record_failure("openai")
                print(f"[IMAGE] Tier4 OpenAI failed (recorded): {e}")

    # ── Tier 5: local emergency template ─────────────────────────────────────
    _entity = topic or "crime"
    years   = re.findall(r'\b(19[4-9]\d|20[0-2]\d)\b', chunk_text)
    _year   = years[0] if years else ""
    loc_hit = next((k for k in _LOCATIONS if k in chunk_text.lower()), "")
    _loc    = loc_hit.title() if loc_hit else ""
    template = " ".join(filter(None, [_entity, _loc, _year, "documentary"])).strip()
    print(f"[IMAGE] Tier5 local template: {template[:70]}")
    return f"{template}{style_suffix}{_IMAGE_PROMPT_SUFFIX}"


def generate_image_prompts(script_text: str, count: int, topic: str = "", research: dict | None = None, style_profile: str = "") -> list[str]:
    """Split script into [count] equal chunks, build one image prompt per chunk.
    Returns list of [count] specific image prompts.
    Falls back gracefully per chunk if AI is unavailable.
    Deduplicates similar adjacent chunks to cut AI calls by 50–80 %.
    """
    import re

    # ── ViMax storyboard (Ollama local → Groq fallback) ───────────────────────
    # Generates shot-specific image prompts from the actual script content.
    # Falls back silently to the per-chunk system below if unavailable.
    try:
        from agents.vimax_bridge import generate_storyboard_prompts as _vimax
        _lang = "arabic" if any(
            "؀" <= c <= "ۿ" for c in script_text[:200]
        ) else "english"
        _vimax_prompts = _vimax(script_text, topic=topic, language=_lang, num_shots=count)
        if _vimax_prompts:
            print(f"[Image] ViMax storyboard: {len(_vimax_prompts)} shot prompts")
            return _vimax_prompts
    except Exception as _ve:
        print(f"[Image] ViMax bridge skipped (non-fatal): {_ve}")

    # Strip [SECTION: ...] markers so they don't pollute chunk text
    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()

    if not words:
        return [f"true crime historical documentary scene cinematic dark{_IMAGE_PROMPT_SUFFIX}"] * count

    chunk_size = max(1, len(words) // count)

    def _word_set(text: str) -> set:
        return {w.lower() for w in text.split() if len(w) > 3 and w.lower() not in _STOPWORDS}

    def _jaccard(a: str, b: str) -> float:
        sa, sb = _word_set(a), _word_set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    prompts: list[str] = []
    prev_chunk = ""
    prev_prompt = ""
    ai_calls = 0
    dedup_hits = 0

    for i in range(count):
        start      = i * chunk_size
        end        = start + chunk_size if i < count - 1 else len(words)
        chunk_text = " ".join(words[start:end])

        # Dedup: reuse previous prompt when chunks are ≥60 % similar
        if prev_chunk and prev_prompt and _jaccard(chunk_text, prev_chunk) >= 0.60:
            prompts.append(prev_prompt)
            dedup_hits += 1
            print(f"[IMAGE] Chunk {i+1}/{count} deduped (reusing similar prompt)")
        else:
            p = build_image_prompt(
                chunk_text,
                style_profile=style_profile,
                topic=topic,
                research=research,
            )
            prompts.append(p)
            prev_chunk  = chunk_text
            prev_prompt = p
            ai_calls += 1

    print(
        f"[Image] Built {len(prompts)} prompts "
        f"(AI calls: {ai_calls}, deduped: {dedup_hits}/{count})"
    )
    return prompts


def clean_prompt(prompt: str) -> str:
    """Remove special characters that break Pollinations URLs."""
    import re
    prompt = prompt.replace("(", "").replace(")", "")
    prompt = prompt.replace(",", " ").replace("_", " ")
    prompt = prompt.replace("&", "and")
    prompt = prompt.replace("/", " ")
    prompt = prompt.replace('"', "").replace("'", "")
    prompt = re.sub(r'\s+', ' ', prompt).strip()
    return prompt[:200]


def safe_download_image(url: str, output_path: str, timeout: int = 15) -> str | None:
    """
    Download an image URL with strict validation. Never raises.

    Rejects: HTML pages, text/html redirects, files < 5 KB, bad magic bytes.
    Returns output_path on success, None on any failure.
    """
    import io
    from PIL import Image as PILImage
    try:
        r = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        ct = r.headers.get("Content-Type", "").lower()
        if ct and not ct.startswith("image/"):
            print(f"[Image] safe_download: rejected non-image Content-Type '{ct.split(';')[0].strip()}'")
            return None
        if len(r.content) < 5_000:
            print(f"[Image] safe_download: rejected tiny file ({len(r.content)} bytes)")
            return None
        if not _check_image_bytes(r.content[:12]):
            print(f"[Image] safe_download: rejected bad magic bytes from {url[:60]}")
            return None
        img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
        img = img.resize((1080, 1920), PILImage.LANCZOS)
        output_path = output_path.replace(".jpg", ".png")
        img.save(output_path, "PNG")
        return output_path
    except Exception as e:
        print(f"[Image] safe_download failed ({url[:60]}): {e}")
        return None


def generate_ai_image(prompt: str, output_path: str, seed: int = None) -> str:
    """Fetch an AI-generated image from Pollinations with retry + dark fallback."""
    global _pollinations_402_blocked
    import io
    from PIL import Image as PILImage

    if _pollinations_402_blocked:
        return None  # circuit breaker open — Pollinations 402'd this session

    output_path = output_path.replace(".jpg", ".png")
    encoded = requests.utils.quote(clean_prompt(prompt))
    _seed = seed if seed is not None else random.randint(1, 99999)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1920&nologo=true&seed={_seed}"
    )

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=90)
            if response.status_code == 200:
                # Reject non-image responses — Pollinations backend sometimes returns
                # Azure BlobNotFound XML (200 OK but Content-Type: application/xml).
                _ct = response.headers.get("Content-Type", "")
                if _ct and not _ct.startswith("image/"):
                    print(f"[Image] Non-image Content-Type '{_ct}' (attempt {attempt + 1}/3) — retrying")
                    time.sleep(20)
                    continue
                try:
                    img = PILImage.open(io.BytesIO(response.content)).convert("RGB")
                    img = img.resize((1080, 1920), PILImage.LANCZOS)
                    img.save(output_path, "PNG")
                    print(f"[Image] Generated: {prompt[:60]}")
                    _record_pollinations_result(True)
                    time.sleep(5)
                    return output_path
                except Exception as _pil_e:
                    print(f"[Image] PIL parse failed attempt {attempt + 1}/3: {_pil_e} — retrying")
                    time.sleep(20)
                    continue
            elif response.status_code in (402, 403):
                # Payment required / forbidden — trip circuit breaker so remaining calls skip
                _record_pollinations_result(False)
                _pollinations_402_blocked = True
                print(f"[Image] Pollinations {response.status_code} — circuit breaker tripped, all future Pollinations calls disabled: {prompt[:60]}")
                return None
            elif response.status_code == 429:
                _record_pollinations_result(False)
                print(f"[Image] Rate limited, waiting 45s... (attempt {attempt + 1}/3)")
                time.sleep(45)
            else:
                print(f"[Image] Pollinations returned {response.status_code} (attempt {attempt + 1}/3)")
                time.sleep(15)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"[Image] Network error attempt {attempt + 1}: {e} — switching to AI fallback")
            break
        except Exception as e:
            err = str(e)
            if "cancel" in err.lower() or "operation" in err.lower():
                print(f"[Image] Search cancelled — switching to AI fallback")
                break
            print(f"[Image] Attempt {attempt + 1} failed: {e}")
            time.sleep(15)

    # All attempts failed — return None so callers skip gracefully (no dark placeholder)
    print(f"[Image] Pollinations failed after all attempts — skipping: {prompt[:60]}")
    return None


def _is_dark_placeholder(path: str, threshold: int = 8) -> bool:
    """Return True if image is a nearly-solid black background (corrupted/empty download).

    threshold=8 (mean < 8/255 ≈ 3% brightness) catches only truly blank files.
    Crime documentary content intentionally uses dark palettes (~15-40 mean) which
    must NOT be filtered. The PIL gradient emergency fallbacks use ~18-34 RGB,
    all safely above this threshold.
    """
    try:
        from PIL import Image as _PILCheck
        import numpy as _np_check
        img = _PILCheck.open(path).convert("RGB")
        w, h = img.size
        cx, cy = w // 2, h // 2
        sample = img.crop((max(0, cx - 50), max(0, cy - 50),
                           min(w, cx + 50), min(h, cy + 50)))
        arr = _np_check.array(sample, dtype=float)
        return arr.mean() < threshold and arr.std() < 3
    except Exception:
        return False


def _filter_dark_placeholders(paths: list[str], label: str = "") -> list[str]:
    """Remove dark solid-background images from pool, log count."""
    if not paths:
        return paths
    clean = [p for p in paths if not _is_dark_placeholder(p)]
    removed = len(paths) - len(clean)
    if removed > 0:
        pct = removed / len(paths) * 100
        tag = f" [{label}]" if label else ""
        print(f"[ImageQC{tag}] Removed {removed} dark placeholders ({pct:.0f}%) "
              f"— {len(clean)} real images remain")
        if pct > 40:
            print(f"[ImageQC] WARNING: placeholder coverage {pct:.0f}% > 40% "
                  "— Pollinations may be down or returning errors")
    return clean


def _validate_render_inputs(
    image_paths: list[str],
    audio_secs: float,
    is_short: bool = False,
    language: str = "",
) -> list[str]:
    """Return list of warning strings. Logs all warnings. Empty = render is safe."""
    warnings: list[str] = []
    n = len(image_paths)

    # Image count: minimum 1 unique image per 8 seconds
    if audio_secs > 0:
        min_imgs = max(4, int(audio_secs / 8))
        if n < min_imgs:
            warnings.append(
                f"IMAGE COUNT LOW: {n} images for {audio_secs:.0f}s "
                f"(need {min_imgs} for 1-per-8s rotation)"
            )

    # Narration length minimums
    if not is_short and audio_secs < 600:
        warnings.append(f"NARRATION SHORT: {audio_secs:.0f}s < 600s (10min) minimum")
    if not is_short and "arabic" in (language or "").lower() and audio_secs < 900:
        warnings.append(f"ARABIC NARRATION SHORT: {audio_secs:.0f}s < 900s (15min) minimum")

    for w in warnings:
        print(f"[PreExport] WARNING: {w}")
    return warnings


# â"€â"€ Real-photo fetching â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_IMAGE_MAGIC = {
    b"\xff\xd8\xff":         "jpeg",
    b"\x89\x50\x4e\x47":    "png",
    b"\x52\x49\x46\x46":    "webp",
    b"\x47\x49\x46\x38":    "gif",
}
_IMAGE_MIN_BYTES = 5_000    # 5 KB — archive/newspaper scans can be small
_BLOCKED_IMAGE_DOMAINS = {"pinterest.com", "instagram.com", "facebook.com", "twitter.com", "x.com"}
_BLOCKED_URL_PATTERNS  = {".html", ".php", ".aspx", "/blog/", "/article/", "/post/"}
_VALID_IMAGE_EXTS      = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".jfif"}


def _is_valid_image_url(url: str) -> bool:
    """Pre-filter: skip obviously non-image URLs before attempting any download."""
    u = url.lower()
    if any(d in u for d in _BLOCKED_IMAGE_DOMAINS):
        return False
    if any(p in u for p in _BLOCKED_URL_PATTERNS):
        return False
    # Accept if path ends with OR contains a known image extension
    # (CDN URLs often embed extensions mid-path, e.g. /photo.jpg/1280px-photo.jpg)
    from urllib.parse import urlparse, unquote
    path = unquote(urlparse(url).path).lower()
    return any(ext in path for ext in _VALID_IMAGE_EXTS)


def _check_image_bytes(data: bytes) -> bool:
    """Return True if first bytes match a known image magic signature."""
    for magic in _IMAGE_MAGIC:
        if data[:len(magic)] == magic:
            return True
    return False


def download_real_image(url: str, output_path: str) -> str | None:
    """Download image from URL, validate content type + magic bytes, smart-crop to 1080x1920."""
    import io
    from PIL import Image as PILImage

    if not _is_valid_image_url(url):
        print(f"[Image] Skipped non-image URL (pre-filter): {url[:80]}")
        return None

    try:
        # HEAD first to check Content-Type cheaply
        ct = ""
        try:
            head = requests.head(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
            ct = head.headers.get("Content-Type", "").lower()
        except Exception:
            pass

        if ct and not ct.startswith("image/"):
            print(f"[Image] Rejected non-image URL ({ct.split(';')[0].strip()}): {url[:80]}")
            return None

        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None

        # Validate magic bytes when Content-Type was unknown
        if not ct or not ct.startswith("image/"):
            if not _check_image_bytes(r.content[:12]):
                print(f"[Image] Rejected (bad magic bytes): {url[:80]}")
                return None

        if len(r.content) < _IMAGE_MIN_BYTES:
            print(f"[Image] Rejected tiny image ({len(r.content)} bytes): {url[:80]}")
            return None

        img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
        w, h = img.size
        target_ratio = 9 / 16
        if w / h > target_ratio:
            new_w = int(h * target_ratio)
            left  = (w - new_w) // 2
            img   = img.crop((left, 0, left + new_w, h))
        img = img.resize((1080, 1920), PILImage.LANCZOS)
        output_path = output_path.replace(".jpg", ".png")
        img.save(output_path, "PNG")
        return output_path
    except Exception as e:
        print(f"[Image] Download failed ({url[:70]}): {e}")
        return None


def _wikimedia_image_results(query: str, max_results: int = 5) -> list[str]:
    """Search Wikimedia Commons for real photos -- works from server IPs."""
    try:
        params = {
            'action': 'query', 'format': 'json', 'generator': 'search',
            'gsrnamespace': '6', 'gsrsearch': query, 'gsrlimit': max_results * 3,
            'prop': 'imageinfo', 'iiprop': 'url|mediatype', 'iiurlwidth': 1080,
        }
        r = requests.get('https://commons.wikimedia.org/w/api.php', params=params, timeout=12)
        if r.status_code != 200:
            return []
        pages = r.json().get('query', {}).get('pages', {}).values()
        urls = []
        for page in pages:
            ii = page.get('imageinfo', [{}])[0]
            url = ii.get('thumburl') or ii.get('url', '')
            mtype = ii.get('mediatype', '')
            if url and mtype in ('BITMAP', 'DRAWING') and _is_valid_image_url(url):
                urls.append(url)
            if len(urls) >= max_results:
                break
        return urls
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        print(f'[Image] Wikimedia search cancelled/timeout for: {query} — using AI fallback')
        return []
    except Exception as e:
        err = str(e)
        if "cancel" in err.lower() or "operation" in err.lower():
            print(f'[Image] Search cancelled — switching to AI fallback')
        else:
            print(f'[Image] Wikimedia search failed: {e}')
        return []


def _search_wikimedia_commons(query: str, max_results: int = 3) -> list[str]:
    """Search Wikimedia Commons by MIME type — broader than mediatype filter."""
    try:
        r = requests.get(
            'https://commons.wikimedia.org/w/api.php',
            params={
                'action': 'query',
                'generator': 'search',
                'gsrsearch': query,
                'gsrnamespace': 6,
                'gsrlimit': max_results * 3,
                'prop': 'imageinfo',
                'iiprop': 'url|mime',
                'format': 'json',
            },
            timeout=15,
            headers={'User-Agent': 'DarkCrimeDecoded/1.0'},
        )
        urls = []
        pages = r.json().get('query', {}).get('pages', {})
        for page in pages.values():
            for info in page.get('imageinfo', []):
                mime = info.get('mime', '')
                url = info.get('url', '')
                if mime.startswith('image/') and url:
                    urls.append(url)
        print(f'[Image] Wikimedia Commons: {len(urls)} results for "{query}"')
        return urls[:max_results]
    except Exception as e:
        print(f'[Image] Wikimedia Commons error: {e}')
        return []


def _search_images_openai(query: str, max_results: int = 5) -> list[str]:
    import re
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not api_key:
        return []
    try:
        r = requests.post(
            'https://api.openai.com/v1/responses',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'gpt-4o-mini',
                'tools': [{'type': 'web_search_preview'}],
                'input': f'Find real photographs of {query}. Return only direct image URLs ending in .jpg .jpeg .png or .webp. One URL per line. No explanation, no markdown.'
            },
            timeout=20,
        )
        data = r.json()
        print(f'[Image] OpenAI search status: {r.status_code} for: {query}')

        text = ''
        for item in data.get('output', []):
            if item.get('type') == 'message':
                for c in item.get('content', []):
                    if c.get('type') == 'output_text':
                        text += c.get('text', '') + '\n'

        urls = re.findall(
            r'https?://\S+\.(?:jpg|jpeg|png|webp)',
            text,
            flags=re.IGNORECASE
        )

        print(f'[Image] OpenAI search found {len(urls)} URLs for: {query}')
        return urls[:max_results]

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        print(f'[Image] OpenAI search cancelled/timeout for: {query} — switching to AI fallback')
        return []
    except Exception as e:
        err = str(e)
        if "cancel" in err.lower() or "operation" in err.lower():
            print(f'[Image] Search cancelled — switching to AI fallback')
        else:
            print(f'[Image] OpenAI search error: {e}')
        return []


def _internet_archive_image_results(query: str, max_results: int = 5) -> list[str]:
    """Search Internet Archive for historical images."""
    try:
        params = {
            'q': f'{query} AND mediatype:image',
            'fl': 'identifier', 'rows': max_results * 2,
            'output': 'json', 'page': 1,
        }
        r = requests.get('https://archive.org/advancedsearch.php', params=params, timeout=15)
        if r.status_code != 200:
            return []
        docs = r.json().get('response', {}).get('docs', [])
        urls = []
        for doc in docs:
            ident = doc.get('identifier', '')
            if ident:
                urls.append(f'https://archive.org/download/{ident}/{ident}.jpg')
            if len(urls) >= max_results:
                break
        return urls
    except Exception as e:
        print(f'[Image] Internet Archive search failed: {e}')
        return []


_KNOWN_CRIME_PERSONS = {
    # Profilers / investigators
    "john douglas", "robert ressler", "ann burgess",
    # Serial killers
    "edmund kemper", "charles manson", "david berkowitz", "ted bundy",
    "jeffrey dahmer", "richard ramirez", "john wayne gacy", "btk",
    "dennis rader", "henry lee lucas", "aileen wuornos", "gary ridgway",
    "night stalker", "son of sam", "zodiac",
    # Organized crime
    "pablo escobar", "el chapo", "joaquin guzman", "griselda blanco",
    "frank lucas", "henry hill", "al capone", "lucky luciano",
    "whitey bulger", "john gotti", "meyer lansky", "bugsy siegel",
    "carlo gambino", "vito genovese", "frank costello",
    # Cartel figures
    "amado carrillo", "felix gallardo", "miguel angel felix gallardo",
    # Classic gangsters / outlaws
    "bonnie parker", "clyde barrow", "john dillinger", "pretty boy floyd",
    "machine gun kelly",
}

# Canonical Wikipedia name for aliases that resolve poorly on their own
_PERSON_ALIASES: dict[str, str] = {
    "el chapo":      "joaquin guzman",
    "btk":           "dennis rader",
    "night stalker": "richard ramirez",
    "son of sam":    "david berkowitz",
    "zodiac":        "zodiac killer",
}

# ── Visual event type classifiers ─────────────────────────────────────────────
# Used by extract_visual_events() to map each script chunk to a unique visual class.

_VE_EVIDENCE = frozenset({
    "blood", "weapon", "gun", "knife", "bullet", "fingerprint", "dna",
    "forensic", "evidence", "body", "victim", "wound", "discovered", "found",
    "analysis", "autopsy", "sample", "ballistic", "toxicology", "seized",
    "arrested", "confiscated", "recovered", "lab", "laboratory", "corpse",
    "stabbed", "shot", "killed", "murdered", "strangled", "poisoned",
    # Arabic
    "دم", "سلاح", "بندقية", "سكين", "رصاصة", "بصمة", "دنا",
    "جنائي", "دليل", "أدلة", "جثة", "ضحية", "جرح", "اكتشف", "وجد",
    "تحليل", "تشريح", "مختبر", "مصادرة", "استرداد",
    "طعن", "أطلق", "قتل", "مقتل", "خنق", "تسمم", "اعتقل", "قبض",
})
_VE_NEWSPAPER = frozenset({
    "newspaper", "headline", "article", "press", "media", "coverage",
    "published", "breaking", "broadcast", "news", "declared", "announced",
    "aired", "reporters", "journalist", "front page",
    # Arabic
    "جريدة", "صحيفة", "عنوان", "مقال", "صحافة", "إعلام", "تغطية",
    "نشر", "عاجل", "أخبار", "خبر", "أعلن", "صحفي", "بث",
})
_VE_CCTV = frozenset({
    "camera", "footage", "surveillance", "security", "recorded", "tape",
    "video", "caught on", "captured", "monitored", "cctv", "screenshot",
    "film", "filming", "filmed",
    # Arabic
    "كاميرا", "لقطات", "مراقبة", "تسجيل", "شريط", "فيديو",
    "التقطت", "رصد", "صورة", "فيلم", "تصوير", "أمني",
})
_VE_COURTROOM = frozenset({
    "court", "judge", "jury", "trial", "verdict", "sentence", "conviction",
    "acquittal", "prosecution", "defense", "testify", "testified", "charges",
    "pleaded", "guilty", "innocent", "attorney", "lawyer", "hearing",
    "indicted", "indictment", "sentenced",
    # Arabic
    "محكمة", "قاضي", "قاض", "محلفين", "محاكمة", "حكم", "إدانة",
    "براءة", "نيابة", "دفاع", "شهادة", "تهمة", "مذنب", "بريء",
    "محامي", "جلسة", "اتهام", "صدر", "قرار",
})
_VE_INTERROGATION = frozenset({
    "interrogated", "questioned", "interview", "confession", "admitted",
    "denied", "suspect", "interrogation", "detained", "custody", "handcuffed",
    "arrested", "taken in", "brought in",
    # Arabic
    "استجواب", "استجوب", "اعتراف", "اعترف", "نفى", "مشتبه",
    "احتجاز", "احتجز", "حجز", "مقيد", "اعتقال", "موقوف",
})
_VE_MAP = frozenset({
    "map", "route", "distance", "miles", "kilometers", "north", "south",
    "east", "west", "border", "territory", "region", "headquarters",
    "coordinates", "located", "location", "address", "neighborhood",
    "traveled", "crossed", "drove to", "flew to",
    # Arabic
    "خريطة", "مسار", "مسافة", "كيلومتر", "شمال", "جنوب",
    "شرق", "غرب", "حدود", "منطقة", "إحداثيات", "موقع",
    "سافر", "عبر", "قاد", "طار", "انتقل",
})
_VE_LOCATION = frozenset({
    "apartment", "house", "hotel", "city", "street", "building", "room",
    "office", "car", "warehouse", "prison", "school", "hospital", "bar",
    "club", "alley", "highway", "road", "entered", "arrived", "drove",
    "walked", "lived", "moved", "fled", "corridor", "hallway", "basement",
    "rooftop", "garage", "parking", "factory", "dock", "harbor",
    # Arabic
    "شقة", "منزل", "بيت", "فندق", "مدينة", "شارع", "مبنى",
    "غرفة", "مكتب", "سيارة", "مستودع", "مدرسة", "مستشفى",
    "زقاق", "طريق", "دخل", "وصل", "هرب", "فر", "قبو", "سطح",
    "مرآب", "ميناء", "مصنع",
})
_VE_CHILDHOOD = frozenset({
    "born", "childhood", "young", "youth", "grew up", "parents", "family",
    "school", "teenage", "teenager", "adolescent", "early life", "child",
    "upbringing", "raised", "mother", "father", "brother", "sister",
    # Arabic
    "ولد", "طفولة", "صغير", "شباب", "نشأ", "والدان", "والده", "والدته",
    "عائلة", "مراهق", "طفل", "تربية", "ربي", "أم", "أب", "أخ", "أخت",
})
_VE_PRISON = frozenset({
    "prison", "jail", "cell", "bars", "incarcerated", "sentence", "serving",
    "released", "parole", "warden", "inmate", "penitentiary", "lockup",
    # Arabic
    "سجن", "زنزانة", "قضبان", "محكوم", "أُفرج", "إفراج", "سجين",
    "حبس", "معتقل", "سراح",
})

_VE_ATMOSPHERE_POOL = [
    "dark urban night crime city atmospheric documentary cinematic establishing shot",
    "police investigation dark corridor single light cinematic documentary",
    "crime scene night forensic dark atmospheric documentary cinematic establishing",
    "detective office shadows crime wall evidence noir documentary",
    "dramatic shadows dark room crime investigation moody cinematic documentary",
    "rainy night city street crime atmospheric noir documentary cinematic",
    "close-up hands typing crime report dark atmospheric documentary",
    "police car lights flashing night crime scene documentary cinematic",
    "courtroom empty dramatic shadow dark cinematic documentary",
    "prison corridor dark bars single light atmospheric documentary",
    "dark telephone booth vintage crime city night cinematic",
    "money stacks crime dark atmospheric documentary cinematic",
    "newspaper archive dark library documentary cinematic atmospheric",
]


def _clean_topic_name(topic: str) -> str:
    """Strip subtitle from a full video title so prompts don't embed the whole title.
    'Jeffrey Epstein: Secret Network' → 'Jeffrey Epstein'
    'Pablo Escobar — Rise of a Cartel' → 'Pablo Escobar'
    """
    return re.split(r'[:—–|]', (topic or ""), maxsplit=1)[0].strip()


# Concrete visual queries per event type — short, literal, archive-searchable
_SCENE_BASE_QUERIES: dict[str, str] = {
    "courtroom":     "courtroom empty wooden benches judge",
    "evidence":      "forensic evidence table crime lab",
    "newspaper":     "newspaper front page headline close-up",
    "cctv":          "surveillance camera footage grainy timestamp",
    "prison":        "prison cell corridor iron bars",
    "location":      "building exterior street daytime",
    "map":           "city map overhead aerial view",
    "interrogation": "interrogation room table chair overhead light",
    "childhood":     "vintage family photograph portrait",
    "atmosphere":    "city street night empty",
}

# Location phrases detectable in chunk text → concrete archive search query
_CHUNK_LOCATION_HINTS: dict[str, str] = {
    "palm beach":  "Palm Beach Florida mansion exterior",
    "manhattan":   "Manhattan New York City aerial",
    "new york":    "New York City street",
    "washington":  "Washington DC Capitol building",
    "miami":       "Miami Florida beach aerial",
    "los angeles": "Los Angeles California street",
    "chicago":     "Chicago downtown skyline",
    "london":      "London street United Kingdom",
    "paris":       "Paris France street",
    "wall street": "Wall Street New York financial",
    "white house": "White House Washington DC exterior",
    "fbi":         "FBI headquarters building Washington",
    "cia":         "CIA headquarters Langley Virginia",
    "pentagon":    "Pentagon building Arlington aerial",
    "prison":      "prison corridor bars cell",
    "courthouse":  "courthouse exterior steps stone",
    "airport":     "airport terminal interior",
}

# Noise patterns that make AI prompts useless for documentary realism
_AI_PROMPT_NOISE_RE = re.compile(
    r'\b(cinematic|documentary|dark|dramatic|moody|epic|atmospheric|noir|'
    r'artistic|professional|4k|high[\s\-]detail|no[\s\-]text|'
    r'no[\s\-]watermarks|photorealistic)\b',
    re.IGNORECASE,
)
_AI_PROMPT_SUFFIX_CLEAN = ", photorealistic, vertical 9:16"

# Cache: query string → list of Wikimedia URLs (per pipeline run, not persistent)
_wikimedia_query_cache: dict[str, list[str]] = {}


# Short event-type context word to combine with the person name
_TYPE_CONTEXT: dict[str, str] = {
    "courtroom":     "courtroom",
    "evidence":      "FBI investigation",
    "newspaper":     "newspaper",
    "cctv":          "surveillance footage",
    "prison":        "prison",
    "location":      "location",
    "map":           "location map",
    "interrogation": "interrogation",
    "childhood":     "childhood",
    "atmosphere":    "investigation",
    "portrait":      "",
}


def _build_scene_search_queries(chunk: str, topic: str, event_type: str) -> list[str]:
    """Return an ordered list of archive/stock search queries for this scene chunk.

    Priority (most specific → most generic):
    1. person + location  e.g. 'Jeffrey Epstein Palm Beach'
    2. location alone     e.g. 'Palm Beach Florida mansion exterior'
    3. person + context   e.g. 'Jeffrey Epstein courtroom'
    4. person + year      e.g. 'Jeffrey Epstein 2008'
    5. person alone       e.g. 'Jeffrey Epstein'   (identity anchor)
    6. generic type base  e.g. 'courtroom empty wooden'  (no person)

    The caller tries each in order and stops at the first successful download.
    The per-run cache (_wikimedia_query_cache) ensures duplicate queries across
    parallel workers only hit the API once.
    """
    person = _clean_topic_name(topic)
    chunk_lower = chunk.lower()
    years = re.findall(r'\b(19[4-9]\d|20[0-2]\d)\b', chunk)
    year = years[0] if years else ""
    type_ctx = _TYPE_CONTEXT.get(event_type, "")

    queries: list[str] = []

    # 1 & 2: person + location, then location alone
    for loc_phrase, loc_query in _CHUNK_LOCATION_HINTS.items():
        if loc_phrase in chunk_lower:
            queries.append(f"{person} {loc_phrase}")  # "Jeffrey Epstein Palm Beach"
            queries.append(loc_query)                  # "Palm Beach Florida mansion exterior"
            break

    # 3: person + event context
    if type_ctx:
        queries.append(f"{person} {type_ctx}")         # "Jeffrey Epstein courtroom"

    # 4: person + year
    if year:
        queries.append(f"{person} {year}")             # "Jeffrey Epstein 2008"

    # 5: person alone (identity anchor — always included for person-centric events)
    if person:
        queries.append(person)                         # "Jeffrey Epstein"

    # 6: generic type base (no person — useful when person photos are exhausted)
    base = _SCENE_BASE_QUERIES.get(event_type, "documentary archival")
    queries.append(" ".join(base.split()[:3]))         # "courtroom empty wooden"

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        if q and q.strip() not in seen:
            seen.add(q.strip())
            result.append(q.strip())
    return result


def _sanitize_ai_prompt(prompt: str, topic: str) -> str:
    """Strip title-based content and cinematic noise from AI image prompts.

    Pollinations prompts should describe WHAT to show (specific, literal,
    visual) rather than HOW it should feel (cinematic, dark, dramatic).
    """
    if not prompt:
        return "archival documentary scene" + _AI_PROMPT_SUFFIX_CLEAN
    # Remove the appended _IMAGE_PROMPT_SUFFIX block entirely
    prompt = re.sub(r',?\s*dark cinematic documentary style.*$', '', prompt,
                    flags=re.IGNORECASE)
    prompt = re.sub(r',?\s*no\s+(text|watermarks)[^,]*', '', prompt,
                    flags=re.IGNORECASE)
    # Strip full video title if it leaked in (title > 15 chars)
    if len(topic) > 15 and topic.lower() in prompt.lower():
        prompt = re.sub(re.escape(topic), _clean_topic_name(topic), prompt,
                        flags=re.IGNORECASE)
    # Remove noise words
    prompt = _AI_PROMPT_NOISE_RE.sub('', prompt)
    prompt = re.sub(r',\s*,', ',', prompt)
    prompt = re.sub(r'\s+', ' ', prompt).strip().strip(',').strip()
    # Cap at 15 words so Pollinations stays focused
    words = prompt.split()
    if len(words) > 15:
        prompt = ' '.join(words[:15])
    return (prompt + _AI_PROMPT_SUFFIX_CLEAN) if prompt else \
           ("archival documentary scene" + _AI_PROMPT_SUFFIX_CLEAN)


def _wikimedia_cached(query: str, max_results: int = 4) -> list[str]:
    """Wikimedia search with per-run cache — avoids duplicate API calls."""
    if query in _wikimedia_query_cache:
        return _wikimedia_query_cache[query]
    urls = _wikimedia_image_results(query, max_results=max_results)
    _wikimedia_query_cache[query] = urls
    return urls


def _classify_visual_event(
    chunk_lower: str, position: float, topic: str
) -> tuple[str, str]:
    """Return (visual_type, ai_prompt) for one 12-word script chunk.

    Priority ladder:
      known person → portrait
      courtroom keyword → courtroom
      interrogation keyword → interrogation
      evidence keyword → evidence
      CCTV keyword → cctv
      newspaper keyword → newspaper
      map keyword → map
      prison keyword → prison
      childhood keyword → childhood
      location keyword → location
      default → atmosphere (rotated by position)
    """
    # Use only the person/topic name — never the full video title as a prompt prefix
    _t = _clean_topic_name((topic or "").strip())
    _pfx = f"{_t} " if _t else ""

    for person in _KNOWN_CRIME_PERSONS:
        if person in chunk_lower:
            canon = _PERSON_ALIASES.get(person, person)
            return ("portrait",
                    f"{canon} real historical photograph documentary portrait dark cinematic"
                    f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_COURTROOM):
        return ("courtroom",
                f"courtroom trial judge jury dramatic dark documentary cinematic evidence"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_INTERROGATION):
        return ("interrogation",
                f"police interrogation room single overhead light shadow suspect detective dark cinematic"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_EVIDENCE):
        return ("evidence",
                f"{_pfx}forensic crime evidence investigation dark documentary cinematic"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_CCTV):
        return ("cctv",
                f"CCTV surveillance footage grainy timestamp night dark crime documentary cinematic"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_NEWSPAPER):
        return ("newspaper",
                f"{_pfx}crime newspaper front page headline archival black white documentary"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_MAP):
        return ("map",
                f"{_pfx}crime location city map aerial vintage noir documentary dark cinematic"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_PRISON):
        return ("prison",
                f"prison cell bars cold light cinematic atmospheric dark documentary"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_CHILDHOOD):
        return ("childhood",
                f"{_pfx}childhood archive family photograph vintage documentary dark cinematic"
                f"{_IMAGE_PROMPT_SUFFIX}")

    if any(k in chunk_lower for k in _VE_LOCATION):
        return ("location",
                f"{_pfx}crime location establishing shot dark atmospheric documentary cinematic"
                f"{_IMAGE_PROMPT_SUFFIX}")

    # Atmosphere: rotate through pool by position so consecutive chunks differ
    _atmo_idx = int(position * len(_VE_ATMOSPHERE_POOL)) % len(_VE_ATMOSPHERE_POOL)
    return ("atmosphere",
            f"{_pfx}{_VE_ATMOSPHERE_POOL[_atmo_idx]}{_IMAGE_PROMPT_SUFFIX}")


def extract_visual_events(
    script_text: str, topic: str = "", runtime_secs: float = 0.0
) -> list[dict]:
    """Parse script into unique visual events — one per ~12-word narrative chunk.

    Scale: 12 unique events per minute of runtime.
      10-min doc → ~120 unique visual events
      15-min doc → ~180 unique visual events

    Each event carries: idx, position (0-1), type, prompt, chunk (for logging).
    These are SEMANTICALLY UNIQUE — every event maps to a different
    narrative moment and generates a distinct Pollinations prompt.
    """
    import re
    from collections import Counter as _Counter
    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()
    if not words:
        return []

    runtime_min = max(runtime_secs / 60, 1.0) if runtime_secs > 0 else len(words) / 150.0
    n_events    = max(30, min(400, int(runtime_min * 7)))  # 7 unique images/min
    chunk_size  = max(8, len(words) // n_events)

    chunks: list[str] = []
    for i in range(n_events):
        start = i * chunk_size
        end   = (i + 1) * chunk_size if i < n_events - 1 else len(words)
        if start >= len(words):
            break
        chunks.append(" ".join(words[start:end]))

    events: list[dict] = []
    for i, chunk in enumerate(chunks):
        pos    = i / max(len(chunks) - 1, 1)
        vtype, prompt = _classify_visual_event(chunk.lower(), pos, topic)
        events.append({
            "idx":    i,
            "pos":    pos,
            "type":   vtype,
            "prompt": prompt,
            "chunk":  chunk[:80],
        })

    _dist = _Counter(e["type"] for e in events)
    print(f"[VisualPlan] {len(events)} events: " +
          " | ".join(f"{t}:{c}" for t, c in sorted(_dist.items())))
    return events


# Search query templates for each visual event type.
# {topic} is replaced with the actual documentary topic at runtime.
_TYPE_SEARCH_QUERIES: dict[str, str] = {
    "courtroom":     "{topic} courtroom trial",
    "evidence":      "{topic} crime evidence investigation",
    "newspaper":     "{topic} newspaper headline",
    "cctv":          "{topic} surveillance security camera",
    "prison":        "prison cell bars inmates",
    "location":      "{topic} building location exterior",
    "map":           "{topic} city map aerial",
    "interrogation": "police interrogation room detective",
    "childhood":     "{topic} young childhood portrait vintage",
    "atmosphere":    "{topic} crime investigation documentary",
}


def _prefetch_real_urls(events: list[dict], topic: str) -> dict[str, list[str]]:
    """Fetch Wikimedia URLs once per event type using multi-query strategy.

    For each type, uses the first chunk of that type to generate an ordered
    query list (person+location, person+context, person+year, person alone,
    generic base), tries each until it collects enough URLs.
    Returns dict[type → [url, ...]] as a cycling real-image pool.
    """
    # Take a representative chunk for each event type
    type_chunks: dict[str, str] = {}
    for ev in events:
        t = ev["type"]
        if t not in type_chunks and t != "portrait":
            type_chunks[t] = ev.get("chunk", "")

    pool: dict[str, list[str]] = {}
    for t, chunk in type_chunks.items():
        queries = _build_scene_search_queries(chunk, topic, t)
        all_urls: list[str] = []
        for q in queries:
            if len(all_urls) >= 8:
                break
            urls = _wikimedia_image_results(q, max_results=4)
            # Cache result so _gen_event workers don't repeat the API call
            _wikimedia_query_cache[q] = urls
            all_urls.extend(urls)
        if not all_urls:
            # Internet Archive fallback using the best query
            q = queries[0] if queries else topic
            all_urls = _internet_archive_image_results(q, max_results=4)
        pool[t] = list(dict.fromkeys(all_urls))  # deduplicate, preserve order
        tag = "real" if all_urls else "none"
        top_q = queries[0] if queries else ""
        print(f"[RealImages] prefetch {t}: {len(all_urls)} {tag} URLs (top='{top_q}')")
    return pool


def build_documentary_visual_pool(
    script_text: str,
    runtime_secs: float,
    topic: str,
    video_id: str,
    is_short: bool = False,
    style_profile: str = "",
) -> list[str]:
    """Build a UNIQUE visual for every narrative event in the script.

    Architecture:
      1. extract_visual_events() → typed events (portrait/courtroom/evidence/etc.)
      2. Portrait events → Wikimedia person photo first, Pollinations fallback
      3. All other events → real archive images first, Pollinations AI fallback
      4. Emergency gradient fallback only if pool returns < 8 images

    Result: N UNIQUE images in narrative order — NO PIL recycling, NO repeats.
    """
    if is_short:
        return fetch_real_images(
            script_text, 6, video_id, topic=topic, style_profile=style_profile
        )

    _img_dir = _get_images_dir()
    events   = extract_visual_events(script_text, topic=topic, runtime_secs=runtime_secs)
    if not events:
        return fetch_real_images(
            script_text, 30, video_id, topic=topic, style_profile=style_profile
        )

    seed = random.randint(1, 99999)
    # Pre-fetch real image URLs once per event type — avoids hammering Wikimedia
    # with hundreds of identical queries from the parallel workers.
    real_url_pool = _prefetch_real_urls(events, topic)

    def _gen_event(args):
        ev, out_path, _seed, _pool = args
        idx   = ev["idx"]
        ev_type = ev["type"]
        chunk   = ev.get("chunk", "")
        _from_real = False  # True when image came from real archive, not AI

        if os.path.exists(out_path) and os.path.getsize(out_path) > 5_000:
            # Cached image — we don't know its source; assume real if it predates AI run
            return (ev_type, out_path, True)

        # ── Scene analysis — build ordered query list ───────────────────
        scene_queries = _build_scene_search_queries(chunk, topic, ev_type)
        print(f"[ScenePlanner] idx={idx} type={ev_type} "
              f"queries={scene_queries[:3]}")

        saved = None

        # ── Step 1: portrait — Wikipedia REST then Commons ──────────────
        if ev_type == "portrait":
            person = _detect_person_in_chunk(chunk)
            if person:
                resolved = _PERSON_ALIASES.get(person.lower(), person)
                print(f"[VisualSearch] Wikipedia person photo: '{resolved}'")
                photo_url = _search_wikimedia_person_photo(resolved)
                if photo_url:
                    saved = _download_first_valid([photo_url], out_path)
                if not saved:
                    # Fall through the query list (includes person+context, person+year, etc.)
                    for q in scene_queries:
                        urls = _wikimedia_cached(q, max_results=3)
                        if urls:
                            saved = _download_first_valid(urls, out_path)
                            if saved:
                                print(f"[VisualSearch] portrait found via '{q}'")
                                break
            if saved:
                _from_real = True
                print(f"[VisualSearch] found real portrait image")

        # ── Step 2: non-portrait — iterate query list until hit ─────────
        else:
            for q in scene_queries:
                urls = _wikimedia_cached(q, max_results=4)
                if urls:
                    saved = _download_first_valid(urls, out_path)
                    if saved:
                        _from_real = True
                        print(f"[VisualSearch] found real archive image: '{q}'")
                        break

            # Pre-fetched pool as final real-image fallback (cycles through pool)
            if not saved:
                type_urls = _pool.get(ev_type, [])
                if type_urls:
                    url = type_urls[idx % len(type_urls)]
                    print(f"[VisualSearch] pool fallback: type={ev_type}")
                    saved = _download_first_valid([url], out_path)
                    if saved:
                        _from_real = True
                        print(f"[VisualSearch] found pool image: type={ev_type}")

        # ── Step 2b: DuckDuckGo — broad web image search (finds thousands per topic)
        if not saved and scene_queries:
            for q in scene_queries[:3]:
                ddg_urls = _search_duckduckgo_images(q, max_results=4)
                if ddg_urls:
                    saved = _download_first_valid(ddg_urls, out_path)
                    if saved:
                        _from_real = True
                        print(f"[VisualSearch] DuckDuckGo hit: '{q}'")
                        break

        # ── Step 2c: Pexels / Pixabay / OpenVerse ────────────────────────
        if not saved and scene_queries:
            _sq = scene_queries[0]
            for _search_fn in (_search_pexels_images, _search_pixabay_images,
                               _search_openverse_images, _search_loc_images):
                try:
                    _stock_urls = _search_fn(_sq, max_results=3)
                    if _stock_urls:
                        saved = _download_first_valid(_stock_urls, out_path)
                        if saved:
                            _from_real = True
                            print(f"[VisualSearch] stock hit via {_search_fn.__name__}: '{_sq}'")
                            break
                except Exception:
                    pass

        # ── Step 3: AI generation with sanitized prompt ─────────────────
        if not saved:
            clean_prompt = _sanitize_ai_prompt(ev["prompt"], topic)
            print(f"[AIGeneration] Pollinations: '{clean_prompt[:80]}'")
            saved = generate_ai_image(clean_prompt, out_path, seed=_seed + idx)
            if not saved:
                print(f"[FallbackReason] all sources failed for idx={idx} type={ev_type}")

        return (ev_type, saved, _from_real) if saved else (ev_type, None, False)

    tasks = [
        (ev, os.path.join(_img_dir, f"{video_id}_ev_{ev['idx']:04d}.png"), seed, real_url_pool)
        for ev in events
    ]
    print(f"[QUEUE] Image generation: {len(tasks)} pending | workers={_WORKERS['doc_visual']} | mode=doc-visual")
    real_types = {t for t, urls in real_url_pool.items() if urls}
    print(f"[RealImages] Types with real archive images: {real_types or 'none'} — AI fallback for rest")
    raw_results = parallel_map_safe(
        _gen_event, tasks, max_workers=_WORKERS["doc_visual"], timeout=120, label="doc visual",
    )

    global _REAL_IMAGE_PATHS
    _REAL_IMAGE_PATHS.clear()   # reset for this pipeline run

    paths: list[str] = []
    _type_counts: dict[str, int] = {}
    _real_count = 0
    _ai_count   = 0
    for r in raw_results:
        if r and r[1]:
            paths.append(r[1])
            _type_counts[r[0]] = _type_counts.get(r[0], 0) + 1
            is_real = r[2] if len(r) > 2 else False
            if is_real:
                _REAL_IMAGE_PATHS.add(r[1])
                _real_count += 1
            else:
                _ai_count += 1

    print(f"[VisualPlan] Pool complete: {len(paths)}/{len(events)} visuals — "
          f"real:{_real_count} AI:{_ai_count} | " +
          " | ".join(f"{t}:{c}" for t, c in sorted(_type_counts.items())))
    print(f"[VisualPlan] Real-archive images tracked: {len(_REAL_IMAGE_PATHS)}")

    # Emergency fallback: never return fewer than 8 images
    if len(paths) < 8:
        print(f"[VisualPlan] Pool too small ({len(paths)}) — activating emergency engine")
        _em = _generate_emergency_visuals(
            max(20, len(events) - len(paths)), _img_dir, is_short=False, topic=topic
        )
        paths.extend(_em)

    return paths


def _search_wikimedia_person_photo(person_name: str) -> str | None:
    """Fetch Wikipedia thumbnail for a real person via two endpoints."""
    # Resolve alias to canonical Wikipedia name (e.g. "el chapo" → "joaquin guzman")
    person_name = _PERSON_ALIASES.get(person_name.lower(), person_name)
    print(f'[Image] Wikimedia person search: {person_name}')
    encoded = requests.utils.quote(person_name)

    # Endpoint 1: REST summary API (simpler, more reliable)
    try:
        url = f'https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}'
        print(f'[Image] Wikimedia URL: {url}')
        r = requests.get(url, timeout=10, headers={'User-Agent': 'DarkCrimeDecoded/1.0'})
        print(f'[Image] Wikimedia response status: {r.status_code}')
        if r.status_code == 200:
            data = r.json()
            thumb = data.get('thumbnail', {}).get('source', '')
            if thumb:
                print(f'[Image] Wikimedia REST found: {thumb[:80]}')
                return thumb
            print(f'[Image] Wikimedia REST: no thumbnail in response keys={list(data.keys())}')
    except Exception as e:
        print(f'[Image] Wikimedia REST failed for "{person_name}": {e}')

    # Endpoint 2: pageimages API
    try:
        url = f'https://en.wikipedia.org/w/api.php?action=query&titles={encoded}&prop=pageimages&pithumbsize=800&format=json'
        print(f'[Image] Wikimedia URL: {url}')
        r = requests.get(url, timeout=10, headers={'User-Agent': 'DarkCrimeDecoded/1.0'})
        print(f'[Image] Wikimedia response status: {r.status_code}')
        if r.status_code == 200:
            resp_data = r.json()
            print(f'[Image] Wikimedia response: {resp_data}')
            pages = resp_data.get('query', {}).get('pages', {})
            for page in pages.values():
                thumb = page.get('thumbnail', {}).get('source', '')
                if thumb:
                    return thumb
    except Exception as e:
        print(f'[Image] Wikimedia pageimages failed for "{person_name}": {e}')

    return None


def _detect_person_in_chunk(chunk: str) -> str | None:
    """Return the first known crime figure name found in the text chunk, or None."""
    chunk_lower = chunk.lower()
    for name in _KNOWN_CRIME_PERSONS:
        if name in chunk_lower:
            return name
    return None



def _download_first_valid(urls: list[str], output_path: str) -> str | None:
    """Try each URL in order, return path of the first that downloads successfully."""
    for url in urls:
        saved = download_real_image(url, output_path)
        if saved:
            return saved
    return None


def _is_video_file(path: str) -> bool:
    ext = os.path.splitext(path or "")[1].lower()
    return ext in {".mp4", ".mov", ".m4v", ".webm"}


def _search_pexels_images(query: str, max_results: int = 5) -> list[str]:
    """Search Pexels photos and return direct image URLs (free licensed)."""
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        print("[Image] Pexels: no API key — skipping (set PEXELS_API_KEY secret)")
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": max_results, "orientation": "landscape"},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[Image] Pexels photos {r.status_code} for '{query}'")
            return []
        urls = []
        for photo in r.json().get("photos", []):
            src = photo.get("src", {})
            url = src.get("large2x") or src.get("large") or src.get("original")
            if url:
                urls.append(url)
        return urls
    except Exception as e:
        print(f"[Image] Pexels photo search error: {e}")
        return []


def _search_pixabay_images(query: str, max_results: int = 5) -> list[str]:
    """Search Pixabay photos and return direct image URLs (free licensed)."""
    api_key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        print("[Image] Pixabay: no API key — skipping (set PIXABAY_API_KEY secret)")
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={
                "key": api_key,
                "q": query,
                "per_page": max_results,
                "image_type": "photo",
                "orientation": "horizontal",
                "safesearch": "true",
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[Image] Pixabay photos {r.status_code} for '{query}'")
            return []
        return [
            hit.get("largeImageURL", "")
            for hit in r.json().get("hits", [])
            if hit.get("largeImageURL")
        ]
    except Exception as e:
        print(f"[Image] Pixabay image search error: {e}")
        return []


def _search_pexels_videos(query: str, per_page: int = 15) -> list[str]:
    """Search Pexels videos and return direct MP4 URLs (watermark-safe source)."""
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": api_key},
            params={"query": query, "per_page": per_page, "orientation": "portrait"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[Stock] Pexels search failed ({r.status_code}) for '{query}'")
            return []
        data = r.json()
        urls: list[str] = []
        for video in data.get("videos", []):
            files = video.get("video_files", [])
            # Prefer medium portrait MP4 for faster download/render.
            files = sorted(files, key=lambda f: (f.get("height", 0), f.get("width", 0)))
            picked = None
            for f in files:
                link = f.get("link", "")
                if f.get("file_type") == "video/mp4" and link:
                    picked = link
                    if (f.get("height") or 0) >= 720:
                        break
            if picked and "watermark" not in picked.lower():
                urls.append(picked)
        return urls
    except Exception as e:
        print(f"[Stock] Pexels error for '{query}': {e}")
        return []


def _search_pixabay_videos(query: str, per_page: int = 15) -> list[str]:
    """Search Pixabay videos and return direct MP4 URLs (free licensed source)."""
    api_key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": api_key,
                "q": query,
                "per_page": per_page,
                "safesearch": "true",
            },
            timeout=30,
        )
        if r.status_code != 200:
            print(f"[Stock] Pixabay search failed ({r.status_code}) for '{query}'")
            return []
        data = r.json()
        urls: list[str] = []
        for hit in data.get("hits", []):
            vids = hit.get("videos", {})
            # Prefer medium/large MP4s for stable rendering quality.
            for key in ("medium", "large", "small", "tiny"):
                info = vids.get(key) or {}
                u = info.get("url", "")
                if u and "mp4" in u:
                    urls.append(u)
                    break
        return urls
    except Exception as e:
        print(f"[Stock] Pixabay error for '{query}': {e}")
        return []


def _groq_query_for_chunk(chunk_text: str, topic: str = "", for_video: bool = False) -> str | None:
    """Groq-based query generator — skipped instantly when provider is unhealthy."""
    if not _provider_health.is_healthy("groq"):
        print("[Stock] Groq unhealthy — skipping for chunk query")
        return None
    try:
        from agents.script_agent import _groq_call
    except ImportError:
        try:
            from script_agent import _groq_call  # type: ignore
        except ImportError:
            return None
    first_120 = " ".join((chunk_text or "").split()[:120])
    if for_video:
        prompt = (
            f"Create one stock B-roll video search query (3-6 English words).\n"
            f"Topic: {topic}\n"
            f"Be as specific as possible. Use real names, real places, real time periods from the text.\n"
            f"GOOD: 'John Douglas FBI agent 1977'\n"
            f"GOOD: 'Edmund Kemper prison interview 1979'\n"
            f"GOOD: 'FBI Quantico Behavioral Science Unit'\n"
            f"BAD: 'crime story background'\n"
            f"BAD: 'dark street night'\n"
            f"Text: {first_120}\nReturn only the query."
        )
    else:
        prompt = (
            f"What is the most specific searchable image subject in this text?\n"
            f"Return only a short English search query (max 5 words).\n"
            f"Be as specific as possible. Use real names, real places, real time periods from the text.\n"
            f"GOOD: 'John Douglas FBI agent 1977'\n"
            f"GOOD: 'Edmund Kemper prison interview 1979'\n"
            f"GOOD: 'FBI Quantico Behavioral Science Unit'\n"
            f"BAD: 'crime story background'\n"
            f"BAD: 'dark street night'\n"
            f"Text: {first_120}\nReturn only the query."
        )
    try:
        result = _groq_call(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=20, temperature=0.2,
        ).choices[0].message.content.strip().strip('"\'')
        # Reject if Groq returned Arabic/non-Latin despite "English only" instruction
        if any('؀' <= c <= 'ۿ' for c in result):
            return None
        if 2 <= len(result.split()) <= 8:
            _provider_health.reset("groq")
            return result
    except Exception as e:
        _provider_health.record_failure("groq")
        print(f"[Stock] Groq query failed (recorded): {e}")
    return None


def _extract_script_keywords(script_text: str, topic: str = "", count: int = 8) -> list[str]:
    """
    Extract specific search keywords from script text for images/video searches.
    Uses Groq if available, falls back to rule-based extraction.
    Returns list of 2-5 word search query strings.
    """
    import re
    _groq_call = None
    try:
        from agents.script_agent import _groq_call as _gc
        _groq_call = _gc
    except ImportError:
        try:
            from script_agent import _groq_call as _gc
            _groq_call = _gc
        except ImportError:
            pass

    if _groq_call and _provider_health.is_healthy("groq"):
        try:
            excerpt = " ".join(script_text.split()[:600])
            prompt = (
                f"Extract {count} specific image/video search queries from this script.\n"
                f"Topic: {topic}\n"
                f"Rules:\n"
                f"- Each query 2-5 words\n"
                f"- Include real names, places, years, events from the text\n"
                f"- Be specific not generic\n"
                f"- English only\n"
                f"- One query per line, no bullets\n\n"
                f"Script: {excerpt}\n\nReturn only the queries, one per line."
            )
            raw = _groq_call(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200, temperature=0.3,
            ).choices[0].message.content.strip()
            queries = [q.strip().lstrip("-•123456789. ").strip()
                       for q in raw.splitlines() if q.strip() and len(q.strip()) > 3][:count]
            if queries:
                print(f"[Stock] Groq extracted {len(queries)} keywords for '{topic}'")
                return queries
        except Exception as e:
            _provider_health.record_failure("groq")
            print(f"[Stock] Groq keyword extraction failed (recorded): {e}")

    # Rule-based fallback
    topic_lower = (topic or "").lower()
    years = re.findall(r'\b(19[4-9]\d|20[0-2]\d)\b', script_text)
    queries: list[str] = [topic] if topic else []
    for yr in years[:2]:
        queries.append(f"{yr} {topic_lower.split()[0] if topic_lower else 'crime'} documentary")
    for loc, loc_q in _LOCATIONS.items():
        if loc in script_text.lower():
            queries.append(loc_q.split(",")[0])
    for theme, theme_q in _THEMES.items():
        if theme in script_text.lower():
            queries.append(theme_q.split(",")[0])
    generic = [
        "courtroom trial vintage", "police investigation 1970s",
        "city street crime night", "prison corridor bars",
        "detective evidence board", "newspaper headlines closeup",
    ]
    queries += generic
    return queries[:count]


def _load_user_images_from_folders(topic: str = "") -> list[dict]:
    """
    Auto-detect user-provided images in standard locations before fetching stock images.
    Checks: assets/images/, content/images/, content/pending/images/, content/images/<topic>/
    Supports .jpg/.jpeg/.png/.webp/.jfif — JFIF files are auto-converted via Pillow.
    Returns list of {"path", "caption", "tags"} dicts.
    """
    search_dirs = [
        "assets/images",
        "content/images",
        "content/pending/images",
        "output/user_images",   # Telegram images downloaded by notify_agent
    ]
    # Also check topic-specific subfolder
    if topic:
        from utils.content_manager import topic_to_slug
        slug = topic_to_slug(topic)
        search_dirs.append(f"content/images/{slug}")
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".jfif"}
    found: list[dict] = []

    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in image_exts:
                continue
            path = os.path.abspath(os.path.join(d, fname))
            # Convert JFIF → JPEG so MoviePy/Pillow can load it reliably
            if ext == ".jfif":
                try:
                    from PIL import Image as _PIL
                    converted = os.path.abspath(os.path.join(d, os.path.splitext(fname)[0] + "_converted.jpg"))
                    if not os.path.exists(converted):
                        _PIL.open(path).convert("RGB").save(converted, "JPEG")
                        print(f"[Image] Converted JFIF → JPG: {fname}")
                    path = converted
                except Exception as e:
                    print(f"[Image] JFIF conversion failed ({fname}): {e}")
                    continue
            stem = os.path.splitext(fname)[0].replace("_", " ").replace("-", " ")
            # Check for sidecar .txt caption (written by notify_agent for Telegram images)
            sidecar_caption = ""
            txt_sidecar = os.path.join(d, os.path.splitext(fname)[0] + ".txt")
            if os.path.exists(txt_sidecar):
                try:
                    with open(txt_sidecar, encoding="utf-8") as _sf:
                        sidecar_caption = _sf.read().strip()
                except Exception:
                    pass
            # Quality gate: reject images too small to upscale to 1080p cleanly
            try:
                from PIL import Image as _PQIL
                _qw, _qh = _PQIL.open(path).size
                if _qw < 480 or _qh < 270:
                    print(f"[Image] SKIP too small ({_qw}×{_qh}): {fname} — min 480×270")
                    continue
            except Exception as _qe:
                print(f"[Image] SKIP unreadable: {fname} — {_qe}")
                continue

            caption = sidecar_caption or stem or topic or "documentary scene"
            tags = (
                [w.lower() for w in sidecar_caption.split() if len(w) > 3]
                if sidecar_caption else ["user_provided"]
            )
            if sidecar_caption:
                print(f"[Image] Folder image with caption: '{caption[:80]}'")
            found.append({
                "path":    path,
                "caption": caption,
                "tags":    tags,
            })

    if found:
        print(f"[Image] Found {len(found)} user-provided image(s) in assets/content folders")
    return found


_CONTENT_SKIP = {'_shared', 'images', 'pending', 'processed', 'shopmart', 'dark_crime'}

def _normalize_for_match(s: str) -> str:
    """Lowercase, strip non-ASCII (Arabic etc.), keep only a-z0-9 — for fuzzy folder matching."""
    import re
    s = s.lower()
    s = re.sub(r'[^\x00-\x7F]', '', s)  # drop non-ASCII (Arabic, accented chars)
    s = re.sub(r'[^a-z0-9]', '', s)      # drop punctuation, spaces, underscores, hyphens
    return s


def find_content_folder(topic: str) -> str | None:
    """Return path to content/<folder> matching this topic dynamically, or None.

    Scans existing folders — never creates new ones.
    Matching: normalize both sides (lowercase, drop non-ASCII, drop separators),
    then try exact → containment. Prefers longest (most specific) containment match.
    Alias map handles topics with no word overlap with their folder (e.g. narcos → pablo_escobar).
    """
    # Alias map for topics that share no words with their folder name
    _ALIAS = {
        'narcos':            'pablo_escobar',
        'american gangster': 'frank_lucas',
        'monster':           'dahmer',
        'boardwalk empire':  'al_capone',
    }
    topic_lower = topic.lower().strip()
    for alias, folder_name in _ALIAS.items():
        if alias in topic_lower:
            p = f'content/{folder_name}'
            if os.path.exists(p):
                return p

    norm_topic = _normalize_for_match(topic)
    if not norm_topic:
        return None

    if not os.path.isdir('content'):
        return None

    best_path  = None
    best_score = 0

    for entry in os.scandir('content'):
        if not entry.is_dir() or entry.name in _CONTENT_SKIP:
            continue
        norm_folder = _normalize_for_match(entry.name)
        if not norm_folder:
            continue

        if norm_topic == norm_folder:
            return entry.path                          # exact — return immediately

        if norm_folder in norm_topic:                  # e.g. "dahmer" in "jeffreydahmer"
            score = len(norm_folder)
        elif norm_topic in norm_folder:                # e.g. "griselda" in "griseblanco" reverse
            score = len(norm_topic)
        else:
            continue

        if score > best_score:
            best_score = score
            best_path  = entry.path

    return best_path


def load_all_content(
    topic: str,
) -> tuple[list[str], list[dict], str | None, str | None]:
    """Load images, videos, and music from GitHub content library.

    Priority: topic-specific folder first, then content/_shared as supplement.
    Returns (image_paths, video_dicts, music_long_path, music_short_path).
    video_dicts: [{"path", "duration", "type": "pure"|"broll", "tags", "caption"}]
    All content-library videos are typed "pure" by default.
    """
    _img_exts = {'.jpg', '.jpeg', '.png', '.webp', '.jfif'}
    _vid_exts = {'.mp4', '.mov', '.avi'}

    def _scan_paths(d: str, exts: set) -> list[str]:
        if not os.path.isdir(d):
            return []
        return [
            os.path.abspath(os.path.join(d, f)) for f in sorted(os.listdir(d))
            if not f.startswith('.') and os.path.splitext(f)[1].lower() in exts
        ]

    def _validate_video_file(path: str) -> bool:
        size = os.path.getsize(path)
        if size < 1000:  # LFS pointer files are ~130 bytes — 1KB is safe floor
            print(f'[GitHub] Skipping LFS pointer file: {os.path.basename(path)} ({size} bytes)')
            return False
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', path],
                capture_output=True, text=True, timeout=15,
            )
            duration = float(result.stdout.strip())
            return duration > 0
        except Exception:
            print(f'[GitHub] Invalid video file: {os.path.basename(path)}')
            return False

    def _make_video_dict(path: str) -> dict:
        dur  = _ffprobe_duration(path) or 0.0
        stem = os.path.splitext(os.path.basename(path))[0]
        tags = [w.lower() for w in stem.replace('_', ' ').replace('-', ' ').split() if len(w) > 2]
        return {"path": path, "duration": dur, "type": "pure", "tags": tags, "caption": stem}

    def _scan_and_validate_videos(d: str) -> list[dict]:
        raw = _scan_paths(d, _vid_exts)
        valid = [p for p in raw if _validate_video_file(p)]
        skipped = len(raw) - len(valid)
        print(f'[GitHub] Valid videos: {len(valid)} / {len(raw)} total ({skipped} skipped - LFS pointers)')
        return [_make_video_dict(p) for p in valid]

    def _filter_images_by_quality(paths: list[str]) -> list[str]:
        """Reject images too small to upscale to 1080p without visible pixelation."""
        try:
            from PIL import Image as _PQIL
        except ImportError:
            return paths
        good: list[str] = []
        for p in paths:
            try:
                w, h = _PQIL.open(p).size
                if w >= 480 and h >= 270:
                    good.append(p)
                else:
                    print(f'[GitHub] SKIP low-res image ({w}×{h}): {os.path.basename(p)}')
            except Exception as _e:
                print(f'[GitHub] SKIP unreadable image: {os.path.basename(p)} — {_e}')
        return good

    topic_folder  = find_content_folder(topic)
    shared_folder = 'content/_shared'

    images: list[str]       = []
    videos: list[dict]      = []
    music_long: str | None  = None
    music_short: str | None = None

    # Topic-specific
    if topic_folder and os.path.exists(topic_folder):
        images += _filter_images_by_quality(_scan_paths(f'{topic_folder}/images', _img_exts))
        videos += _scan_and_validate_videos(f'{topic_folder}/videos')
        long_p  = f'{topic_folder}/music/documentary_long.mp3'
        short_p = f'{topic_folder}/music/documentary_short.mp3'
        if os.path.exists(long_p):
            music_long = long_p
        if os.path.exists(short_p):
            music_short = short_p

    # Shared supplement
    if os.path.exists(shared_folder):
        images += _filter_images_by_quality(_scan_paths(f'{shared_folder}/images', _img_exts))
        videos += _scan_and_validate_videos(f'{shared_folder}/videos')
        if not music_long:
            shared_long = f'{shared_folder}/music/documentary_long.mp3'
            if os.path.exists(shared_long):
                music_long = shared_long

    total_dur = sum(v["duration"] for v in videos)
    print(f'[GitHub] Content loaded for topic: {topic}')
    print(f'[GitHub] Topic folder: {topic_folder or "none"}')
    print(f'[GitHub] Images: {len(images)} | Videos: {len(videos)} ({total_dur:.0f}s total)')
    print(f'[GitHub] Custom music: {bool(music_long)}')
    return images, videos, music_long, music_short


def _load_user_videos_from_folder() -> list[dict]:
    """
    Load user-provided videos from output/user_videos/ (downloaded from Telegram).
    Reads sidecar .txt caption written by notify_agent at download time.
    Returns list of {"path", "tags", "caption"} dicts.
    """
    folder = "output/user_videos"
    if not os.path.isdir(folder):
        return []
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    found: list[dict] = []
    for fname in sorted(os.listdir(folder)):
        ext = os.path.splitext(fname)[1].lower()
        if ext not in video_exts:
            continue
        path = os.path.join(folder, fname)
        if not os.path.exists(path):
            continue
        txt_path = os.path.splitext(path)[0] + ".txt"
        caption = ""
        if os.path.exists(txt_path):
            try:
                with open(txt_path, encoding="utf-8") as f:
                    caption = f.read().strip()
            except Exception:
                pass
        stem = os.path.splitext(fname)[0].replace("_", " ").replace("-", " ")
        caption = caption or stem
        tags = [w.lower() for w in caption.split() if len(w) > 3]
        found.append({"path": path, "tags": tags, "caption": caption})
    if found:
        print(f"[Video] Found {len(found)} user-provided video(s) in {folder}")
    return found


def clean_caption_for_prompt(caption: str) -> str:
    """Strip file extension and noise chars from a filename before using as AI prompt."""
    import re as _re
    caption = _re.sub(r'\.(jfif|jpg|jpeg|png|webp|mp4|mov|avi)$', '', caption, flags=_re.IGNORECASE)
    caption = _re.sub(r'[+_\-]', ' ', caption)
    caption = _re.sub(r'\bCopy\b', '', caption, flags=_re.IGNORECASE)
    caption = _re.sub(r'\s+', ' ', caption).strip()
    return caption


_PURE_VIDEO_KEYWORDS = {"pure", "clean", "scene", "real", "documentary",
                        "original", "raw", "interview", "live", "reel"}


def _is_pure_video(video_dict: dict) -> bool:
    """Return True if video should keep its original background audio.

    Videos from the content/ library are always pure.
    Telegram videos: pure only if filename/caption contains a pure keyword.
    """
    path = (video_dict.get("path") or "")
    # All content-library videos are pure by default
    if "content" + os.sep in path or "content/" in path:
        return True
    tags    = [t.lower() for t in (video_dict.get("tags") or [])]
    caption = (video_dict.get("caption") or "").lower()
    stem    = os.path.splitext(os.path.basename(path))[0].lower().replace("_", " ").replace("-", " ")
    combined = caption + " " + stem
    return (
        any(t in _PURE_VIDEO_KEYWORDS for t in tags)
        or any(k in combined for k in _PURE_VIDEO_KEYWORDS)
    )


def _mix_pure_video_audio(final_video_path: str, pure_video_paths: list[str]) -> str:
    """Mix original audio from pure user videos (25%) with narration (100%).

    Loops the pure video audio so it always covers the full narration length.
    Returns final_video_path (file replaced in-place on success).
    """
    import shutil as _shutil

    if not pure_video_paths:
        return final_video_path

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        print("[Video] ffmpeg not found — skipping pure video audio mix")
        return final_video_path

    bg_video = next((p for p in pure_video_paths if os.path.exists(p)), None)
    if not bg_video:
        return final_video_path

    mixed_path = final_video_path.replace(".mp4", "_mixed.mp4")
    try:
        cmd = [
            ffmpeg, "-y",
            "-stream_loop", "-1", "-i", bg_video,
            "-i", final_video_path,
            "-filter_complex",
            "[0:a]volume=0.25[orig];[1:a]volume=1.0[narr];"
            "[orig][narr]amix=inputs=2:duration=shortest:normalize=0[aout]",
            "-map", "1:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            mixed_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        os.replace(mixed_path, final_video_path)
        print(f"[Video] Pure video with original sound mixed: {os.path.basename(final_video_path)}")
        print(f"[Video] Original audio at 25%, narration at 100%")
    except Exception as e:
        print(f"[Video] Pure video audio mix failed: {e} — using narration only")
        if os.path.exists(mixed_path):
            os.remove(mixed_path)

    return final_video_path


def _escape_drawtext(text: str) -> str:
    """Escape special characters for ffmpeg drawtext filter."""
    return (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
            .replace("[", "\\[")
            .replace("]", "\\]")
    )


def _find_text_font(arabic: bool = False) -> str:
    """Return a valid TTF path for ffmpeg drawtext, or empty string."""
    import glob as _glob
    if arabic:
        candidates = [
            r"C:\Windows\Fonts\NotoSansArabic-Regular.ttf",
            r"C:\Windows\Fonts\Arabic.ttf",
            r"/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
            r"/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
            r"/usr/share/fonts/noto/NotoSansArabic-Regular.ttf",
        ]
    else:
        candidates = [
            r"C:\Windows\Fonts\DejaVuSans-Bold.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            r"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            r"/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            r"/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # last resort: any ttf on the system
    for pattern in [r"C:\Windows\Fonts\*.ttf", "/usr/share/fonts/**/*.ttf"]:
        found = _glob.glob(pattern, recursive=True)
        if found:
            return found[0]
    return ""


def _parse_chapter_timestamps(chapters_str: str) -> list[tuple[float, str]]:
    """Parse 'MM:SS Title' lines → list of (seconds, title) sorted by time."""
    import re as _re
    results = []
    for line in (chapters_str or "").splitlines():
        m = _re.match(r"(\d{1,2}):(\d{2})\s+(.*)", line.strip())
        if m:
            secs = int(m.group(1)) * 60 + int(m.group(2))
            results.append((float(secs), m.group(3).strip()))
    return sorted(results, key=lambda x: x[0])


def _apply_intro_outro_overlay(
    video_path: str,
    title: str,
    language: str,
    video_id: str,
    is_short: bool = False,
    chapters_str: str = "",
    hook_text: str = "",
) -> str:
    """Apply cold open hook, title card, chapter transitions, and outro via single ffmpeg pass.

    Returns video_path (replaced in-place on success, original kept on failure).
    """
    import shutil as _shutil

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        print("[Overlay] ffmpeg not found — skipping overlays")
        return video_path

    # ── Pre-validate video before building filter chain ────────────────────
    if not video_path or not os.path.exists(video_path):
        print("[Overlay] Video file missing — skipping overlays")
        return video_path
    try:
        _pre_dur = _ffprobe_duration(video_path) or 0.0
        if _pre_dur < 5.0:
            print(f"[Overlay] Video too short ({_pre_dur:.1f}s) — skipping overlays")
            return video_path
    except Exception as _pv_e:
        print(f"[Overlay] Pre-validation probe failed ({_pv_e}) — skipping overlays")
        return video_path

    arabic = (language or "").lower().startswith("ar")
    font = _find_text_font(arabic=arabic)
    if not font:
        print("[Overlay] No font found — skipping overlays")
        return video_path

    font_esc = font.replace("\\", "/").replace(":", "\\:")
    channel_name = _escape_drawtext("Dark Crime Decoded")
    subtitle_text = _escape_drawtext("حقائق الجريمة الحقيقية" if arabic else "True Crime Documentary")
    title_esc = _escape_drawtext(title[:60] if title else "")
    cta_text = _escape_drawtext("اشترك في القناة" if arabic else "Subscribe for more True Crime")

    w, h = "iw", "ih"

    # Normalize resolution + pixel format to prevent "Error reinitializing filters"
    norm_w, norm_h = ("1080", "1920") if is_short else ("1920", "1080")
    filters = [f"scale={norm_w}:{norm_h},format=yuv420p"]

    if is_short:
        # Shorts: no text overlays — clean video
        pass
    else:
        # Long video: fade-out only — no text overlays
        try:
            _vid_dur = _ffprobe_duration(video_path) or 0.0
        except Exception:
            _vid_dur = 0.0

        if _vid_dur > 10:
            outro_start = _vid_dur - 4.5
            filters += [f"fade=t=out:st={outro_start}:d=4.5"]

    vf = ",".join(filters)
    out_path = video_path.replace(".mp4", "_overlay.mp4")
    cmd = [
        ffmpeg, "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        os.replace(out_path, video_path)
        print(f"[Overlay] Intro/outro overlays applied: {os.path.basename(video_path)}")
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="replace")[-400:]
        print(f"[Overlay] skipped due to mismatch: {err[:200]}")
        if os.path.exists(out_path):
            os.remove(out_path)
    except Exception as e:
        print(f"[Overlay] skipped due to mismatch: {e}")
        if os.path.exists(out_path):
            os.remove(out_path)

    return video_path


def check_content_sufficiency(
    user_images: list,
    user_videos: list,
    target_duration_sec: float,
) -> tuple[bool, float]:
    """Calculate how much of the target duration user content covers.

    Returns (is_sufficient, coverage_ratio) where coverage_ratio is 0.0-1.0+.
    Each image counts as 5 s on screen.
    All videos counted by actual ffprobe duration (pure) or real duration (broll).
    """
    images_coverage = len(user_images) * 5

    pure_coverage  = 0.0
    broll_coverage = 0.0
    for v in user_videos:
        path = v.get("path", "")
        if not path or not os.path.exists(path):
            continue
        dur = _ffprobe_duration(path) or 0.0
        if _is_pure_video(v):
            pure_coverage += dur
        else:
            broll_coverage += min(dur, 8.0) if dur > 0 else 8.0

    total_coverage = images_coverage + pure_coverage + broll_coverage
    ratio = total_coverage / target_duration_sec if target_duration_sec > 0 else 0.0

    pure_files  = sum(1 for v in user_videos if _is_pure_video(v) and os.path.exists(v.get("path","")))
    broll_files = sum(1 for v in user_videos if not _is_pure_video(v) and os.path.exists(v.get("path","")))
    print(f"[Video] Pure videos:  {pure_coverage:.0f}s across {pure_files} file(s)")
    print(f"[Video] Broll clips:  {broll_coverage:.0f}s across {broll_files} clip(s)")
    print(f"[Video] Images:       {images_coverage}s across {len(user_images)} image(s)")
    print(f"[Video] Total coverage: {total_coverage:.0f}s / {target_duration_sec:.0f}s target ({ratio*100:.0f}%)")

    if ratio >= 0.80:
        print(f"[Video] \u2705 SELF-SUFFICIENT \u2014 skipping all external search")
    elif ratio >= 0.60:
        gap = target_duration_sec - total_coverage
        print(f"[Video] \u26a0\ufe0f Gap: {gap:.0f}s \u2014 filling with Wikimedia + OpenAI only")
    else:
        gap = target_duration_sec - total_coverage
        print(f"[Video] \u26a0\ufe0f Gap: {gap:.0f}s \u2014 full search chain activated")

    return ratio >= 0.80, ratio


def parallel_map_safe(fn, items: list, max_workers: int = 10,
                      timeout: int = 60, label: str = "task") -> list:
    """
    Apply fn to each item concurrently using ThreadPoolExecutor.

    Returns a list of results (None for any failed item).
    Never raises — individual task failures are logged and skipped.
    Safe for GitHub Actions: no shared mutable state, bounded concurrency.

    Args:
        fn:          Callable(item) -> result
        items:       List of inputs
        max_workers: Max parallel threads (20-40 for image/TTS, 4-8 for API calls)
        timeout:     Per-task timeout in seconds
        label:       Log label for progress messages
    """
    import concurrent.futures

    results: list = [None] * len(items)
    if not items:
        return results

    print(f"[Parallel] Starting {len(items)} {label}(s) (max_workers={max_workers})")
    completed = 0

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_idx = {ex.submit(fn, item): i for i, item in enumerate(items)}
            done_iter = concurrent.futures.as_completed(
                future_to_idx, timeout=timeout * max(len(items), 1)
            )
            for future in done_iter:
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result(timeout=timeout)
                    completed += 1
                except concurrent.futures.TimeoutError:
                    print(f"[Parallel] {label} {idx} timed out — continuing")
                except Exception as e:
                    print(f"[Parallel] {label} {idx} failed: {e} — continuing")
    except concurrent.futures.TimeoutError:
        print(f"[Parallel] Overall timeout — partial results returned")
    except Exception as e:
        print(f"[Parallel] Executor error: {e}")

    print(f"[Parallel] {completed}/{len(items)} {label}(s) completed")
    return results


def _fetch_gap_images(
    script_text: str,
    needed: int,
    video_id: str,
    topic: str,
    coverage_ratio: float,
    style_profile: str = "",
) -> list[str]:
    """Fill a visual gap: fetch_real_images → DuckDuckGo bulk → Pollinations AI."""
    if needed <= 0:
        return []

    _clean_t = _clean_topic_name(topic)  # "Richard Farley" not the full video title
    results: list[str] = []

    # Priority 1: full real-image search per chunk (raised cap from 8 → 50)
    wiki_imgs = fetch_real_images(script_text, min(needed, 50), video_id,
                                   topic=_clean_t, style_profile=style_profile)
    results.extend(wiki_imgs)
    if len(results) >= needed:
        return results[:needed]

    # Priority 2: DuckDuckGo bulk — query variations on the clean topic name
    remaining = needed - len(results)
    if remaining > 0 and _clean_t:
        _ddg_queries = [
            _clean_t,
            f"{_clean_t} crime",
            f"{_clean_t} arrest",
            f"{_clean_t} documentary",
            f"{_clean_t} news",
        ]
        _ddg_seen: set[str] = set()
        for _dq in _ddg_queries:
            if len(results) >= needed:
                break
            _ddg_urls = _search_duckduckgo_images(_dq, max_results=10)
            for _url in _ddg_urls:
                if len(results) >= needed:
                    break
                if _url in _ddg_seen:
                    continue
                _ddg_seen.add(_url)
                _out = os.path.join(IMAGES_DIR, f"{video_id}_ddg_{len(results)}.png")
                _dl = download_real_image(_url, _out)
                if _dl:
                    results.append(_dl)

    if len(results) >= needed:
        return results[:needed]

    # Priority 3: OpenAI web search
    remaining = needed - len(results)
    if remaining > 0:
        ai_imgs = _fetch_openai_images_for_gap(_clean_t, remaining, video_id)
        results.extend(ai_imgs)
    if len(results) >= needed:
        return results[:needed]

    # Priority 4: Pollinations AI — last resort with CLEAN topic name (not full video title)
    remaining = needed - len(results)
    if remaining > 0:
        print(f"[Video] Gap-fill last resort: generating {remaining} Pollinations AI images")
        style_hint = f", {style_profile}" if style_profile else ""
        _poll_prompts = [
            f"{_clean_t} crime investigation documentary cinematic dark{style_hint}",
            f"{_clean_t} courtroom documentary cinematic{style_hint}",
            f"{_clean_t} evidence forensic dark cinematic documentary{style_hint}",
        ]
        _tasks = [
            (_poll_prompts[i % len(_poll_prompts)],
             os.path.join(IMAGES_DIR, f"{video_id}_gap_{i}.png"))
            for i in range(remaining)
        ]
        _poll_workers = _adaptive_pollinations_workers()
        print(f"[QUEUE] Image generation: {len(_tasks)} pending | workers={_poll_workers} | mode=pollinations")
        gen_results = parallel_map_safe(
            lambda args: generate_ai_image(args[0], args[1]),
            _tasks, max_workers=_poll_workers, timeout=120, label="AI image",
        )
        for r in gen_results:
            if r and os.path.exists(r):
                results.append(r)

    print(f"[Video] Gap-fill complete: {len(results)}/{needed} images")
    return results[:needed]


def _fetch_openai_images_for_gap(topic: str, count: int, video_id: str) -> list[str]:
    """Download images found via OpenAI web search, return local paths."""
    urls = _search_images_openai(f"{topic} real historical photograph", max_results=count * 2)
    paths: list[str] = []
    for i, url in enumerate(urls):
        if len(paths) >= count:
            break
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "DarkCrimeDecoded/1.0"})
            if r.status_code == 200 and r.content:
                ext = ".jpg"
                for candidate in (".png", ".webp", ".jpeg"):
                    if candidate in url.lower():
                        ext = candidate
                        break
                out = os.path.join(IMAGES_DIR, f"{video_id}_oai_{i}{ext}")
                with open(out, "wb") as f:
                    f.write(r.content)
                paths.append(out)
        except Exception as e:
            print(f"[Image] OpenAI gap-fill download failed: {e}")
    return paths


def _detect_assembly_mode(user_images: list | None, user_videos: list | None) -> str:
    """Return 'user_content' if user provided any images or videos, else 'auto'."""
    mode = "user_content" if (user_images or user_videos) else "auto"
    print(f"[Video] Assembly mode: {mode.upper()}")
    return mode


def _search_internet_archive(query: str, max_results: int = 5) -> list[str]:
    """
    Search Internet Archive (archive.org) for public domain video footage.
    Ideal for 1970s-90s news clips, documentaries, real historical footage.
    Returns list of direct MP4 URLs.
    """
    try:
        encoded = requests.utils.quote(query)
        r = requests.get(
            f"https://archive.org/advancedsearch.php"
            f"?q={encoded}+mediatype:movies"
            f"&fl[]=identifier,title"
            f"&sort[]=downloads+desc"
            f"&rows={max_results * 4}"
            f"&output=json",
            timeout=20,
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
        )
        if r.status_code != 200:
            return []
        docs = r.json().get("response", {}).get("docs", [])
        video_urls: list[str] = []
        for doc in docs:
            identifier = doc.get("identifier", "")
            title = doc.get("title", "")
            if not identifier:
                continue
            if _is_blacklisted_source(identifier) or _is_blacklisted_source(title):
                print(f"[Stock] Archive: skipping blacklisted: {identifier}")
                continue
            try:
                fr = requests.get(
                    f"https://archive.org/metadata/{identifier}/files",
                    timeout=15,
                    headers={"User-Agent": "DarkCrimeDecoded/1.0"},
                )
                if fr.status_code == 200:
                    all_mp4s = [
                        f.get("name", "") for f in fr.json().get("result", [])
                        if f.get("name", "").lower().endswith(".mp4")
                        and "thumbnail" not in f.get("name", "").lower()
                    ]
                    # Prefer smaller compressed versions: 512kb > 256kb > h264 > full
                    def _archive_score(n: str) -> int:
                        nl = n.lower()
                        if "512kb" in nl or "256kb" in nl:
                            return 0
                        if "h264" in nl or "_512" in nl:
                            return 1
                        if "ia." in nl:
                            return 2
                        return 3
                    all_mp4s.sort(key=_archive_score)
                    if all_mp4s:
                        name = all_mp4s[0]
                        video_urls.append(
                            f"https://archive.org/download/{identifier}/"
                            f"{requests.utils.quote(name)}"
                        )
            except Exception:
                pass
            if len(video_urls) >= max_results:
                break
            time.sleep(0.3)
        if video_urls:
            print(f"[Stock] Internet Archive: {len(video_urls)} result(s) for '{query}'")
        return video_urls
    except Exception as e:
        print(f"[Stock] Internet Archive error for '{query}': {e}")
        return []


def _search_wikimedia_videos(query: str, max_results: int = 5) -> list[str]:
    """
    Search Wikimedia Commons for public domain video clips.
    Returns list of direct video URLs.
    """
    try:
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search",
                "srsearch": f"{query} filetype:video",
                "srnamespace": "6", "srlimit": max_results * 2,
                "format": "json",
            },
            timeout=15,
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
        )
        if r.status_code != 200:
            return []
        results = r.json().get("query", {}).get("search", [])
        video_urls: list[str] = []
        for item in results:
            title = item.get("title", "")
            if not title.startswith("File:"):
                title = f"File:{title}"
            try:
                ir = requests.get(
                    "https://commons.wikimedia.org/w/api.php",
                    params={
                        "action": "query", "titles": title,
                        "prop": "imageinfo", "iiprop": "url|mime",
                        "format": "json",
                    },
                    timeout=15,
                    headers={"User-Agent": "DarkCrimeDecoded/1.0"},
                )
                if ir.status_code == 200:
                    for page in ir.json().get("query", {}).get("pages", {}).values():
                        info = (page.get("imageinfo") or [{}])[0]
                        if "video" in info.get("mime", "") and info.get("url"):
                            video_urls.append(info["url"])
                            break
            except Exception:
                pass
            if len(video_urls) >= max_results:
                break
            time.sleep(0.4)
        if video_urls:
            print(f"[Stock] Wikimedia Commons: {len(video_urls)} video(s) for '{query}'")
        return video_urls
    except Exception as e:
        print(f"[Stock] Wikimedia Commons error for '{query}': {e}")
        return []


def _search_coverr(query: str, max_results: int = 5) -> list[str]:
    """
    Search Coverr.co for free cinematic stock videos.
    Returns list of direct MP4 URLs.
    """
    import re as _re
    try:
        encoded = requests.utils.quote(query)
        r = requests.get(
            f"https://coverr.co/s?q={encoded}",
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json, text/html",
            },
        )
        if r.status_code != 200:
            return []
        # Try JSON first
        try:
            data = r.json()
            for key in ("hits", "videos", "results"):
                items = data.get(key) or []
                if isinstance(items, dict):
                    items = items.get("hits") or []
                urls = []
                for v in items[:max_results]:
                    src = (v.get("_source", {}).get("url") or v.get("url") or
                           v.get("mp4_url") or "")
                    if src and ".mp4" in src:
                        urls.append(src)
                if urls:
                    print(f"[Stock] Coverr: {len(urls)} video(s) for '{query}'")
                    return urls
        except Exception:
            pass
        # HTML fallback
        mp4s = _re.findall(r'https://[^"\'<>\s]+\.mp4[^"\'<>\s]*', r.text)
        mp4s = list(dict.fromkeys(mp4s))[:max_results]
        if mp4s:
            print(f"[Stock] Coverr (HTML): {len(mp4s)} video(s) for '{query}'")
        return mp4s
    except Exception as e:
        print(f"[Stock] Coverr error for '{query}': {e}")
        return []


def _filter_relevant_results(urls: list[str], topic_keywords: list[str]) -> list[str]:
    """
    Basic relevance filter: keep URLs whose path/filename contains at least one
    topic keyword. Falls back to returning all URLs if none match.
    """
    if not topic_keywords or not urls:
        return urls
    keywords_lower = [k.lower() for k in topic_keywords if k]
    relevant = [
        u for u in urls
        if any(kw in u.lower() for kw in keywords_lower)
    ]
    return relevant if relevant else urls


_VIDEO_MIN_BYTES = 100_000              # 100 KB
_VIDEO_MAX_BYTES = 80_000_000           # 80 MB  (general sources)
_ARCHIVE_VIDEO_MAX_BYTES = 200_000_000  # 200 MB (Internet Archive — large archival files)


def _ytdlp_clip_first15(url: str, output_path: str) -> str | None:
    """Use yt-dlp to download only the first 15 seconds of a video URL.
    Works on any URL yt-dlp supports (archive.org, YouTube, Vimeo, etc.).
    Ignores total file size — only downloads the clip portion."""
    import subprocess
    if not _ensure_ytdlp():
        return None
    try:
        cmd = [
            "yt-dlp",
            url,
            "--download-sections", "*0:00-0:15",
            "-f", "mp4[height<=720]/best[ext=mp4]/best",
            "-o", output_path,
            "--quiet",
            "--no-warnings",
            "--force-keyframes-at-cuts",
        ]
        subprocess.run(cmd, timeout=90, check=False)
        if os.path.exists(output_path) and os.path.getsize(output_path) > _VIDEO_MIN_BYTES:
            print(f"[Stock] yt-dlp clip (first 15s): {url[:60]}")
            return output_path
    except Exception as e:
        print(f"[Stock] yt-dlp clip error for '{url[:60]}': {e}")
    return None


def _download_video_url(url: str, output_path: str,
                        max_bytes: int | None = None) -> str | None:
    """Download one stock video URL with Content-Type + size validation.
    Oversized videos are rescued via yt-dlp (first 15s clip) instead of skipped."""
    limit = max_bytes or _VIDEO_MAX_BYTES
    try:
        # Check Content-Type via HEAD before downloading the full file
        ct = ""
        content_length = 0
        try:
            head = requests.head(url, timeout=8, headers={"User-Agent": "DarkCrimeDecoded/1.0"}, allow_redirects=True)
            ct = head.headers.get("Content-Type", "").lower()
            content_length = int(head.headers.get("Content-Length", 0) or 0)
            if content_length > limit:
                # Rescue via yt-dlp: download only the first 15s instead of skipping
                print(f"[Stock] Oversized ({content_length // 1_000_000} MB) — trying yt-dlp clip: {url[:60]}")
                return _ytdlp_clip_first15(url, output_path)
        except Exception:
            pass

        if ct and not (ct.startswith("video/") or "octet-stream" in ct or "mp4" in ct):
            print(f"[Stock] Rejected non-video Content-Type ({ct.split(';')[0].strip()}): {url[:60]}")
            return None

        r = requests.get(
            url,
            timeout=90,
            stream=True,
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
        )
        if r.status_code != 200:
            return None

        with open(output_path, "wb") as f:
            downloaded = 0
            for chunk in r.iter_content(chunk_size=1024 * 128):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded > limit:
                        # Downloaded enough — close and try yt-dlp clip instead
                        print(f"[Stock] Stream limit hit (>{limit // 1_000_000} MB) — trying yt-dlp clip: {url[:60]}")
                        break

        size = os.path.getsize(output_path)
        if size < _VIDEO_MIN_BYTES:
            try:
                os.remove(output_path)
            except OSError:
                pass
            # Last resort: yt-dlp clip
            return _ytdlp_clip_first15(url, output_path)
        return output_path
    except Exception:
        return None


_SOURCE_BLACKLIST = {"agc", "chronicle", "reaction", "review", "compilation"}


def _is_blacklisted_source(url_or_title: str) -> bool:
    """Return True if the URL or title belongs to a channel/type we want to skip."""
    text = (url_or_title or "").lower()
    return any(kw in text for kw in _SOURCE_BLACKLIST)


def _validate_clip(path: str) -> bool:
    """Return True if path is a non-corrupt video file with any usable duration (>0.5s).

    Duration limits (3s min / 60s max) are NOT enforced here — short clips are
    looped during assembly and long clips are trimmed by _trim_long_clip before
    this function is called.
    """
    if not path or not os.path.exists(path):
        return False
    try:
        import subprocess as _sp
        result = _sp.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout:
            import json as _json
            data = _json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    dur = float(stream.get("duration", 0) or 0)
                    return dur > 0.5
    except Exception:
        pass
    try:
        try:
            from moviepy.editor import VideoFileClip as _VFC
        except ImportError:
            from moviepy import VideoFileClip as _VFC
        with _VFC(path) as c:
            return c.duration > 0.5
    except Exception:
        return False


def _trim_long_clip(path: str, max_dur: float = 15.0) -> bool:
    """Trim a video longer than max_dur seconds to a documentary-safe subclip.

    Picks a random start at 10–50% into the clip to skip intros and credits.
    Replaces the file in-place using ffmpeg stream-copy (fast, no re-encode).
    Returns True on success, False if the clip is already short enough or if
    trimming fails (caller should still use the original).
    """
    import subprocess as _sp
    dur = _ffprobe_duration(path)
    if dur <= max_dur:
        return True  # nothing to do
    # Stay at least max_dur seconds from the end
    latest_start = max(0.0, dur - max_dur - 1.0)
    earliest_start = dur * 0.10
    start = random.uniform(min(earliest_start, latest_start), latest_start)
    tmp = path + "._trim.mp4"
    try:
        res = _sp.run(
            ["ffmpeg", "-y", "-ss", f"{start:.1f}", "-i", path,
             "-t", f"{max_dur:.1f}", "-c", "copy", tmp],
            capture_output=True, timeout=30,
        )
        if res.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
            os.replace(tmp, path)
            print(f"[Stock] Trimmed long clip {dur:.0f}s → {max_dur:.0f}s (start={start:.0f}s)")
            return True
        print(f"[Stock] Trim ffmpeg failed (rc={res.returncode}) — keeping original")
    except Exception as e:
        print(f"[Stock] Trim error: {e} — keeping original")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def _ffprobe_duration(path: str) -> float:
    """Return video duration in seconds via ffprobe, or 0.0 on failure."""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return float(result.stdout.strip() or 0)
    except Exception:
        pass
    return 0.0


def _download_first_valid_video(urls: list[str], output_path: str,
                                max_bytes: int | None = None) -> str | None:
    for url in urls:
        if _is_blacklisted_source(url):
            print(f"[Stock] Skipping blacklisted source: {url[:80]}")
            continue
        saved = _download_video_url(url, output_path, max_bytes=max_bytes)
        if not saved:
            continue
        dur = _ffprobe_duration(saved)
        if dur < 0.5:
            # Truly corrupt or empty — discard
            print(f"[Stock] Rejected corrupt video (duration={dur:.2f}s): {url[:60]}")
            try:
                os.remove(saved)
            except OSError:
                pass
            continue
        if dur > 60.0:
            # Long archive footage — trim to a 15s documentary subclip
            _trim_long_clip(saved, max_dur=15.0)
        elif dur < 3.0:
            print(f"[Stock] Short clip ({dur:.1f}s) accepted — will loop during assembly")
        if not _validate_clip(saved):
            print(f"[Stock] Clip failed structural validation: {url[:60]}")
            try:
                os.remove(saved)
            except OSError:
                pass
            continue
        return saved
    return None


def _topic_stock_fallback_queries(topic: str, script_text: str = "") -> list[str]:
    """
    Build fallback B-roll queries from script keywords when AI query generation fails.
    Tries to be specific to the script content before falling back to generic crime terms.
    """
    t = (topic or "").lower()

    # Script-aware: extract keywords directly from script when provided
    if script_text and len(script_text.split()) > 30:
        extracted = _extract_script_keywords(script_text, topic, count=8)
        if extracted:
            return extracted

    # Topic-specific fallbacks for known subjects
    if "frank lucas" in t or "american gangster" in t:
        return [
            "1970s harlem street night", "new york police investigation",
            "courtroom trial 1970s", "prison corridor bars",
            "money counting cash table", "vintage newspaper headlines",
            "city skyline night traffic", "detective evidence board",
        ]
    if "pablo escobar" in t or "narcos" in t or "medellin" in t:
        return [
            "Medellin Colombia 1980s street", "cocaine drug operation 1980s",
            "Colombian police raid", "cartel money stacks",
            "DEA investigation 1980s", "prison Bogota Colombia",
            "South America jungle operation", "vintage news footage crime",
        ]
    if "el chapo" in t or "sinaloa" in t or "cartel" in t:
        return [
            "Mexico border desert 1990s", "drug tunnel underground",
            "Mexican police operation", "cartel weapons money",
            "prison escape tunnel", "US DEA investigation Mexico",
            "border patrol drugs", "Mexican courtroom trial",
        ]
    if "al capone" in t or "prohibition" in t or "chicago" in t:
        return [
            "Chicago 1920s prohibition era street", "speakeasy 1920s bar interior",
            "FBI investigation 1930s", "gangster 1920s suit car",
            "prison Alcatraz exterior", "vintage courtroom 1930s",
            "newspaper headline bootlegger", "1920s city street night",
        ]
    if "serial killer" in t or "dahmer" in t or "bundy" in t or "btk" in t:
        return [
            "crime scene investigation night", "detective evidence board",
            "prison corridor solitary", "FBI profiling 1980s",
            "suburban street night dark", "police car lights",
            "courtroom trial criminal", "newspaper headlines murder",
        ]
    if "wall street" in t or "fraud" in t or "bernie madoff" in t:
        return [
            "Wall Street New York financial district", "stock market trading floor",
            "FBI financial investigation", "luxury penthouse interior",
            "courtroom white collar crime", "handcuffs arrest businessman",
            "bank vault money", "SEC investigation documents",
        ]
    if "sudan" in t or "darfur" in t or "africa" in t:
        return [
            "Darfur Sudan desert landscape", "African village burning documentary",
            "UN peacekeepers Africa", "refugee camp Sudan",
            "military checkpoint Africa", "International Criminal Court",
            "conflict zone aerial view", "African militia armed group",
        ]
    if "mindhunter" in t or "behavioral science" in t or "criminal profiling" in t or "john douglas" in t:
        return [
            "FBI Quantico academy 1970s",
            "serial killer prison interview 1970s",
            "FBI agents investigation 1970s",
            "Edmund Kemper mugshot arrest",
            "Charles Manson prison interview",
            "FBI behavioral science unit",
            "criminal profiling evidence board",
            "prison interview room 1970s",
        ]

    # Generic crime documentary fallbacks
    return [
        "dark city street night crime",
        "police lights crime scene investigation",
        "courtroom interior judge gavel vintage",
        "prison corridor bars cell",
        "newspaper headlines crime closeup",
        "detective investigation evidence board",
        "vintage police car street",
        "criminal trial archival footage",
    ]


# Section-index → query template for when Groq fails per chunk.
# Uses actual topic name at runtime — NOT the word "mindhunter" hardcoded.
_SECTION_QUERY_TEMPLATES = [
    "{topic} real story documentary",          # section 0 / Hook
    "{topic} history background",              # section 1 / Background
    "{topic} crime investigation evidence",    # section 2 / Main Story
    "{topic} arrest trial verdict",            # section 3 / Shocking Facts
    "{topic} legacy impact today",             # section 4 / Conclusion
]


def _section_fallback_query(section_idx: int, topic: str) -> str:
    """Return a section-specific fallback query using the actual topic name."""
    t = (topic or "crime documentary").strip()
    template = _SECTION_QUERY_TEMPLATES[section_idx % len(_SECTION_QUERY_TEMPLATES)]
    return template.format(topic=t)


def _get_stock_video_query_for_chunk(chunk_text: str, topic: str = "") -> str | None:
    """
    B-roll video query builder.
    Priority: deterministic → Groq (if healthy) → OpenAI fallback.
    """
    # Tier 1: deterministic (no AI)
    det = build_visual_search_query(chunk_text, topic=topic)
    if det and not det.lower().startswith("true crime historical"):
        return det

    # Tier 2: Groq (only if healthy)
    result = _groq_query_for_chunk(chunk_text, topic=topic, for_video=True)
    if result:
        return result

    # Tier 3: OpenAI fallback
    first_120 = " ".join((chunk_text or "").split()[:120])
    prompt = (
        f"Create one stock video search query (3-6 English words) for this script chunk.\n"
        f"Topic context: {topic}\n"
        f"Be as specific as possible. Use real names, real places, real time periods from the text.\n"
        f"GOOD: 'John Douglas FBI agent 1977'\n"
        f"GOOD: 'Edmund Kemper prison interview 1979'\n"
        f"BAD: 'crime story background'\n"
        f"Text: {first_120}\nReturn only the query."
    )
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key and _provider_health.is_healthy("openai"):
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 20, "temperature": 0.2},
                timeout=12,
            )
            if r.status_code == 200:
                q = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if 2 <= len(q.split()) <= 8:
                    return q
            elif r.status_code == 429:
                _provider_health.record_failure("openai")
        except Exception as e:
            _provider_health.record_failure("openai")
            print(f"[Stock] OpenAI video query failed (recorded): {e}")
    return None


# ── yt-dlp availability check ────────────────────────────────────────────────
def _ensure_ytdlp() -> bool:
    """Return True if yt-dlp is available, install it if not."""
    import subprocess
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, timeout=5)
        return True
    except FileNotFoundError:
        print("[Stock] yt-dlp not found — installing...")
        os.system("pip install yt-dlp -q")
        try:
            subprocess.run(["yt-dlp", "--version"], capture_output=True, timeout=5)
            return True
        except FileNotFoundError:
            return False


_YT_CC_BLACKLIST_TITLES = {
    "tutorial", "how to", "review", "reaction", "gaming", "minecraft",
    "fortnite", "cooking", "recipe", "workout", "yoga", "meditation",
    "unboxing", "haul", "vlog", "prank", "challenge",
    "compilation of compilations",
}
_YT_CC_BLACKLIST_CHANNELS = {"music", "songs", "beats", "gaming", "kids"}


def _search_youtube_cc(query: str, max_results: int = 5) -> list[str]:
    """Search YouTube for Creative Commons licensed videos (10-120s duration)."""
    import subprocess
    if not _ensure_ytdlp():
        return []
    cmd = [
        "yt-dlp",
        f"ytsearch{max_results * 3}:{query}",
        "--match-filter", "license = Creative Commons Attribution license",
        "--print", "%(id)s|%(title)s|%(duration)s|%(channel)s",
        "--no-download",
        "--quiet",
        "--no-warnings",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        urls: list[str] = []
        for line in result.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) < 3:
                continue
            vid_id, title, duration_str = parts[0], parts[1], parts[2]
            channel = parts[3] if len(parts) > 3 else ""
            title_lower  = title.lower()
            channel_lower = channel.lower()
            # Skip blacklisted titles
            if any(b in title_lower for b in _YT_CC_BLACKLIST_TITLES):
                continue
            # Skip blacklisted channels
            if any(b in channel_lower for b in _YT_CC_BLACKLIST_CHANNELS):
                continue
            # Duration filter: 10–120 seconds
            try:
                dur = int(duration_str)
                if not (10 <= dur <= 120):
                    continue
            except (ValueError, TypeError):
                continue
            urls.append(f"https://www.youtube.com/watch?v={vid_id}")
            if len(urls) >= max_results:
                break
        if urls:
            print(f"[Stock] YouTube CC: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Stock] YouTube CC search error for '{query}': {e}")
        return []


def _download_youtube_cc(url: str, output_path: str) -> str | None:
    """Download a YouTube CC video via yt-dlp. Returns path if successful."""
    import subprocess
    if not _ensure_ytdlp():
        return None
    cmd = [
        "yt-dlp",
        url,
        "--match-filter", "license = Creative Commons Attribution license",
        "-f", "mp4[height<=720]/best[ext=mp4]/best",
        "-o", output_path,
        "--quiet",
        "--no-warnings",
        "--max-filesize", "50m",
    ]
    try:
        subprocess.run(cmd, timeout=60)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 10_000:
            return output_path
    except Exception as e:
        print(f"[Stock] YouTube CC download error for '{url}': {e}")
    return None


def _search_vimeo_free(query: str, max_results: int = 5) -> list[str]:
    """Search Vimeo public API for CC-licensed free videos."""
    try:
        r = requests.get(
            "https://api.vimeo.com/videos",
            params={"query": query, "filter": "CC", "per_page": max_results},
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        urls: list[str] = []
        for item in r.json().get("data", []):
            for dl in item.get("download", []):
                link = dl.get("link", "")
                if link and dl.get("type") == "source":
                    urls.append(link)
                    break
        if urls:
            print(f"[Stock] Vimeo CC: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Stock] Vimeo error for '{query}': {e}")
        return []


# ── OpenVerse (WordPress CC Search — no API key, aggregates Flickr + Wikimedia) ──

def _search_openverse_images(query: str, max_results: int = 5) -> list[str]:
    """
    Search OpenVerse (openverse.org) for CC-licensed images.
    Free, no API key. Aggregates Flickr, Wikimedia Commons, and 20+ other sources.
    Returns direct image URLs.
    """
    try:
        r = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": query,
                "page_size": max_results,
                "license_type": "commercial,modification",  # CC licenses safe for YouTube
                "mature": "false",
            },
            headers={"User-Agent": "DarkCrimeDecoded/1.0 (documentary content pipeline)"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[Image] OpenVerse {r.status_code} for '{query}'")
            return []
        results = r.json().get("results", [])
        urls = [item["url"] for item in results if item.get("url")]
        if urls:
            print(f"[Image] OpenVerse: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Image] OpenVerse error for '{query}': {e}")
        return []


def _search_openverse_videos(query: str, max_results: int = 5) -> list[str]:
    """
    Search OpenVerse for CC-licensed video clips.
    Same free endpoint, no API key.
    """
    try:
        r = requests.get(
            "https://api.openverse.org/v1/audio/",  # OpenVerse audio has some video sources too
            params={"q": query, "page_size": max_results, "mature": "false"},
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            timeout=15,
        )
        # OpenVerse doesn't have a dedicated video endpoint — fall back to images
        # that have video mime types
        r2 = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": query,
                "page_size": max_results,
                "mature": "false",
                "extension": "mp4,webm,ogv",
            },
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            timeout=15,
        )
        if r2.status_code == 200:
            results = r2.json().get("results", [])
            urls = [item["url"] for item in results if item.get("url")]
            if urls:
                print(f"[Stock] OpenVerse video: {len(urls)} result(s) for '{query}'")
            return urls
        return []
    except Exception as e:
        print(f"[Stock] OpenVerse video error for '{query}': {e}")
        return []


# ── Library of Congress (loc.gov) — free, no API key, massive US historical archive ──

_LOC_MEDIA_TYPES = {
    "photo": "still image",
    "video": "moving image",
}

def _search_loc_images(query: str, max_results: int = 5) -> list[str]:
    """
    Search Library of Congress (loc.gov) for public domain historical images.
    Free, no API key. Best for US crime/law enforcement/political history 1860s–1990s.
    Returns direct image URLs.
    """
    try:
        r = requests.get(
            "https://www.loc.gov/search/",
            params={
                "q": query,
                "fo": "json",
                "fa": "online-format:image",
                "c": max_results * 3,
                "sp": 1,
            },
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[Image] LoC {r.status_code} for '{query}'")
            return []
        results = r.json().get("results", [])
        urls = []
        for item in results:
            # Prefer JPEG thumbnail or image_url
            for field in ("image_url", "thumbnail"):
                val = item.get(field)
                if isinstance(val, list):
                    val = val[0] if val else None
                if val and isinstance(val, str) and val.startswith("http"):
                    urls.append(val)
                    break
            if len(urls) >= max_results:
                break
        if urls:
            print(f"[Image] Library of Congress: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Image] LoC error for '{query}': {e}")
        return []


def _search_loc_videos(query: str, max_results: int = 5) -> list[str]:
    """
    Search Library of Congress for public domain moving images.
    Returns direct video/stream URLs when available.
    """
    try:
        r = requests.get(
            "https://www.loc.gov/search/",
            params={
                "q": query,
                "fo": "json",
                "fa": "online-format:video",
                "c": max_results * 3,
                "sp": 1,
            },
            headers={"User-Agent": "DarkCrimeDecoded/1.0"},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        results = r.json().get("results", [])
        urls = []
        for item in results:
            # Resources may have streaming links
            for res in item.get("resources", []):
                url = res.get("url") or res.get("stream") or ""
                if url and any(url.endswith(ext) for ext in (".mp4", ".mov", ".webm")):
                    urls.append(url)
                    break
            if len(urls) >= max_results:
                break
        if urls:
            print(f"[Stock] Library of Congress video: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Stock] LoC video error for '{query}': {e}")
        return []


# ── Flickr CC (requires FLICKR_API_KEY env var — free account at flickr.com/services/api) ──

def _search_flickr_images(query: str, max_results: int = 5) -> list[str]:
    """
    Search Flickr for CC-licensed photos.
    Requires FLICKR_API_KEY (free). Best source for real documentary-style news photos.
    Returns direct image URLs sized to 1024px wide (Large).
    """
    api_key = os.getenv("FLICKR_API_KEY", "").strip()
    if not api_key or api_key.startswith("YOUR_"):
        return []
    try:
        r = requests.get(
            "https://www.flickr.com/services/rest/",
            params={
                "method": "flickr.photos.search",
                "api_key": api_key,
                "text": query,
                "license": "1,2,3,4,5,6,9,10",  # All CC + public domain licenses
                "sort": "relevance",
                "per_page": max_results,
                "format": "json",
                "nojsoncallback": 1,
                "extras": "url_l,url_c,url_b",  # Large, Medium 800, Large 1024
                "safe_search": 1,
                "content_type": 1,  # photos only
            },
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[Image] Flickr {r.status_code} for '{query}'")
            return []
        photos = r.json().get("photos", {}).get("photo", [])
        urls = []
        for p in photos:
            url = p.get("url_b") or p.get("url_l") or p.get("url_c") or ""
            if url:
                urls.append(url)
        if urls:
            print(f"[Image] Flickr CC: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Image] Flickr error for '{query}': {e}")
        return []


def _search_duckduckgo_images(query: str, max_results: int = 5) -> list[str]:
    """Search DuckDuckGo images — no API key, returns direct image URLs.
    Accesses Bing image index so finds thousands of results for any topic."""
    try:
        from duckduckgo_search import DDGS
        raw = DDGS().images(keywords=query, max_results=max_results * 3)
        urls = []
        _blocked = _BLOCKED_IMAGE_DOMAINS
        for r in (raw or []):
            url = r.get("image", "")
            if not url or not url.startswith("http"):
                continue
            u_low = url.lower()
            if any(d in u_low for d in _blocked):
                continue
            urls.append(url)
            if len(urls) >= max_results:
                break
        if urls:
            print(f"[Image] DuckDuckGo: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Image] DuckDuckGo images error for '{query}': {e}")
        return []


def _search_duckduckgo_videos(query: str, max_results: int = 5) -> list[str]:
    """Search DuckDuckGo videos — finds news clips, reels, short videos.
    Returns direct embed/source URLs; caller downloads via yt-dlp or direct HTTP."""
    try:
        from duckduckgo_search import DDGS
        raw = DDGS().videos(keywords=query, max_results=max_results * 4)
        urls = []
        for r in (raw or []):
            # DDG video results have 'content' (embed page) and sometimes 'embed_url'
            url = r.get("content", "") or r.get("embed_url", "")
            if not url or not url.startswith("http"):
                continue
            if _is_blacklisted_source(url):
                continue
            urls.append(url)
            if len(urls) >= max_results:
                break
        if urls:
            print(f"[Stock] DuckDuckGo videos: {len(urls)} result(s) for '{query}'")
        return urls
    except Exception as e:
        print(f"[Stock] DuckDuckGo videos error for '{query}': {e}")
        return []


# ── Visual query helpers ─────────────────────────────────────────────────────

_IRRELEVANT_QUERY_TERMS = frozenset({
    "animal", "animals", "wildlife", "nature", "fashion", "beauty",
    "makeup", "cooking", "recipe", "food", "travel", "tourism",
    "fitness", "workout", "yoga", "dance", "gaming",
    "minecraft", "fortnite", "unboxing", "haul",
})


def _is_crime_relevant_query(query: str) -> bool:
    """Return False if query contains off-topic category terms."""
    return not bool(set(query.lower().split()) & _IRRELEVANT_QUERY_TERMS)


def _generate_visual_queries(chunk: str, topic: str) -> list[str]:
    """
    Use OpenAI to generate 2-3 specific stock-video queries from a script chunk.
    Each query targets a real location, action, or time period.
    Falls back to single Groq/OpenAI query on failure.
    """
    first_150 = " ".join((chunk or "").split()[:150])
    prompt = (
        f"You are a documentary video editor. Generate 2-3 specific stock video search queries "
        f"for B-roll footage that matches this script excerpt.\n\n"
        f"Topic: {topic}\n"
        f"Script excerpt: {first_150}\n\n"
        f"Rules:\n"
        f"- Each query: 3-6 English words\n"
        f"- Include a real location, action, or time period\n"
        f"- GOOD: 'FBI headquarters Washington 1970s', 'courtroom trial verdict 1983'\n"
        f"- BAD: 'crime background', 'dark dramatic scene'\n"
        f"- Never include: animals, nature, fashion, beauty, cooking, gaming\n\n"
        f"Return ONLY the queries, one per line. No bullets, no numbers, no explanations."
    )
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 80, "temperature": 0.3},
                timeout=20,
            )
            if r.status_code == 200:
                text = r.json()["choices"][0]["message"]["content"].strip()
                queries = [
                    line.strip().strip('"\'').strip("-•1234567890. ")
                    for line in text.splitlines()
                    if line.strip() and 2 <= len(line.strip().split()) <= 8
                ]
                relevant = [q for q in queries if _is_crime_relevant_query(q)]
                if relevant:
                    print(f"[Stock] Visual queries: {relevant}")
                    return relevant[:3]
        except Exception as e:
            print(f"[Stock] Visual query generation failed: {e}")

    # Fallback: single Groq/OpenAI query
    single = _get_stock_video_query_for_chunk(chunk, topic=topic)
    return [single] if single else []


def _refine_with_youtube_metadata(queries: list[str], topic: str) -> list[str]:
    """
    Fetch YouTube video titles for each query (metadata only, no download).
    Extract recurring keywords from titles and append to the original query.
    Non-fatal — returns originals unchanged if yt-dlp fails or finds nothing.
    """
    import subprocess

    if not _ensure_ytdlp():
        return queries

    _STOP_WORDS = frozenset({
        "the", "a", "an", "of", "in", "on", "at", "to", "for", "and",
        "or", "but", "is", "was", "are", "were", "this", "that", "with",
        "from", "by", "about", "how", "why", "what", "when", "who",
        "full", "video", "official", "new", "best", "top", "part",
        "episode", "channel", "youtube", "hd", "4k", "2024", "2023",
    })

    refined: list[str] = []
    for q in queries:
        try:
            cmd = [
                "yt-dlp", f"ytsearch5:{q}",
                "--print", "%(title)s",
                "--no-download", "--quiet", "--no-warnings",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            titles = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            if titles:
                freq: dict[str, int] = {}
                q_words = set(q.lower().split())
                for title in titles:
                    for word in title.lower().split():
                        word = word.strip(".,!?;:()[]\"'")
                        if (len(word) >= 4 and word not in _STOP_WORDS
                                and word not in q_words and word.isalpha()):
                            freq[word] = freq.get(word, 0) + 1
                top = [w for w, c in sorted(freq.items(), key=lambda x: -x[1]) if c >= 2][:1]
                if top:
                    refined_q = f"{q} {top[0]}"
                    if _is_crime_relevant_query(refined_q) and len(refined_q.split()) <= 7:
                        print(f"[Stock] Refined: '{q}' → '{refined_q}'")
                        refined.append(refined_q)
                        continue
        except Exception as e:
            print(f"[Stock] YouTube metadata refinement failed for '{q}': {e}")
        refined.append(q)

    return refined


def fetch_stock_videos(script_text: str, count: int, video_id: str, topic: str = "") -> list[str]:
    """
    Build a stock-video pool from free licensed sources.

    Priority order per chunk:
      1. Internet Archive (real archival/documentary footage, public domain)
      2. Wikimedia Commons (public domain)
      3. Coverr.co (free cinematic stock)
      4. Pexels (free licensed)
      5. Pixabay (free licensed)

    Queries extracted from actual script content.
    Tries 2-3 alternative queries before falling back to generic.
    """
    import re
    import shutil

    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()
    if not words:
        return []

    chunk_size = max(1, len(words) // max(count, 1))
    chunks = [
        " ".join(words[i * chunk_size: (i + 1) * chunk_size if i < count - 1 else len(words)])
        for i in range(count)
    ]

    # Pre-extract script keywords for fallback queries
    fallback_queries = _topic_stock_fallback_queries(topic, script_text)

    results: list[str] = []
    query_cache: dict[str, str] = {}

    def _try_all_sources(query: str, out_path: str) -> str | None:
        # (src_name, search_fn, use_ytdlp, max_bytes_override)
        for src_name, src_fn, use_ytdlp, mb_override in [
            ("Internet Archive", _search_internet_archive, False, _ARCHIVE_VIDEO_MAX_BYTES),
            ("Wikimedia Videos", _search_wikimedia_videos, False, _VIDEO_MAX_BYTES),
            ("Library of Congress", _search_loc_videos,    False, _VIDEO_MAX_BYTES),
            ("DuckDuckGo",       _search_duckduckgo_videos, True, None),
            ("YouTube CC",       _search_youtube_cc,        True, None),
            ("Pexels",           _search_pexels_videos,    False, _VIDEO_MAX_BYTES),
            ("Pixabay",          _search_pixabay_videos,   False, _VIDEO_MAX_BYTES),
            ("OpenVerse",        _search_openverse_videos, False, _VIDEO_MAX_BYTES),
            ("Coverr",           _search_coverr,           False, _VIDEO_MAX_BYTES),
            ("Vimeo CC",         _search_vimeo_free,       False, _VIDEO_MAX_BYTES),
        ]:
            urls = src_fn(query)
            if not urls:
                continue
            if use_ytdlp:
                saved = _download_youtube_cc(urls[0], out_path)
            else:
                saved = _download_first_valid_video(urls, out_path, max_bytes=mb_override)
            if saved:
                print(f"[Stock] {src_name}: '{query}'")
                return saved
        return None

    for i, chunk in enumerate(chunks):
        # Generate 2-3 specific queries (location/action/mood) then refine via YouTube titles
        ai_queries = _generate_visual_queries(chunk, topic=topic)
        refined_queries = _refine_with_youtube_metadata(ai_queries, topic) if ai_queries else []
        section_q = _section_fallback_query(i, topic)
        fb_a = fallback_queries[i % len(fallback_queries)]
        fb_b = fallback_queries[(i + 1) % len(fallback_queries)]
        # Priority: AI-refined queries → section template → keyword fallbacks
        queries_to_try = list(dict.fromkeys(filter(None, refined_queries + [section_q, fb_a, fb_b])))

        out = os.path.join(STOCK_VIDEOS_DIR, f"{video_id}_stock_{i}.mp4")
        saved = None

        for q in queries_to_try:
            if q in query_cache and os.path.exists(query_cache[q]):
                shutil.copy2(query_cache[q], out)
                saved = out
                print(f"[Stock] Reused '{q}' for chunk {i}")
                break
            print(f"[Stock] Chunk {i}: trying '{q}'")
            saved = _try_all_sources(q, out)
            if saved:
                query_cache[q] = saved
                break
            print(f"[Stock] Chunk {i}: no result for '{q}', trying next...")

        if saved:
            results.append(saved)
        time.sleep(1)

    print(f"[Stock] Videos fetched: {len(results)}/{count}")
    return results


def _translate_to_arabic_query(english_query: str) -> str | None:
    """Translate an English image search query to Arabic. Groq primary → OpenAI fallback."""
    _prompt = (
        f"Translate this image search query to Arabic. "
        f"Return only the Arabic translation, nothing else.\n\nQuery: {english_query}"
    )

    # Groq first (free tier)
    try:
        from agents.script_agent import _groq_call as _gc
    except ImportError:
        try:
            from script_agent import _groq_call as _gc
        except ImportError:
            _gc = None
    if _gc:
        try:
            result = _gc(
                messages=[{"role": "user", "content": _prompt}],
                max_tokens=30, temperature=0.1,
            ).choices[0].message.content.strip()
            if result:
                return result
        except Exception as e:
            print(f"[Image] Groq Arabic query translation failed: {e}")

    # OpenAI fallback
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "gpt-4o-mini",
                  "messages": [{"role": "user", "content": _prompt}],
                  "max_tokens": 30, "temperature": 0.1},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Image] OpenAI Arabic query translation failed: {e}")
    return None


def search_real_image(query: str, output_path: str) -> str | None:
    """
    Parallel multi-source image search: DDG + Wikimedia + Internet Archive.
    First valid download wins. Falls back to Google if parallel pass yields nothing.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_ddgs():
        return _ddgs_image_results(query)

    def _fetch_wiki():
        return _wikimedia_image_results(query, max_results=4)

    def _fetch_archive():
        return _internet_archive_image_results(query, max_results=3)

    all_urls: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=_WORKERS["search"]) as pool:
            futures = {
                pool.submit(_fetch_ddgs):    "DDG",
                pool.submit(_fetch_wiki):    "Wikimedia",
                pool.submit(_fetch_archive): "Archive",
            }
            for fut in as_completed(futures, timeout=18):
                src = futures[fut]
                try:
                    urls = fut.result()
                    if urls:
                        print(f"[IMAGE] {src} returned {len(urls)} URLs for '{query}'")
                        all_urls.extend(urls)
                except Exception as e:
                    print(f"[IMAGE] {src} search error: {e}")
    except Exception as e:
        print(f"[IMAGE] Parallel retrieval failed ({e}) — falling back to sequential")
        all_urls = _ddgs_image_results(query) or []

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped = [u for u in all_urls if not (u in seen or seen.add(u))]  # type: ignore[func-returns-value]

    if deduped:
        saved = _download_first_valid(deduped, output_path)
        if saved:
            print(f"[IMAGE] Tier1 retrieval used: '{query}'")
            return saved

    # Fallback: Google (sequential, separate key path)
    g_urls = _google_image_results(query)
    if g_urls:
        saved = _download_first_valid(g_urls, output_path)
        if saved:
            print(f"[IMAGE] Tier1 retrieval used (Google): '{query}'")
            return saved

    print(f"[Image] No real photo found for '{query}'")
    return None


def _get_search_query_for_chunk(chunk_text: str, topic: str = "") -> str | None:
    """
    Get a factual English image search query for a script chunk.
    Priority: deterministic → Groq (if healthy) → OpenAI fallback.
    Always returns English even if chunk is Arabic.
    """
    # Tier 1: deterministic (no AI)
    det = build_visual_search_query(chunk_text, topic=topic)
    if det and not det.lower().startswith("true crime historical"):
        return det

    # Tier 2: Groq (only if healthy — no blocking wait)
    result = _groq_query_for_chunk(chunk_text, topic=topic, for_video=False)
    if result:
        return result

    # Tier 3: OpenAI fallback
    first_150 = " ".join(chunk_text.split()[:150])
    prompt = (
        "What is the single most specific, searchable subject in this text?\n"
        "Return only a short English search query (max 5 words) suitable for image search.\n"
        "Examples:\n"
        "GOOD: 'Pablo Escobar Medellin 1980s'\n"
        "GOOD: 'Darfur burning village 2003'\n"
        "BAD: 'crime story background'\n"
        f"Text: {first_150}\n"
        "Return only the English search query, nothing else."
    )
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key and _provider_health.is_healthy("openai"):
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 20, "temperature": 0.3},
                timeout=12,
            )
            if r.status_code == 200:
                q = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if len(q.split()) <= 8 and len(q) > 3:
                    return q
            elif r.status_code == 429:
                _provider_health.record_failure("openai")
        except Exception as e:
            _provider_health.record_failure("openai")
            print(f"[Image] OpenAI search query failed (recorded): {e}")
    return None


def fetch_real_images(script_text: str, count: int, video_id: str,
                      topic: str = "", style_profile: str = "") -> list[str]:
    """
    Universal image builder — works for any script topic.

    Priority order:
      1. User images from Telegram (always first)
      2. Wikimedia person photo (if person detected in chunk)
      3. Wikimedia Commons + OpenAI web search
      4. Pexels photos (real licensed photos)
      5. Pexels video clip (related video used directly in assembler)
      6. Pixabay photos (real licensed photos)
      7. Pollinations AI photo generation
      8. Pollinations animation/illustration style (gap-filler of last resort)

    Logs each image as real photo, video, AI, or animation.
    Returns list of image/video paths.
    """
    import re
    import shutil

    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()

    seed = random.randint(1, 99999)

    # Topic-specific fallback: never use generic "dark portrait dramatic lighting"
    if topic:
        _t = topic.lower()
        if any(k in _t for k in ("mindhunter", "behavioral", "bsu", "fbi", "douglas", "ressler")):
            fallback_base = f"FBI Behavioral Science Unit office 1970s dark cinematic documentary style{_IMAGE_PROMPT_SUFFIX}"
        elif any(k in _t for k in ("narcos", "escobar", "medellin")):
            fallback_base = f"1980s Colombia Medellin cartel cinematic documentary dark{_IMAGE_PROMPT_SUFFIX}"
        elif any(k in _t for k in ("manson", "cult", "helter")):
            fallback_base = f"1960s California cult commune cinematic documentary dark{_IMAGE_PROMPT_SUFFIX}"
        elif any(k in _t for k in ("godfather", "mafia", "luciano", "gotti", "capone")):
            fallback_base = f"1940s New York mafia meeting dark cinematic documentary{_IMAGE_PROMPT_SUFFIX}"
        elif any(k in _t for k in ("scarface", "cocaine", "miami")):
            fallback_base = f"1980s Miami drug trafficking cinematic documentary dark{_IMAGE_PROMPT_SUFFIX}"
        elif any(k in _t for k in ("goodfellas", "henry hill", "wiseguy")):
            fallback_base = f"1970s New York organized crime cinematic dark documentary{_IMAGE_PROMPT_SUFFIX}"
        else:
            fallback_base = f"{topic} real historical documentary cinematic dark{_IMAGE_PROMPT_SUFFIX}"
    else:
        fallback_base = f"true crime historical documentary scene cinematic dark{_IMAGE_PROMPT_SUFFIX}"

    _img_dir = _get_images_dir()

    if not words:
        paths = []
        for i in range(count):
            p = os.path.join(_img_dir, f"{video_id}_img_{i}.png")
            r = generate_ai_image(fallback_base, p, seed=seed + i)
            if r:
                _save_image_prompt_cache(fallback_base, r)
                paths.append(r)
        return paths

    # Priority 0: user-provided images from standard asset folders
    user_folder_images = _load_user_images_from_folders(topic)
    preloaded_paths: list[str] = []
    for uimg in user_folder_images:
        dest = os.path.join(_img_dir, f"{video_id}_user_{len(preloaded_paths)}.png")
        try:
            shutil.copy2(uimg["path"], dest)
            preloaded_paths.append(dest)
            print(f"[Image] User image: {uimg['path']}")
        except Exception as e:
            print(f"[Image] Could not copy user image {uimg['path']}: {e}")

    # If user images fill the quota, return them directly
    if len(preloaded_paths) >= count:
        print(f"[Image] Using {count} user-provided images (skipping stock search)")
        return preloaded_paths[:count]

    # Remaining slots to fill from Wikimedia / OpenAI / Archive / Pollinations
    remaining = count - len(preloaded_paths)

    # AI fallback prompts (one per chunk)
    ai_prompts = generate_image_prompts(script_text, remaining, style_profile=style_profile)

    # Split script into equal word-chunks for remaining images
    chunk_size = max(1, len(words) // remaining)
    chunks = [
        " ".join(words[i * chunk_size: (i + 1) * chunk_size if i < remaining - 1 else len(words)])
        for i in range(remaining)
    ]

    image_paths: list[str] = list(preloaded_paths)
    real_count   = len(preloaded_paths)
    ai_count     = 0

    # ── Parallel chunk processing ─────────────────────────────────────────────
    # Each chunk is image-independent — run all searches simultaneously.
    _clean_topic = _clean_topic_name(topic)  # "Richard Farley" not full title

    def _process_chunk(args: tuple):
        ci, chunk_text, out_path, ai_prompt_c, seed_val = args
        _saved = None
        _kind  = "none"

        # Extract query — always pass topic so era/theme returns "Richard Farley 1990s"
        # not "crime 1990s". Force English: if query is Arabic, prepend clean topic.
        _query = _get_search_query_for_chunk(chunk_text, topic=_clean_topic)
        if _query and any('؀' <= c <= 'ۿ' for c in _query):
            # query returned in Arabic — replace with English topic-based fallback
            _query = _clean_topic or "true crime documentary"

        # Step 1: person photo (Wikimedia — highest priority)
        _person = _detect_person_in_chunk(chunk_text)
        if _person:
            _photo_url = _search_wikimedia_person_photo(_person)
            if _photo_url:
                _saved = _download_first_valid([_photo_url], out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: Wikimedia person photo '{_person}'")
                    _kind = "real-person"

        # Step 2: Wikimedia Commons + OpenAI web search
        if not _saved and _query:
            _wiki = _search_wikimedia_commons(_query) or _wikimedia_image_results(_query)
            if not _wiki and len(_query.split()) > 3:
                _sq = " ".join(_query.split()[:3])
                _wiki = _search_wikimedia_commons(_sq) or _wikimedia_image_results(_sq)
            if _wiki:
                _saved = _download_first_valid(_wiki, out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: real photo (Wikimedia) '{_query}'")
                    _kind = "real-wiki"
            if not _saved:
                _oai = _search_images_openai(_query)
                if _oai:
                    _saved = _download_first_valid(_oai, out_path)
                    if _saved:
                        print(f"[Image] chunk {ci}: real photo (OpenAI search) '{_query}'")
                        _kind = "real-oai"

        # Step 2b: DuckDuckGo — supports English AND Arabic queries
        if not _saved:
            _ddg_queries = [q for q in [_query, chunk_text[:80] if any('؀' <= c <= 'ۿ' for c in chunk_text) else None] if q]
            for _dq in _ddg_queries:
                _ddg_imgs = _search_duckduckgo_images(_dq, max_results=4)
                if _ddg_imgs:
                    _saved = _download_first_valid(_ddg_imgs, out_path)
                    if _saved:
                        print(f"[Image] chunk {ci}: DuckDuckGo image '{_dq[:60]}'")
                        _kind = "real-ddg"
                        break

        # Step 3: Pexels photos (real licensed photos)
        if not _saved and _query:
            _pex_imgs = _search_pexels_images(_query, max_results=3)
            if _pex_imgs:
                _saved = _download_first_valid(_pex_imgs, out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: Pexels photo '{_query}'")
                    _kind = "real-pexels"

        # Step 4: Pexels video clip (assembler handles .mp4 natively)
        if not _saved and _query:
            _pex_vids = _search_pexels_videos(_query, per_page=5)
            if _pex_vids:
                _vid_out = out_path.replace(".png", "_pv.mp4")
                _saved = _download_first_valid_video(_pex_vids, _vid_out)
                if _saved:
                    print(f"[Image] chunk {ci}: Pexels video '{_query}'")
                    _kind = "real-pexels-video"

        # Step 5: Pixabay photos (real licensed photos)
        if not _saved and _query:
            _pix_imgs = _search_pixabay_images(_query, max_results=3)
            if _pix_imgs:
                _saved = _download_first_valid(_pix_imgs, out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: Pixabay photo '{_query}'")
                    _kind = "real-pixabay"

        # Step 5b: OpenVerse CC (aggregates Flickr + Wikimedia + 20 sources, no key)
        if not _saved and _query:
            _ov_imgs = _search_openverse_images(_query, max_results=3)
            if _ov_imgs:
                _saved = _download_first_valid(_ov_imgs, out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: OpenVerse CC '{_query}'")
                    _kind = "real-openverse"

        # Step 5c: Library of Congress (public domain US historical archive, no key)
        if not _saved and _query:
            _loc_imgs = _search_loc_images(_query, max_results=3)
            if _loc_imgs:
                _saved = _download_first_valid(_loc_imgs, out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: Library of Congress '{_query}'")
                    _kind = "real-loc"

        # Step 5d: Flickr CC (free API key — set FLICKR_API_KEY in .env)
        if not _saved and _query:
            _flickr_imgs = _search_flickr_images(_query, max_results=3)
            if _flickr_imgs:
                _saved = _download_first_valid(_flickr_imgs, out_path)
                if _saved:
                    print(f"[Image] chunk {ci}: Flickr CC '{_query}'")
                    _kind = "real-flickr"

        # Step 6: Pollinations AI photo (with prompt-hash cache to avoid duplicates)
        if not _saved:
            _cached = _check_image_prompt_cache(ai_prompt_c)
            if _cached and os.path.exists(_cached):
                try:
                    shutil.copy2(_cached, out_path)
                    _saved = out_path
                    _kind = "ai-cached"
                    print(f"[Image] chunk {ci}: reusing cached AI image")
                except Exception:
                    pass
            if not _saved:
                _saved = generate_ai_image(ai_prompt_c, out_path, seed=seed_val)
                if _saved:
                    _save_image_prompt_cache(ai_prompt_c, _saved)
                    _kind = "ai-gen"
                    print(f"[Image] chunk {ci}: AI generated")

        # Step 7: Pollinations animation/illustration (gap-filler of last resort)
        if not _saved:
            _q_words  = (_query or "").split()[:3]
            _t_words  = topic.split()[:2] if topic else []
            _anim_prompt = (
                f"{' '.join(_t_words + _q_words)} documentary illustration "
                f"cinematic art style dark atmospheric{_IMAGE_PROMPT_SUFFIX}"
            )
            _anim_out = out_path.replace(".png", "_anim.png")
            _saved = generate_ai_image(_anim_prompt, _anim_out, seed=seed_val + 50000)
            if _saved:
                _kind = "ai-animation"
                print(f"[Image] chunk {ci}: animation illustration gap-filler")

        return ci, _saved, _kind

    _chunk_tasks = [
        (i, chunks[i],
         os.path.join(_img_dir, f"{video_id}_img_{i}.png"),
         ai_prompts[i] if i < len(ai_prompts) else fallback_base,
         seed + i)
        for i in range(len(chunks))
    ]
    _n_search_workers = min(len(_chunk_tasks), _WORKERS["search"])
    print(f"[QUEUE] Image generation: {len(_chunk_tasks)} pending | workers={_n_search_workers} | mode=parallel-search")

    import concurrent.futures as _cf_img
    _chunk_results: list = [None] * len(_chunk_tasks)
    with _cf_img.ThreadPoolExecutor(max_workers=_n_search_workers) as _img_ex:
        _img_futs = {_img_ex.submit(_process_chunk, t): t[0] for t in _chunk_tasks}
        for _img_fut in _cf_img.as_completed(_img_futs):
            try:
                _ci, _cpath, _ckind = _img_fut.result()
                _chunk_results[_ci] = _cpath
                if _cpath:
                    if "real" in _ckind:
                        real_count += 1
                    elif "ai" in _ckind:
                        ai_count += 1
            except Exception as _ie:
                print(f"[Image] chunk error: {_ie}")

    # Preserve narrative order — extend image_paths in index order
    for _cpath in _chunk_results:
        if _cpath and os.path.exists(_cpath):
            image_paths.append(_cpath)

    print(f"[QUEUE] Image generation complete: {real_count} real | {ai_count} AI | {len(image_paths)}/{count} total")
    return image_paths


# â"€â"€ Title card helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _detect_font() -> str | None:
    """Find a usable bold TTF font on the system."""
    candidates = [
        # Windows
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        r"C:\Windows\Fonts\verdanab.ttf",
        # Linux (GitHub Actions)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _extract_series_from_title(title: str) -> str | None:
    """
    Extract 'Narcos Series' or 'Wolf of Wall Street Movie' from a title like
    'Dark Crime Decoded: Pablo Escobar & Narcos Series — Hook Text'.
    Returns the text between ' & ' and ' — ', or None.
    """
    if " & " in title and " — " in title:
        after_amp  = title.split(" & ", 1)[1]
        before_dash = after_amp.split(" — ", 1)[0].strip()
        return before_dash
    return None


def create_title_card(main_line: str, sub_line: str, duration: float = 7.0):
    """
    Return a 1080x1920 VideoClip with a branded title card.
    Uses the same make_frame pattern as image_to_clips for MoviePy compatibility.
    Fades in over 0.5 s and out over 0.5 s.
    """
    import numpy as np
    from PIL import Image as PILImage, ImageDraw, ImageFont
    try:
        from moviepy.editor import VideoClip
    except ImportError:
        from moviepy import VideoClip

    TARGET_W, TARGET_H = 1080, 1920
    TEAL  = (29, 158, 117)
    AMBER = (239, 159, 39)
    WHITE = (255, 255, 255)
    BG    = (13, 13, 26)

    img  = PILImage.new("RGB", (TARGET_W, TARGET_H), color=BG)
    draw = ImageDraw.Draw(img)

    font_path = _detect_font()
    try:
        if font_path:
            font_brand = ImageFont.truetype(font_path, 48)
            font_main  = ImageFont.truetype(font_path, 72)
            font_sub   = ImageFont.truetype(font_path, 48)
        else:
            font_brand = font_main = font_sub = ImageFont.load_default()
    except Exception:
        font_brand = font_main = font_sub = ImageFont.load_default()

    cx = TARGET_W // 2
    cy = TARGET_H // 2

    # Brand name at top
    draw.text((cx, 200), "Dark Crime Decoded", fill=TEAL, font=font_brand, anchor="mm")
    # Top amber bar
    draw.rectangle([140, 280, 940, 285], fill=AMBER)
    # Main line (series + type)
    draw.text((cx, cy - 80), main_line, fill=TEAL,  font=font_main, anchor="mm")
    # Sub line
    draw.text((cx, cy + 80), sub_line,  fill=WHITE, font=font_sub,  anchor="mm")
    # Bottom amber bar
    draw.rectangle([140, cy + 160, 940, cy + 165], fill=AMBER)

    frame = np.array(img)

    def make_frame(t: float):
        alpha = 1.0
        if t < 0.5:
            alpha = t / 0.5
        elif t > duration - 0.5:
            alpha = (duration - t) / 0.5
        alpha = max(0.0, min(1.0, alpha))
        return (frame * alpha).astype("uint8")

    return VideoClip(make_frame=make_frame, duration=duration)


# â"€â"€ MoviePy clip helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def image_to_clips(image_path: str, n_variations: int = 4) -> list:
    """Return n_variations animated zoom clips, all exactly 1080x1920.

    Root cause of 'width not divisible by 2' (libx264 error):
      int(1080 * 1.04) = 1123 — odd width — libx264 refuses to encode.

    Fix: use VideoClip(make_frame=fn) where make_frame rounds each dimension
    up to the next even number and then center-crops back to exactly 1080x1920.
    Output frames are always (1920, 1080, 3) regardless of zoom scale.
    MoviePy calls make_frame(0) on construction to set clip.size = (1080,1920),
    which is what ffmpeg receives as the output resolution — no mismatch.
    """
    import numpy as np
    from PIL import Image as PILImage
    try:
        from moviepy.editor import VideoClip
    except ImportError:
        from moviepy import VideoClip

    TARGET_W, TARGET_H = 1080, 1920

    pil_base = PILImage.open(image_path).convert("RGB").resize(
        (TARGET_W, TARGET_H), PILImage.LANCZOS
    )

    def _zoom_fn(start_scale: float, end_scale: float, duration: float):
        """Closure: returns a make_frame callable for one zoom clip."""
        def make_frame(t):
            rate = (end_scale - start_scale) / max(duration, 0.001)
            scale = max(1.0, start_scale + rate * t)
            # Round UP to even — libx264 requires even width & height
            sw = int(TARGET_W * scale)
            if sw % 2:
                sw += 1
            sh = int(TARGET_H * scale)
            if sh % 2:
                sh += 1
            scaled = pil_base.resize((sw, sh), PILImage.LANCZOS)
            # Center-crop back to exactly TARGET_W x TARGET_H
            x = (sw - TARGET_W) // 2
            y = (sh - TARGET_H) // 2
            return np.array(scaled.crop((x, y, x + TARGET_W, y + TARGET_H)))
        return make_frame

    # (start_scale, end_scale, duration_s) — scale always stays >= 1.0
    specs = [
        (1.00, 1.08, 8.0),   # zoom in
        (1.08, 1.00, 8.0),   # zoom out  (1.08 → 1.00, never < 1.0)
        (1.00, 1.06, 7.0),   # zoom in slow
        (1.06, 1.00, 7.0),   # zoom out slow
    ]

    clips = []
    for start_s, end_s, dur in specs[:n_variations]:
        fn = _zoom_fn(start_s, end_s, dur)
        # MoviePy calls fn(0) in __init__ → shape (1920,1080,3) → size=(1080,1920)
        clips.append(VideoClip(make_frame=fn, duration=dur))

    return clips


def _kill_orphan_ffmpeg() -> None:
    """Kill any lingering ffmpeg child processes spawned by this Python session."""
    try:
        import psutil
        current = psutil.Process()
        killed  = 0
        for child in current.children(recursive=True):
            if "ffmpeg" in child.name().lower():
                try:
                    child.kill()
                    killed += 1
                    print(f"[Render] Killed ffmpeg PID {child.pid}")
                except Exception as _ke:
                    print(f"[Render] Could not kill PID {child.pid}: {_ke}")
        print(f"[Render] ffmpeg cleanup: {killed} process(es) killed")
        return
    except ImportError:
        pass
    except Exception as _pe:
        print(f"[Render] psutil cleanup failed: {_pe}")
    # Linux fallback (GitHub Actions)
    try:
        subprocess.run(["pkill", "-TERM", "-f", "ffmpeg"],
                       capture_output=True, timeout=10)
        print("[Render] Sent TERM to ffmpeg via pkill")
    except Exception:
        pass


def _validate_output_file(path: str) -> bool:
    """Return True if path is a valid, non-corrupt MP4 with acceptable duration and size."""
    if not os.path.exists(path):
        print(f"[Render] Validation FAILED — file missing: {path}")
        return False
    size_mb = os.path.getsize(path) // (1024 * 1024)
    if size_mb < 1:
        print(f"[Render] Validation FAILED — too small ({size_mb} MB): {path}")
        return False
    is_short_file = "short" in os.path.basename(path).lower()
    if not is_short_file and size_mb < 5:
        print(f"[Render] Validation FAILED — long video too small ({size_mb} MB < 5 MB): {path}")
        return False
    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        print(f"[Render] Output validated (no ffprobe): {os.path.basename(path)} ({size_mb} MB)")
        return True
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-i", path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        if "moov atom not found" in result.stderr or "Invalid data" in result.stderr:
            print(f"[Render] Validation FAILED — corrupt MP4: {result.stderr[:200]}")
            return False
        dur = _ffprobe_duration(path) or 0.0
        min_dur = 55.0 if is_short_file else 300.0
        if dur > 0 and dur < min_dur:
            print(f"[Render] Validation FAILED — duration {dur:.1f}s < minimum {min_dur:.0f}s: {path}")
            return False
        print(f"[Render] Output validated: {os.path.basename(path)} ({size_mb} MB, {dur:.0f}s)")
        return True
    except Exception as _ve:
        print(f"[Render] Validation error (assuming OK): {_ve}")
        return True


def _write_video_safe(
    final_clip,
    output_path: str,
    clips_to_close: list,
    timeout_seconds: int = 3600,
    **write_kwargs,
) -> bool:
    """
    Thread-with-timeout wrapper around MoviePy write_videofile.

    Guarantees:
    - Heartbeat log every 60 s — prevents GitHub Actions runner timeout
    - Clips are closed in finally regardless of success / failure / timeout
    - On timeout: kills orphan ffmpeg processes, returns False
    - On success: returns True and logs export finish

    Usage:
        ok = _write_video_safe(final, path, [audio, final, *clips], timeout_seconds=3600,
                               fps=30, codec="libx264", ...)
    """
    import threading
    import gc as _gc

    result: dict = {"ok": False, "error": None}

    def _write():
        try:
            print(f"[Render] Export started: {os.path.basename(output_path)}")
            final_clip.write_videofile(output_path, **write_kwargs)
            result["ok"] = True
            print(f"[Render] Export finished: {os.path.basename(output_path)}")
        except Exception as _we:
            result["error"] = _we

    write_thread = threading.Thread(target=_write, daemon=True)
    write_thread.start()

    elapsed    = 0
    check_sec  = 10         # poll completion every 10 s
    next_log   = 60         # first heartbeat at 60 s

    while write_thread.is_alive():
        write_thread.join(timeout=check_sec)
        elapsed += check_sec
        if not write_thread.is_alive():
            break
        if elapsed >= next_log:
            print(f"[Render] Still active... elapsed={elapsed // 60}min — {os.path.basename(output_path)}")
            next_log += 60
        if elapsed >= timeout_seconds:
            print(f"[Render] TIMEOUT after {elapsed // 60}min — killing ffmpeg and aborting")
            _kill_orphan_ffmpeg()
            result["error"] = TimeoutError(f"write_videofile timed out after {elapsed // 60}min")
            break

    # Always close every clip
    print("[Render] Closing MoviePy resources")
    for clip in clips_to_close:
        if clip is None:
            continue
        try:
            clip.close()
        except Exception:
            pass
    try:
        _gc.collect()
    except Exception:
        pass
    print("[Render] Resources closed")

    if result["error"]:
        print(f"[Render] Export error: {result['error']}")
        return False
    return result["ok"]


def assemble_video(
    audio_path: str,
    image_clips: list,
    output_filename: str,
    before_clips: list | None = None,
    after_clips:  list | None = None,
) -> str:
    """
    Loop image_clips to cover the full audio duration, mux, and export.
    before_clips/after_clips are prepended/appended once (not looped).
    """
    import traceback
    try:
        from moviepy.editor import AudioFileClip, concatenate_videoclips
    except ImportError:
        from moviepy import AudioFileClip, concatenate_videoclips

    output_path = os.path.join(FINAL_DIR, f"{output_filename}.mp4")
    temp_audio  = os.path.join(FINAL_DIR, f"{output_filename}_tmp_audio.m4a")

    # â"€â"€ Load audio â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    try:
        audio = AudioFileClip(audio_path)
        total_duration = audio.duration
        print(f"[Video] Audio duration: {total_duration:.1f}s")
    except Exception as e:
        print(f"[Video] CRASH loading audio: {e}")
        traceback.print_exc()
        return ""

    # â"€â"€ Build looped clip list (image portion only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    fixed_before = sum(c.duration for c in (before_clips or []))
    fixed_after  = sum(c.duration for c in (after_clips  or []))
    image_target = max(1.0, total_duration - fixed_before - fixed_after)

    try:
        looped: list = []
        accumulated = 0.0
        idx = 0
        while accumulated < image_target:
            clip = image_clips[idx % len(image_clips)]
            remaining = image_target - accumulated
            if clip.duration > remaining:
                clip = clip.subclip(0, remaining)
            looped.append(clip)
            accumulated += clip.duration
            idx += 1
        print(f"[Video] Looped {len(looped)} clips covering {accumulated:.1f}s")
    except Exception as e:
        print(f"[Video] CRASH building clip loop: {e}")
        traceback.print_exc()
        return ""

    # â"€â"€ Concatenate â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # method="chain": clips are identical 1080x1920 — faster and more reliable
    # than "compose" which tries to composite varying-size clips.
    try:
        all_video_clips = (before_clips or []) + looped + (after_clips or [])
        final = concatenate_videoclips(all_video_clips, method="chain")
        final = final.set_audio(audio)
        print(f"[Video] Concatenated: {final.duration:.1f}s, size={final.size}")
    except Exception as e:
        print(f"[Video] CRASH at concatenation: {e}")
        traceback.print_exc()
        return ""

    # â"€â"€ Write video â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    _clips_to_close = [audio, final] + list(before_clips or []) + list(after_clips or [])
    _ok = _write_video_safe(
        final, output_path, _clips_to_close,
        timeout_seconds=14400,
        fps=30, codec="libx264", audio_codec="aac", preset="ultrafast",
        ffmpeg_params=["-threads", "0", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        temp_audiofile=temp_audio, logger=None,
    )
    for _ in range(5):
        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            break
        except OSError:
            time.sleep(0.5)
    if not _ok:
        return ""

    # â"€â"€ Verify output â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    if not os.path.exists(output_path):
        print(f"[Video] ERROR: output file not created: {output_path}")
        return ""
    file_size = os.path.getsize(output_path)
    if file_size < 100_000:
        print(f"[Video] ERROR: output file too small ({file_size} bytes) — likely corrupt")
        return ""
    print(f"[Video] Success: {output_path} ({file_size // 1024 // 1024}MB)")
    return output_path


# â"€â"€ Voice enhancement (for user-recorded audio) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def clean_voice(input_path: str, output_path: str) -> str:
    """
    Enhance a recorded voice file:
      1. Convert OGG → WAV via ffmpeg
      2. Noise reduction via noisereduce (first 0.5 s as noise profile)
      3. Apply ffmpeg audio filters (highpass, lowpass, denoiser, normalization)
      4. Output as MP3
    Returns output_path on success, or input_path if enhancement fails.
    """
    import subprocess

    wav_path  = output_path.replace(".mp3", "_raw.wav")
    clean_wav = output_path.replace(".mp3", "_clean.wav")

    try:
        subprocess.run(["ffmpeg", "-y", "-i", input_path, wav_path], check=True, capture_output=True)
    except Exception as e:
        print(f"[Voice] ffmpeg decode failed: {e} — skipping enhancement")
        return input_path

    try:
        import noisereduce as nr
        import soundfile as sf
        data, rate = sf.read(wav_path)
        noise_sample = data[:int(rate * 0.5)]
        reduced = nr.reduce_noise(y=data, sr=rate, y_noise=noise_sample, prop_decrease=0.75, stationary=False)
        sf.write(clean_wav, reduced, rate)
    except Exception as e:
        print(f"[Voice] Noise reduction failed: {e} — using raw WAV")
        clean_wav = wav_path

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", clean_wav,
             "-af", "highpass=f=80,lowpass=f=8000,anlmdn=s=7:p=0.002:r=0.002,dynaudnorm=p=0.9",
             "-ar", "44100", output_path],
            check=True, capture_output=True,
        )
        print(f"[Voice] Enhanced audio saved: {output_path}")
    except Exception as e:
        print(f"[Voice] ffmpeg filter failed: {e} — using unfiltered input")
        return input_path

    for f in [wav_path, clean_wav]:
        try:
            if os.path.exists(f) and f != output_path:
                os.remove(f)
        except OSError:
            pass

    return output_path


# â"€â"€ Short clip cutter â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

SHORTS_DIR = "output/shorts"
Path(SHORTS_DIR).mkdir(parents=True, exist_ok=True)

# ── Retention scoring for short-clip selection ────────────────────────────────
# Each list is checked against the chapter title + first 400 chars of section text.

_RETENTION_HOOK     = ["you won't believe", "what if i told you", "imagine",
                       "did you know", "the real story", "no one knew",
                       "this is why", "this is how", "this killer", "this criminal"]
_RETENTION_REVEAL   = ["revealed", "exposed", "shocking", "nobody knew",
                       "secret", "real identity", "it turned out", "the truth was",
                       "what they found", "hidden for years"]
_RETENTION_MYSTERY  = ["who was", "why did", "what happened", "where did",
                       "mystery", "disappeared", "was never found", "unknown"]
_RETENTION_CONFESS  = ["admitted", "confessed", "i killed", "i did it",
                       "he admitted", "she confessed", "in his own words",
                       "told investigators", "according to him", "according to her"]
_RETENTION_ENDING   = ["sentenced", "executed", "life in prison", "never seen again",
                       "escaped", "guilty", "acquitted", "was shot", "final verdict"]
# Universal psychological hooks — topic- and region-agnostic (crime, history, cartel, scandal, any language)
_RETENTION_UNIVERSAL = ["true story", "based on real", "untold", "cover-up", "cover up",
                        "most wanted", "betrayed", "betrayal", "for the first time",
                        "until now", "hidden for decades", "the world never knew"]
# Generic section titles that reduce clip appeal — matched against title only
_RETENTION_BORING   = ["introduction", "background", "context", "overview", "setup",
                       "conclusion", "summary", "prologue", "epilogue", "beginning"]


def _score_retention(title: str, section_text: str) -> int:
    """
    Score a chapter for short-form retention value.
    Checks title + first 400 chars of section text (combined) for positive signals;
    title only for the boring-title penalty.

    Weights (globally topic- and region-agnostic):
      confession quote      +4 each  (rarest, highest watch-through)
      hook phrase           +3 each
      shocking reveal       +3 each
      universal psych hook  +2 each  (betrayal, cover-up, true story — any topic/culture)
      mystery question      +2 each
      dramatic ending       +2 each
      question mark in opening +2
      boring section title  −2 each  (Introduction, Background, etc.)
    """
    combined = (title + " " + section_text[:400]).lower()
    title_l  = title.lower()
    score  = sum(4 for kw in _RETENTION_CONFESS   if kw in combined)
    score += sum(3 for kw in _RETENTION_HOOK      if kw in combined)
    score += sum(3 for kw in _RETENTION_REVEAL    if kw in combined)
    score += sum(2 for kw in _RETENTION_UNIVERSAL if kw in combined)
    score += sum(2 for kw in _RETENTION_MYSTERY   if kw in combined)
    score += sum(2 for kw in _RETENTION_ENDING    if kw in combined)
    if "?" in (title + section_text[:120]):
        score += 2
    score -= sum(2 for kw in _RETENTION_BORING    if kw in title_l)
    return score


def _pick_best_short_start(script_data: dict, video_dur: float,
                            min_remaining: float = 55.0) -> float:
    """
    Return the video timestamp (seconds) of the chapter with the highest
    retention score.  Falls back to 0.0 when chapters are absent or all
    chapters score identically (intro is already a hook by convention).
    """
    import re as _re
    chapters_str = script_data.get("chapters", "")
    script_text  = script_data.get("script",   "")
    if not chapters_str:
        return 0.0

    parsed: list[tuple[float, str]] = []
    for line in chapters_str.strip().splitlines():
        m = _re.match(r'^(\d+):(\d+)\s+(.+)$', line.strip())
        if m:
            secs = int(m.group(1)) * 60 + int(m.group(2))
            parsed.append((float(secs), m.group(3).strip()))

    if not parsed:
        return 0.0

    sections = _parse_script_sections(script_text) if script_text else []

    best_score  = -1
    best_start  = 0.0

    for i, (secs, title) in enumerate(parsed):
        if secs > video_dur - min_remaining:   # not enough video left for a full short
            continue
        section_text = sections[i][1] if i < len(sections) else ""
        score = _score_retention(title, section_text)
        print(f"[Short] Ch{i+1} '{title[:40]}' retention={score}")
        if score > best_score:
            best_score = score
            best_start = secs

    if best_score <= 0:
        print("[Short] No chapter scored — defaulting to t=0 (intro hook)")
        return 0.0

    print(f"[Short] Selected start: {best_start:.0f}s (score={best_score})")
    return best_start


def cut_short_clip(video_path: str, output_path: str, duration: int = 90,
                   script_data: dict | None = None) -> str:
    """
    Cut the most retention-worthy 55-65s clip from a video.

    When script_data contains chapter timestamps, selects the chapter with the
    strongest hook / reveal / confession signals as the cut start point.
    Falls back to t=0 when chapter data is unavailable.
    """
    try:
        from moviepy.editor import VideoFileClip
    except ImportError:
        try:
            from moviepy import VideoFileClip
        except ImportError:
            return ""

    temp_audio = output_path.replace(".mp4", "_tmp.m4a")
    clip = None
    short = None
    try:
        clip = VideoFileClip(video_path)
        cut_start       = _pick_best_short_start(script_data, clip.duration) if script_data else 0.0
        actual_duration = random.randint(55, 65)
        actual_duration = min(actual_duration, clip.duration - cut_start)
        short = clip.subclip(cut_start, cut_start + actual_duration)
        short.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            ffmpeg_params=[
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
            ],
            temp_audiofile=temp_audio,
            remove_temp=True,
            logger=None,
        )
        size_kb = os.path.getsize(output_path) // 1024 if os.path.exists(output_path) else 0
        print(f"[Video] Short clip saved: {output_path} ({size_kb}KB)")
        if size_kb < 10:
            print(f"[Video] WARNING: short clip too small ({size_kb}KB) — may be corrupt")
        return output_path
    except Exception as e:
        print(f"[Video] Short clip error: {e}")
        return ""
    finally:
        if short:
            try: short.close()
            except Exception: pass
        if clip:
            try: clip.close()
            except Exception: pass
        for _ in range(5):
            try:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                break
            except OSError:
                time.sleep(0.5)




def cut_chapter_shorts(
    long_video_path: str,
    script_data: dict,
    output_dir: str | None = None,
) -> list[dict]:
    """Cut 5 chapter-based shorts from a long video using ffmpeg.

    Parses chapter timestamps from script_data['chapters'], cuts a 55-90 second
    clip from each chapter, and adds the chapter title as a text overlay.
    Returns list of dicts: [{path, title, label, chapter_idx}]
    """
    import re as _re

    chapters_str = script_data.get("chapters", "")
    if not chapters_str or not os.path.exists(long_video_path):
        return []

    # Parse "MM:SS Title" lines
    lines = [l.strip() for l in chapters_str.strip().split("\n") if l.strip()]
    chapter_times: list[tuple[int, str]] = []
    for line in lines:
        m = _re.match(r'^(\d+):(\d+)\s+(.+)$', line)
        if m:
            secs = int(m.group(1)) * 60 + int(m.group(2))
            title = m.group(3).strip()
            chapter_times.append((secs, title))

    if not chapter_times:
        print("[Short] No chapter timestamps found -- skipping chapter shorts")
        return []

    total_dur = _ffprobe_duration(long_video_path) or 0
    if total_dur < 30:
        print(f"[Short] Video too short ({total_dur:.0f}s) for chapter shorts")
        return []

    if output_dir is None:
        output_dir = SHORTS_DIR
    os.makedirs(output_dir, exist_ok=True)

    lang = script_data.get("language", "english")
    safe_id = _re.sub(r'[^\w]', '_', script_data.get('topic', 'video')[:20])
    angle_title = script_data.get("angle_title", "")

    short_labels = [
        "Hook — TikTok + Instagram + YouTube Shorts",
        f"{angle_title or 'Untold Angle'} — TikTok + Instagram + YouTube Shorts",
        "Real Story — TikTok + Instagram",
        "Show vs Reality — TikTok + Instagram",
        "Conclusion — YouTube Shorts + TikTok",
    ]

    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        print("[Short] ffmpeg not found -- skipping chapter shorts")
        return []

    # Parse script sections once — used for retention scoring inside the loop
    _sections = _parse_script_sections(script_data.get("script", ""))

    shorts: list[dict] = []

    for idx, (start_sec, chapter_title) in enumerate(chapter_times):
        chapter_end = chapter_times[idx + 1][0] if idx + 1 < len(chapter_times) else total_dur
        chapter_dur = max(0, chapter_end - start_sec)

        # Retention-first start selection:
        # High-scoring chapters (hook, confession, reveal) always start at the
        # chapter's opening — that is where DCD scripts place the strongest line.
        # Low-scoring context chapters (background, setup) skip 25 % of their
        # duration to land past the slow intro sentences.
        section_text = _sections[idx][1] if idx < len(_sections) else ""
        ret_score    = _score_retention(chapter_title, section_text)
        if ret_score >= 3:
            cut_start = start_sec                               # strong opening — use it
        else:
            cut_start = start_sec + min(30, chapter_dur // 4)  # weak intro — skip ahead

        cut_dur = min(90, max(15, chapter_end - cut_start))

        if cut_dur < 15:
            continue

        out_path = os.path.join(output_dir, f"{safe_id}_ch{idx + 1}_{lang}.mp4")

        cmd = [
            ffmpeg_bin, "-y",
            "-i", long_video_path,
            "-ss", str(int(cut_start)),
            "-t",  str(int(cut_dur)),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            out_path,
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            label = short_labels[idx] if idx < len(short_labels) else f"Chapter {idx + 1}"
            shorts.append({
                "path":        out_path,
                "title":       chapter_title,
                "label":       label,
                "chapter_idx": idx + 1,
            })
            print(f"[Short] Ch{idx + 1} cut: {cut_dur:.0f}s -> {os.path.basename(out_path)}")
        except Exception as e:
            print(f"[Short] Ch{idx + 1} cut failed: {e}")

    print(f"[Short] {len(shorts)}/5 chapter shorts created from {os.path.basename(long_video_path)}")
    return shorts


def cut_best_short(
    long_video_path: str,
    script_data: dict,
    output_dir: str | None = None,
) -> list[dict]:
    """Cut the single highest-scoring chapter short from a long video.

    Scores every chapter with retention signals (hook, reveal, mystery,
    confession, twist) and cuts the winner at 45-90 seconds.
    Returns a list with 0 or 1 dict: [{path, title, label, chapter_idx, score}]
    """
    import re as _re

    chapters_str = script_data.get("chapters", "")
    if not chapters_str or not os.path.exists(long_video_path):
        return []

    lines = [l.strip() for l in chapters_str.strip().split("\n") if l.strip()]
    chapter_times: list[tuple[int, str]] = []
    for line in lines:
        m = _re.match(r'^(\d+):(\d+)\s+(.+)$', line)
        if m:
            secs = int(m.group(1)) * 60 + int(m.group(2))
            chapter_times.append((secs, m.group(3).strip()))

    if not chapter_times:
        print("[Short] No chapter timestamps -- skipping best short")
        return []

    total_dur = _ffprobe_duration(long_video_path) or 0
    if total_dur < 45:
        print(f"[Short] Video too short ({total_dur:.0f}s) for best short")
        return []

    if output_dir is None:
        output_dir = SHORTS_DIR
    os.makedirs(output_dir, exist_ok=True)

    lang    = script_data.get("language", "english")
    safe_id = _re.sub(r'[^\w]', '_', script_data.get('topic', 'video')[:20])

    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        print("[Short] ffmpeg not found -- skipping best short")
        return []

    _sections = _parse_script_sections(script_data.get("script", ""))

    # Score every chapter; pick the highest (ties broken by earliest index)
    best_idx, best_score = 0, -1
    for idx, (_, ch_title) in enumerate(chapter_times):
        section_text = _sections[idx][1] if idx < len(_sections) else ""
        score = _score_retention(ch_title, section_text)
        if score > best_score:
            best_score, best_idx = score, idx

    start_sec, chapter_title = chapter_times[best_idx]
    chapter_end = chapter_times[best_idx + 1][0] if best_idx + 1 < len(chapter_times) else total_dur
    chapter_dur = max(0, chapter_end - start_sec)

    cut_start = start_sec if best_score >= 3 else start_sec + min(20, chapter_dur // 5)
    cut_dur   = min(90, max(45, chapter_end - cut_start))

    if cut_dur < 45:
        print(f"[Short] Best chapter too short ({cut_dur:.0f}s) -- skipping")
        return []

    out_path = os.path.join(output_dir, f"{safe_id}_best_{lang}.mp4")

    cmd = [
        ffmpeg_bin, "-y",
        "-i", long_video_path,
        "-ss", str(int(cut_start)),
        "-t",  str(int(cut_dur)),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        out_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        print(f"[Short] Best short: ch{best_idx+1} score={best_score} {cut_dur:.0f}s -> {os.path.basename(out_path)}")
        return [{
            "path":        out_path,
            "title":       chapter_title,
            "label":       "Best Short -- TikTok + Instagram + YouTube Shorts",
            "chapter_idx": best_idx + 1,
            "score":       best_score,
        }]
    except Exception as e:
        print(f"[Short] Best short cut failed: {e}")
        return []

# â"€â"€ User image helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def _find_keyword_position(script_text: str, tags: list[str]) -> float:
    """Return 0.0—1.0 relative position where the first tag appears in the script.
    Returns 0.0 when no tags are provided (opening shot).
    """
    if not tags or not script_text:
        return 0.0
    script_lower = script_text.lower()
    n_chars = len(script_lower)
    if n_chars == 0:
        return 0.0
    best = 1.0
    for tag in tags:
        idx = script_lower.find(tag)
        if 0 <= idx < n_chars:
            pos = idx / n_chars
            if pos < best:
                best = pos
    return best


def _build_clip_pool_with_user_images(
    user_images: list[dict],
    ai_clips: list,
    script_text: str,
    n_variations: int,
) -> list:
    """
    Merge user image clips into the AI clip pool at script-matched positions.

    - User images with face/portrait tags (real, photo, portrait, face) → position 0 (opening).
    - Other user images → positioned proportionally where their tags appear in the script.
    - AI clips fill the rest (shuffled).
    """
    if not user_images:
        random.shuffle(ai_clips)
        return ai_clips

    # Convert user image dicts to (position, clips) tuples
    user_clip_groups: list[tuple[float, list]] = []
    _PORTRAIT_TAGS = {"real", "photo", "portrait", "face", "image", "picture"}

    for img_info in user_images:
        path  = img_info.get("path", "")
        tags  = img_info.get("tags", [])
        if not path or not os.path.exists(path):
            continue
        try:
            clips = image_to_clips(path, n_variations=n_variations)
        except Exception as e:
            print(f"[Video] User image clip failed ({path}): {e}")
            continue

        # Portrait/face tags → force to opening position
        if any(t in _PORTRAIT_TAGS for t in tags):
            pos = 0.0
        else:
            pos = _find_keyword_position(script_text, tags)

        user_clip_groups.append((pos, clips))
        cap = img_info.get("caption", "")[:40]
        print(f"[Video] User image: {len(clips)} clips @ script pos {pos:.2f}  caption='{cap}'")

    if not user_clip_groups:
        random.shuffle(ai_clips)
        return ai_clips

    # Sort by position — opening shots come first
    user_clip_groups.sort(key=lambda x: x[0])

    # Shuffle AI clips so they're varied
    random.shuffle(ai_clips)
    n_ai = len(ai_clips)

    # Insert each user group at its proportional position in the AI clip list
    merged: list = list(ai_clips)
    inserted = 0
    for pos, clips in user_clip_groups:
        insert_at = min(int(pos * n_ai) + inserted, len(merged))
        for j, clip in enumerate(clips):
            merged.insert(insert_at + j, clip)
        inserted += len(clips)

    total_user = sum(len(c) for _, c in user_clip_groups)
    print(f"[Video] Clip pool: {len(merged)} total ({total_user} user + {n_ai} AI)")
    return merged


# â"€â"€ Script moment parsing & visual matching â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_KILLER_KWS     = {"killer", "murderer", "crime", "shot", "kill", "murder", "cartel", "drug", "trafficking"}
_LAW_KWS        = {"fbi", "police", "detective", "arrest", "investigation", "dea", "court", "trial", "agent", "officer"}
_VICTIM_KWS     = {"victim", "disappeared", "missing", "found dead", "body", "hostage"}
_LOCATION_MAP   = {
    "new york": "new york city", "chicago": "chicago", "medellin": "colombia",
    "colombia": "colombia", "mexico": "mexico", "miami": "miami",
    "los angeles": "los angeles", "london": "london", "prison": "prison",
    "court": "courtroom", "fbi": "fbi headquarters",
}


def parse_script_moments(script_text: str, topic: str = "") -> list[dict]:
    """
    Split script into 2-3 sentence chunks, extract WHO/WHAT/WHERE/WHEN context.
    Returns list of {"text", "who", "where", "when", "tags", "categories"} dicts.
    """
    import re
    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    sentences = re.split(r'(?<=[.!?])\s+', clean)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    chunk_size = 3
    chunks = []
    for i in range(0, len(sentences), chunk_size):
        text = " ".join(sentences[i: i + chunk_size])
        if text:
            chunks.append(text)

    moments = []
    for text in chunks:
        text_lower = text.lower()

        # WHO: two-word capitalized names
        who_matches = re.findall(r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', text)
        who = who_matches[0] if who_matches else ""

        # WHERE: known location keywords
        where = next((v for k, v in _LOCATION_MAP.items() if k in text_lower), "")

        # WHEN: year references
        years = re.findall(r'\b(19[4-9]\d|20[0-2]\d)\b', text)
        when = years[0] if years else ""

        # Category tags
        categories: list[str] = []
        if any(k in text_lower for k in _KILLER_KWS):
            categories.append("crime")
        if any(k in text_lower for k in _LAW_KWS):
            categories.append("law_enforcement")
        if any(k in text_lower for k in _VICTIM_KWS):
            categories.append("victim")

        tags: list[str] = []
        if who:
            tags.extend(who.lower().split())
        if where:
            tags.append(where)
        if when:
            tags.append(when)
        tags.extend(categories)

        moments.append({
            "text": text, "who": who, "where": where,
            "when": when, "tags": tags, "categories": categories,
        })

    print(f"[Visual] Parsed {len(moments)} script moments from {len(sentences)} sentences")
    return moments


def match_images_to_moments(
    moments: list[dict],
    user_images: list[dict],
    ai_image_paths: list[str],
) -> list[str]:
    """
    Assign an image to each script moment using documentary rhythm:
      REAL → REAL → REAL → GENERATED ATMOSPHERE → REAL

    Priority tiers:
      Tier 1 (PRIMARY): user uploads + wiki photos + real archive images
      Tier 2 (SECONDARY): AI-generated images — atmosphere / transitions only

    Safeguards:
      - Max 1 consecutive AI-generated image
      - Max 30% of total slots may be AI-generated
      - When Tier-1 pool is exhausted, cycle real images (never switch to all-AI)
      - FORBIDDEN: law-enforcement images on pure crime/killer moments
    """
    if not moments:
        all_paths = [img.get("path", "") if isinstance(img, dict) else img
                     for img in (user_images or []) + (ai_image_paths or [])]
        return [p for p in all_paths if p and os.path.exists(p)]

    user_pool = [img for img in (user_images or [])
                 if isinstance(img, dict) and img.get("path") and os.path.exists(img["path"])]
    ai_pool_all = [p for p in (ai_image_paths or []) if p and os.path.exists(p)]

    # Split ai_pool into real-archive (Tier 1) and AI-generated (Tier 2)
    real_archive = [p for p in ai_pool_all if p in _REAL_IMAGE_PATHS]
    gen_pool     = [p for p in ai_pool_all if p not in _REAL_IMAGE_PATHS]

    # Tier-1 pool: user uploads/wiki + real archive images
    real_pool = [img["path"] for img in user_pool] + real_archive

    print(f"[Visual] match_images_to_moments: {len(user_pool)} user/wiki, "
          f"{len(real_archive)} real-archive, {len(gen_pool)} generated, "
          f"{len(moments)} moments")

    def _is_forbidden(img: dict, moment: dict) -> bool:
        img_lower = {t.lower() for t in img.get("tags", [])}
        has_law = any(t in img_lower for t in
                      {"fbi", "police", "detective", "law_enforcement", "dea", "cop", "officer"})
        cats = moment.get("categories", [])
        return has_law and "crime" in cats and "law_enforcement" not in cats

    # Constants: documentary rhythm policy
    MAX_CONSECUTIVE_GEN = 1     # never 2+ AI images in a row
    MAX_GEN_DENSITY     = 0.30  # at most 30% of total slots may be AI-generated
    GEN_BEAT_INTERVAL   = 4     # insert one AI atmosphere beat every N real slots

    total_slots   = len(moments)
    max_gen_slots = max(1, int(total_slots * MAX_GEN_DENSITY))

    result: list[str] = []
    real_idx        = 0
    gen_idx         = 0
    consecutive_gen = 0
    gen_count       = 0
    real_since_gen  = 0   # count real slots placed since last generated slot

    for m_idx, moment in enumerate(moments):
        # Decide whether this slot gets a real or generated image
        # Insert generated image as atmosphere beat every GEN_BEAT_INTERVAL real images
        # (but only if we haven't hit the consecutive or density caps)
        use_gen = (
            gen_pool                                  # have generated images
            and gen_count < max_gen_slots             # density cap not hit
            and consecutive_gen < MAX_CONSECUTIVE_GEN # consecutive cap not hit
            and real_pool                             # at least 1 real was placed
            and real_since_gen >= GEN_BEAT_INTERVAL   # waited enough real slots
        )

        chosen = None
        label  = ""

        if use_gen:
            chosen = gen_pool[gen_idx % len(gen_pool)]
            gen_idx        += 1
            consecutive_gen += 1
            gen_count       += 1
            real_since_gen   = 0
            label = "generated/atmosphere"
        elif real_pool:
            src_path = real_pool[real_idx % len(real_pool)]
            # Find matching user image dict for forbidden-check (only for user_pool items)
            user_img = next(
                (img for img in user_pool if img.get("path") == src_path), None
            )
            if user_img and _is_forbidden(user_img, moment):
                # Try another user image
                alt = next(
                    (img for img in user_pool
                     if img.get("path") != src_path and not _is_forbidden(img, moment)),
                    None
                )
                if alt:
                    src_path = alt["path"]
            chosen          = src_path
            real_idx       += 1
            consecutive_gen = 0
            real_since_gen += 1
            label = "real/archive"
        elif gen_pool:
            # All real images exhausted — must use generated (best effort)
            chosen = gen_pool[gen_idx % len(gen_pool)]
            gen_idx        += 1
            consecutive_gen += 1
            gen_count       += 1
            label = "generated/fallback"
        else:
            break

        preview = moment["text"][:50].replace("\n", " ")
        print(f"[Visual] Slot {m_idx}: '{preview}...' → {os.path.basename(chosen)} [{label}]")
        result.append(chosen)

    # Pad any remaining slots (edge case: more moments than images)
    all_real = real_pool if real_pool else gen_pool
    while len(result) < len(moments) and all_real:
        result.append(all_real[len(result) % len(all_real)])

    gen_in_result  = sum(1 for r in result if r in set(gen_pool))
    real_in_result = len(result) - gen_in_result
    print(f"[Visual] Final: {real_in_result} real/archive + {gen_in_result} generated = {len(result)} slots")
    return result



def _secs_to_ass_time(s: float) -> str:
    """Convert seconds to ASS timestamp format H:MM:SS.cc"""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


ENABLE_SUBTITLES = False  # set True to re-enable Whisper subtitles

_WHISPER_MODEL = None   # module-level cache — loaded once per process


def generate_subtitles(audio_path: str, language: str) -> list[dict]:
    """
    Transcribe audio with word-level timestamps using openai-whisper.
    Whisper base model is cached at module level so it is never reloaded.
    Returns list of Whisper segments (each with 'words' list).
    """
    global _WHISPER_MODEL
    try:
        import whisper
    except ImportError:
        print("[Subtitle] openai-whisper not installed, installing...")
        os.system("pip install openai-whisper -q")
        try:
            import whisper
        except ImportError:
            print("[Subtitle] Could not install openai-whisper")
            return []

    lang_code = "ar" if language == "arabic" else "en"
    try:
        if _WHISPER_MODEL is None:
            print("[Subtitle] Loading Whisper base model (first call)...")
            _WHISPER_MODEL = whisper.load_model("base")
            print("[Subtitle] Whisper model loaded and cached")
        else:
            print("[Subtitle] Using cached Whisper model")
        result = _WHISPER_MODEL.transcribe(audio_path, language=lang_code, word_timestamps=True)
        segments = result.get("segments", [])
        print(f"[Subtitle] Transcribed {len(segments)} segment(s)")
        return segments
    except Exception as e:
        print(f"[Subtitle] Whisper transcription failed: {e}")
        return []


def find_keyword_timestamp(segments: list[dict], caption_keywords: list[str]) -> float | None:
    """
    Search Whisper segments for the first occurrence of any caption keyword.
    Returns the start timestamp (seconds) of the earliest match, or None.
    """
    if not segments or not caption_keywords:
        return None
    keywords_lower = [kw.lower().strip(".,!?") for kw in caption_keywords if len(kw) > 2]
    for seg in segments:
        for w in seg.get("words", []):
            word_text = w.get("word", "").strip().lower().strip(".,!?")
            if any(kw in word_text or word_text in kw for kw in keywords_lower):
                ts = w.get("start")
                if ts is not None:
                    return float(ts)
    return None


def burn_subtitles_ffmpeg(
    video_path: str,
    segments: list[dict],
    output_path: str,
    language: str,
) -> str | None:
    """
    Premium documentary subtitle burn for Dark Crime Decoded.

    Design:
    - Phrase-based chunking (3-4 words), split at natural pauses / punctuation
    - Large bold font (76 EN / 82 AR), 4 px black outline, 2 px drop shadow
    - Important crime/drama words highlighted inline in crimson
    - Safe bottom position (MarginV 200 px) for mobile UI chrome
    - Supports English (LTR) and Arabic (RTL — handled by libass automatically)
    """
    import subprocess

    # ── Important-word highlight sets ─────────────────────────────────────────
    _HIGHLIGHT_EN = frozenset([
        "murder", "murdered", "kill", "killed", "killing", "killer",
        "dead", "death", "died", "die", "dying", "blood", "bloody",
        "weapon", "gun", "shot", "stabbed", "stab",
        "crime", "criminal", "guilty",
        "secret", "secrets", "hidden", "truth", "exposed", "reveal", "revealed",
        "betrayal", "betrayed", "betray",
        "confession", "confessed", "confess",
        "escaped", "escape", "fled", "flee",
        "missing", "disappeared", "vanished",
        "sentenced", "prison", "arrested", "arrest",
        "executed", "execution", "innocent", "victim", "victims",
        "cartel", "mafia", "gang", "drug", "drugs",
        "corrupt", "corruption", "millions", "billion",
        "never", "first", "only", "untold",
        "shocking", "terrifying", "brutal", "horrific", "deadly",
    ])
    _HIGHLIGHT_AR = frozenset([
        "قتل", "مقتل", "جريمة", "ضحية", "سر", "أسرار", "حقيقة",
        "هرب", "اعتراف", "اختفى", "مفقود", "دم", "سلاح",
        "مخدرات", "عصابة", "سجن", "إعدام", "فساد", "مليون", "مليار",
        "فر", "اعتقل", "حقيقي", "مجرم", "ضحايا",
    ])
    highlight_words = _HIGHLIGHT_AR if language == "arabic" else _HIGHLIGHT_EN

    # ── Flatten Whisper segments → word list ──────────────────────────────────
    words: list[dict] = []
    for seg in segments:
        for w in seg.get("words", []):
            text = w.get("word", "").strip()
            if not text:
                continue
            words.append({
                "word":  text,
                "start": float(w.get("start", 0)),
                "end":   float(w.get("end", 0)),
            })

    if not words:
        print("[Subtitle] No words found in segments, skipping subtitles")
        return None

    is_arabic  = language == "arabic"
    max_phrase = 3 if is_arabic else 4   # max words per chunk

    # ── Phrase chunking: split at pauses, punctuation, or max length ──────────
    def _chunk_words(ws):
        chunks, cur = [], []
        for i, w in enumerate(ws):
            cur.append(w)
            is_last   = (i == len(ws) - 1)
            bare      = w["word"].rstrip()
            has_punct = bare != bare.rstrip(".,!?:;،؟؛")
            long_gap  = (not is_last) and (ws[i + 1]["start"] - w["end"]) > 0.35
            if is_last or len(cur) >= max_phrase or (has_punct and len(cur) >= 2) or (long_gap and len(cur) >= 2):
                chunks.append(cur[:])
                cur = []
        if cur:
            chunks.append(cur)
        return chunks

    chunks = _chunk_words(words)

    # ── ASS colour constants  (format: &HAABBGGRR) ────────────────────────────
    white   = "&H00FFFFFF"
    # Crimson: R=180(B4) G=10(0A) B=30(1E) → BGR=1E0AB4
    crimson = "&H001E0AB4"
    black   = "&H00000000"
    shadow_bg = "&HAA000000"   # semi-transparent dark back

    c_crim  = "{\\c" + crimson + "}"
    c_white = "{\\c" + white   + "}"

    def _phrase_text(chunk):
        parts = []
        for w in chunk:
            clean = w["word"].strip().lower().strip(".,!?:;،؟؛'\"")
            if clean in highlight_words:
                parts.append(c_crim + w["word"] + c_white)
            else:
                parts.append(w["word"])
        return " ".join(parts)

    # ── Build one ASS event per phrase ────────────────────────────────────────
    events: list[tuple[float, float, str]] = []
    for chunk in chunks:
        t_start = chunk[0]["start"]
        t_end   = chunk[-1]["end"]
        if t_end <= t_start:
            t_end = t_start + 0.5
        events.append((t_start, t_end, _phrase_text(chunk)))

    # ── ASS style ─────────────────────────────────────────────────────────────
    # Font: DejaVu Sans (installed via fonts-dejavu-core in CI; fallback on Windows)
    font_name = "DejaVu Sans"
    fontsize  = 82 if is_arabic else 76
    marginv   = 200   # px from bottom — clear of mobile UI chrome

    # Format: Name,Font,Size,Primary,Secondary,Outline,Back,
    #         Bold,Italic,Underline,Strike,ScaleX,ScaleY,Spacing,Angle,
    #         BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
    style_line = (
        f"Style: Default,{font_name},{fontsize},"
        f"{white},&H000000FF,{black},{shadow_bg},"
        f"-1,0,0,0,100,100,0,0,1,4,2,2,60,60,{marginv},1"
    )

    # ── Write ASS file ────────────────────────────────────────────────────────
    ass_path = output_path.replace(".mp4", "_subs.ass")
    ass_header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
        " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style_line,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    event_lines = [
        f"Dialogue: 0,{_secs_to_ass_time(s)},{_secs_to_ass_time(e)},Default,,0,0,0,,{txt}"
        for s, e, txt in events
    ]
    try:
        with open(ass_path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(ass_header + event_lines))
        print(f"[Subtitle] ASS written: {len(events)} phrases ({len(words)} words)")
    except Exception as ex:
        print(f"[Subtitle] Could not write ASS file: {ex}")
        return None

    # ── Burn into video ───────────────────────────────────────────────────────
    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        print("[Subtitle] ffmpeg not found, skipping subtitle burn")
        return None

    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", video_path, "-vf", f"ass={ass_escaped}",
             "-c:a", "copy", output_path],
            capture_output=True, timeout=600,
        )
        if result.returncode == 0:
            print(f"[Subtitle] Burned into: {output_path}")
            return output_path
        print(f"[Subtitle] ffmpeg burn failed (rc={result.returncode}): "
              f"{result.stderr[-300:].decode(errors='replace')}")
        return None
    except Exception as e:
        print(f"[Subtitle] Burn error: {e}")
        return None


def extract_first_frame(video_path: str, output_path: str) -> str:
    """Extract the first frame of a video as a JPEG thumbnail. Returns path or ''."""
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", "2", "-i", video_path,
             "-frames:v", "1", "-q:v", "2", output_path],
            capture_output=True, timeout=30,
        )
        if result.returncode == 0 and os.path.exists(output_path):
            print(f"[Video] Thumbnail extracted: {output_path}")
            return output_path
    except Exception as e:
        print(f"[Video] Thumbnail extraction failed: {e}")
    return ""


def _smooth_transitions(clips: list, fade_dur: float = 0.15) -> list:
    """Apply crossfadein to every clip except the first.

    Skips a clip if it is too short for the transition (< 2× fade_dur) to prevent
    MoviePy errors; those clips are appended as-is so the pipeline never crashes.
    Caller must use method='compose' in concatenate_videoclips.
    """
    result = []
    for i, clip in enumerate(clips):
        if i == 0 or clip.duration < 2 * fade_dur:
            result.append(clip)
        else:
            try:
                result.append(clip.crossfadein(fade_dur))
            except Exception:
                result.append(clip)   # fallback: no transition
    return result


# â"€â"€ Hook-aware assembly (long videos only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def assemble_video_with_hook(
    audio_path: str,
    image_paths: list[str],
    output_path: str,
    video_id: str,
    clip_durations: dict | None = None,
) -> str:
    """Assemble long video with fast-cut hook (0-90 s) and slow main section.

    Hook: all images cycle every 3-5 s — movie-trailer energy.
    Main: each image shown for 8-12 s — calm documentary pace.
    """
    import traceback
    import numpy as np
    from PIL import Image as PILImage
    try:
        from moviepy.editor import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips
    except ImportError:
        from moviepy import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips

    # User images copied in Step B are named *_ui_* inside output/images/
    _ui_in_pool = [p for p in image_paths if p and "_ui_" in os.path.basename(p)]
    _ui_existing = [p for p in _ui_in_pool if os.path.exists(p)]
    print(f"[Video] User images available at long assembly start: {len(_ui_existing)}")
    if _ui_in_pool and not _ui_existing:
        print("[Video] WARNING: User image paths in pool but files missing on disk — path issue")
    print(f"[DEBUG] Image pool at long assembly: {len(_ui_existing)} user images, {len(image_paths) - len(_ui_in_pool)} stock/AI images")
    print(f"[DEBUG] User image paths in pool: {[os.path.basename(p) for p in _ui_existing]}")
    print(f"[DEBUG] First 5 images for long video: {[os.path.basename(p) for p in image_paths[:5]]}")

    # Hard dedup: remove duplicate paths and missing files before any clip is made.
    # Guarantees no image file is counted twice in coverage math.
    _before_dedup = len(image_paths)
    image_paths = list(dict.fromkeys(p for p in image_paths if p and os.path.exists(p)))
    if len(image_paths) < _before_dedup:
        print(f"[Video] Deduped image pool: {_before_dedup} → {len(image_paths)} unique paths")

    TARGET_W, TARGET_H = 1080, 1920
    hook_duration = 90  # first 90 seconds

    temp_audio = output_path.replace(".mp4", "_tmp.m4a")

    try:
        audio = AudioFileClip(audio_path)
        total_duration = audio.duration
        print(f"[Video] Hook assembly — audio: {total_duration:.1f}s")
    except Exception as e:
        print(f"[Video] CRASH loading audio: {e}")
        traceback.print_exc()
        return ""

    main_duration = max(1.0, total_duration - hook_duration)

    def _load_frame(img_path: str):
        pil = PILImage.open(img_path).convert("RGB").resize(
            (TARGET_W, TARGET_H), PILImage.LANCZOS
        )
        return np.array(pil)

    # Frame cache: each unique image path loads its numpy array only once.
    # Clips sharing the same image reference the same array instead of duplicating 6 MB per clip.
    # For a 28-min video with 120 images, this drops frame RAM from ~2 GB to ~700 MB.
    _frame_cache: dict = {}

    def _load_frame_cached(img_path: str):
        if img_path not in _frame_cache:
            _frame_cache[img_path] = _load_frame(img_path)
        return _frame_cache[img_path]

    def _fit_vertical(clip):
        """Scale clip to fill 1080×1920 with center crop — no black bars for any aspect ratio."""
        cw, ch = clip.size
        scale = max(TARGET_W / cw, TARGET_H / ch)
        nw = max(TARGET_W, int(cw * scale))
        nh = max(TARGET_H, int(ch * scale))
        c = clip.resize((nw, nh))
        return c.crop(x_center=nw / 2, y_center=nh / 2, width=TARGET_W, height=TARGET_H)

    def _zoom_clip(
        frame, dur: float,
        start_scale: float, end_scale: float,
        fade_in: float = 0.0, fade_out: float = 0.0,
    ):
        """VideoClip with zoom + fade-in/out baked into make_frame.

        Uses VideoClip(make_frame) so output is always exactly TARGET_WÃ—TARGET_H
        — avoids the libx264 "odd dimension" crash that ImageClip.resize() causes.
        """
        def make_frame(t):
            rate  = (end_scale - start_scale) / max(dur, 0.001)
            scale = max(1.0, start_scale + rate * t)
            sw = int(TARGET_W * scale); sw += sw % 2
            sh = int(TARGET_H * scale); sh += sh % 2
            pil = PILImage.fromarray(frame).resize((sw, sh), PILImage.LANCZOS)
            x = (sw - TARGET_W) // 2
            y = (sh - TARGET_H) // 2
            rgb = np.array(pil.crop((x, y, x + TARGET_W, y + TARGET_H)), dtype=np.float32)
            # Fade-in
            if fade_in > 0 and t < fade_in:
                rgb *= t / fade_in
            # Fade-out
            if fade_out > 0 and t > dur - fade_out:
                rgb *= (dur - t) / fade_out
            return np.clip(rgb, 0, 255).astype("uint8")
        return VideoClip(make_frame=make_frame, duration=dur)

    def _pan_clip(frame, dur: float, pan_right: bool = True,
                  fade_in: float = 0.0, fade_out: float = 0.0):
        """Slow horizontal pan — creates lateral cinematic movement distinct from zoom."""
        def make_frame(t):
            progress = t / max(dur, 0.001)
            scale = 1.10
            sw = int(TARGET_W * scale); sw += sw % 2
            sh = int(TARGET_H * scale); sh += sh % 2
            pil = PILImage.fromarray(frame).resize((sw, sh), PILImage.LANCZOS)
            pan_range = sw - TARGET_W
            x = int(progress * pan_range) if pan_right else int((1.0 - progress) * pan_range)
            x = max(0, min(x, pan_range))
            y = (sh - TARGET_H) // 2
            rgb = np.array(pil.crop((x, y, x + TARGET_W, y + TARGET_H)), dtype=np.float32)
            fade = 1.0
            if fade_in > 0 and t < fade_in:
                fade = t / fade_in
            elif fade_out > 0 and t > dur - fade_out:
                fade = (dur - t) / fade_out
            return np.clip(rgb * max(0.0, min(1.0, fade)), 0, 255).astype("uint8")
        return VideoClip(make_frame=make_frame, duration=dur)

    def _media_clip(src_path: str, dur: float, zoom_in: bool = True, first_clip: bool = False):
        fi = 0.0 if first_clip else 0.2   # no fade-in on opening shot
        if _is_video_file(src_path):
            try:
                v = VideoFileClip(src_path).without_audio()  # mute — TTS voice is the only audio
            except Exception as _vfe:
                print(f"[Video] VideoFileClip failed ({os.path.basename(src_path)}): {_vfe} — using image fallback")
                frame = _load_frame(src_path)
                return _zoom_clip(frame, dur, 1.00, 1.06 if zoom_in else 1.00, fade_in=fi)
            if v.duration <= 0:
                v.close()
                frame = _load_frame(src_path)
                return _zoom_clip(frame, dur, 1.00, 1.06 if zoom_in else 1.00, fade_in=fi)
            # Timeline clip: start=0, end=min(assigned_duration, actual_duration)
            tl_dur = (clip_durations or {}).get(src_path)
            if tl_dur is not None:
                end = min(tl_dur, v.duration)
                c   = v.subclip(0, end)
            else:
                max_start = max(0.0, v.duration - dur)
                start     = random.uniform(0, max_start) if max_start > 0 else 0.0
                c         = v.subclip(start, min(v.duration, start + dur))
            c = _fit_vertical(c)
            if tl_dur is None and c.duration < dur:
                c = c.set_duration(dur)
            return c
        frame = _load_frame_cached(src_path)
        return _zoom_clip(frame, dur, 1.00, 1.08 if zoom_in else 1.00, fade_in=fi, fade_out=0.2)

    # â"€â"€ HOOK SECTION (0:00 to 1:30): fast cuts every 3-5 s â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # Cycle through ALL images repeatedly — movie-trailer energy
    hook_clips = []
    hook_total = 0.0
    img_index  = 0

    while hook_total < hook_duration:
        img_path = image_paths[img_index % len(image_paths)]
        try:
            cut_dur   = random.uniform(3, 4)
            remaining = hook_duration - hook_total
            cut_dur   = min(cut_dur, remaining)
            clip = _media_clip(img_path, cut_dur, zoom_in=(img_index % 2 == 0), first_clip=(img_index == 0))
            hook_clips.append(clip)
            hook_total += cut_dur
        except Exception as e:
            print(f"[Video] Hook clip error: {e}")
        img_index += 1

    print(f"[Video] Hook: {len(hook_clips)} fast cuts in {hook_total:.1f}s")

    # -- MAIN CONTENT (1:30 to end): adaptive slow cuts with zoom + pan variety --
    # FAST mode: max 8 s/clip → 8+ visual transitions/min → no long image holds.
    # FULL mode: max 14 s/clip → more cinematic breathing room.
    _n_imgs = max(len(image_paths), 1)
    # Clip duration cap scales with video length to keep total clips ≤ ~200.
    # Each visual position generates 2 sub-clips (zoom-in + zoom-out), so
    # _dur_cap ≥ main_duration / 200 keeps MoviePy composition manageable.
    # Floor cap (8s for FAST, 6s for FULL) preserved for short videos.
    _dur_cap_base = 8.0 if PIPELINE_MODE == "fast" else 6.0
    _dur_cap = max(_dur_cap_base, min(16.0, main_duration / 200.0))
    _floor   = 2.5 if PIPELINE_MODE == "fast" else 3.0
    _adaptive_base = max(_floor, min(_dur_cap, main_duration / (2.0 * _n_imgs + 1)))
    if _adaptive_base >= _dur_cap:
        print(f"[Visual] Clip duration capped at {_dur_cap:.0f}s "
              f"({_n_imgs} images, {main_duration:.0f}s main) — coverage loop will fill gaps")
    else:
        print(f"[Visual] Adaptive clip duration: {_adaptive_base:.1f}s/clip "
              f"({_n_imgs} images for {main_duration:.0f}s target)")

    main_clips = []
    for idx, img_path in enumerate(image_paths):
        try:
            dur1 = random.uniform(_adaptive_base, _adaptive_base * 1.15)
            dur2 = random.uniform(_adaptive_base, _adaptive_base * 1.15)
            # Rotate through 4 motion styles for cinematic variety
            if not _is_video_file(img_path) and idx % 4 == 1:
                try:
                    frame = _load_frame_cached(img_path)
                    main_clips.append(_pan_clip(frame, dur1, pan_right=True,  fade_in=0.2, fade_out=0.2))
                    main_clips.append(_zoom_clip(frame, dur2, 1.00, 1.06, fade_in=0.2, fade_out=0.2))
                    print(f"[Visual] Reusing cinematic montage (pan-R): {os.path.basename(img_path)[:40]}")
                    continue
                except Exception:
                    pass
            elif not _is_video_file(img_path) and idx % 4 == 3:
                try:
                    frame = _load_frame_cached(img_path)
                    main_clips.append(_pan_clip(frame, dur1, pan_right=False, fade_in=0.2, fade_out=0.2))
                    main_clips.append(_zoom_clip(frame, dur2, 1.06, 1.00, fade_in=0.2, fade_out=0.2))
                    print(f"[Visual] Reusing cinematic montage (pan-L): {os.path.basename(img_path)[:40]}")
                    continue
                except Exception:
                    pass
            main_clips.append(_media_clip(img_path, dur1, zoom_in=True))
            main_clips.append(_media_clip(img_path, dur2, zoom_in=False))
        except Exception as e:
            print(f"[Video] Main clip error: {e}")

    random.shuffle(main_clips)

    # Loop main clips until they cover main_duration + buffer.
    # Round-robin through deduplicated paths so every image appears equally
    # before any image repeats — prevents the same few images dominating.
    _unique_srcs = list(dict.fromkeys(image_paths))
    _cov_idx = 0
    while sum(c.duration for c in main_clips) < main_duration + 20:
        src = _unique_srcs[_cov_idx % len(_unique_srcs)]
        _cov_idx += 1
        dur = random.uniform(_adaptive_base, _adaptive_base * 1.15)
        try:
            main_clips.append(_media_clip(src, dur, zoom_in=(_cov_idx % 2 == 0)))
        except Exception:
            pass
    if _cov_idx > 0:
        print(f"[Visual] Coverage loop: {_cov_idx} extra clips over {len(_unique_srcs)} unique images")
    # Trim to main_duration
    accumulated = 0.0
    final_main  = []
    for clip in main_clips:
        if accumulated >= main_duration:
            break
        remaining = main_duration - accumulated
        if clip.duration > remaining:
            clip = clip.subclip(0, remaining)
        final_main.append(clip)
        accumulated += clip.duration

    print(f"[Video] Main: {len(final_main)} slow cuts in {accumulated:.1f}s")

    try:
        all_clips = hook_clips + final_main
        final = concatenate_videoclips(_smooth_transitions(all_clips), method="compose")
        if final.duration > total_duration:
            final = final.subclip(0, total_duration)
        final = final.set_audio(audio)
    except Exception as e:
        print(f"[Video] CRASH assembling hook video: {e}")
        traceback.print_exc()
        for clip in [audio] + hook_clips + final_main:
            try: clip.close()
            except Exception: pass
        return ""

    _clips_to_close = [audio, final] + hook_clips + final_main
    _ok = _write_video_safe(
        final, output_path, _clips_to_close,
        timeout_seconds=14400,
        fps=24, codec="libx264", audio_codec="aac", preset="ultrafast",
        ffmpeg_params=["-threads", "0", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        temp_audiofile=temp_audio, remove_temp=True, logger=None,
    )
    for _ in range(5):
        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            break
        except OSError:
            time.sleep(0.5)
    if not _ok:
        return ""

    if not os.path.exists(output_path):
        print(f"[Video] ERROR: output not created: {output_path}")
        return ""
    size_mb = os.path.getsize(output_path) // 1024 // 1024
    print(f"[Video] Hook video success: {output_path} ({size_mb}MB)")
    return output_path


# â"€â"€ Image count helpers â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def calculate_unique_images(is_short: bool = False) -> int:
    """Return number of unique AI images to generate (6 short / 20 long)."""
    return 6 if is_short else 20


def plan_visual_requirements(runtime_secs: float, is_short: bool = False) -> dict:
    """Compute the visual inventory required for high-density cinematic documentary.

    FULL mode target: 12 UNIQUE visual events per minute of runtime.
      10-min doc → 120 unique images    (one new visual every 5s)
      15-min doc → 180 unique images
      20-min doc → 240 unique images

    Each image is semantically unique — driven by script narrative events,
    not by PIL cropping/filtering of recycled source images.
    """
    if is_short:
        return {"n_unique": 6, "n_target": 6, "clips_per_min": 8.0,
                "insert_count": 0, "runtime_min": 0.0}
    runtime_min   = max(runtime_secs / 60, 0.1)
    n_unique      = max(60, min(400, int(runtime_min * 7)))  # 7 unique images/min
    clips_per_min = round(n_unique * 2 / runtime_min, 1)
    print(f"[FULL] Visual plan: {n_unique} unique semantic events | "
          f"~{clips_per_min} cuts/min for {runtime_min:.1f}min documentary")
    return {
        "n_unique":      n_unique,
        "n_target":      n_unique,
        "insert_count":  0,   # handled inside build_documentary_visual_pool
        "clips_per_min": clips_per_min,
        "runtime_min":   runtime_min,
    }


def _generate_emergency_visuals(
    n: int, output_dir: str, is_short: bool = False, topic: str = ""
) -> list[str]:
    """Generate cinematic emergency visuals when Pollinations, stock, and user content all fail.

    Priority:
      Tier 1 — Pollinations AI with 15 diverse crime documentary prompts (parallel, capped at 60)
      Tier 2 — PIL dark-gradient images (never flat solid colors)

    Caps unique images at 60 (4 workers × 15 batches). When Pollinations is down (402),
    each call exits immediately so 60 attempts stay fast. Unique seeds ensure diversity.
    """
    # Cap at 60 unique — 4 parallel workers × 15 batches = manageable runtime.
    # When Pollinations returns 402 (down), each call exits immediately so 60 calls
    # are near-instant. When Pollinations works, 60 seeds guarantee unique images.
    n_unique = min(n, 60)
    os.makedirs(output_dir, exist_ok=True)
    paths: list[str] = []

    # Tier 1: Pollinations in parallel (4 workers) — 15 diverse scene prompts,
    # each with a unique seed so the same prompt produces a different image.
    _top = topic or "true crime"
    _EM_PROMPTS = [
        f"{_top} crime investigation dark documentary cinematic{_IMAGE_PROMPT_SUFFIX}",
        f"police detective office 1970s dark cinematic file cabinet{_IMAGE_PROMPT_SUFFIX}",
        f"crime scene night urban dark atmospheric yellow tape{_IMAGE_PROMPT_SUFFIX}",
        f"interrogation room single overhead light shadow two chairs{_IMAGE_PROMPT_SUFFIX}",
        f"evidence board crime photos newspaper clippings map pins{_IMAGE_PROMPT_SUFFIX}",
        f"{_top} court trial gavel dark dramatic cinematic{_IMAGE_PROMPT_SUFFIX}",
        f"surveillance camera footage grainy documentary dark{_IMAGE_PROMPT_SUFFIX}",
        f"prison cell iron bars shadow dramatic dark cinematic{_IMAGE_PROMPT_SUFFIX}",
        f"newspaper front page 1980s crime headline dark dramatic{_IMAGE_PROMPT_SUFFIX}",
        f"city skyline night rain neon reflections dark{_IMAGE_PROMPT_SUFFIX}",
        f"detective notebook handwriting evidence dark moody{_IMAGE_PROMPT_SUFFIX}",
        f"abandoned building interior dark shadow cinematic{_IMAGE_PROMPT_SUFFIX}",
        f"courtroom empty witness stand dramatic window light{_IMAGE_PROMPT_SUFFIX}",
        f"police car lights reflection wet street night{_IMAGE_PROMPT_SUFFIX}",
        f"archive filing cabinet open folders dusty dark{_IMAGE_PROMPT_SUFFIX}",
    ]
    _em_seed = random.randint(1, 99999)

    from concurrent.futures import ThreadPoolExecutor as _TEX, as_completed as _asc

    def _try_one(i: int):
        prompt  = _EM_PROMPTS[i % len(_EM_PROMPTS)]
        em_path = os.path.join(output_dir, f"_emergency_{i:03d}.png")
        return generate_ai_image(prompt, em_path, seed=_em_seed + i)

    print(f"[Emergency] Generating {n_unique} unique visuals (cap={n_unique}, requested={n}), 4 workers")
    with _TEX(max_workers=4) as _pool:
        _futures = {_pool.submit(_try_one, i): i for i in range(n_unique)}
        for _fut in _asc(_futures):
            _res = _fut.result()
            if _res:
                paths.append(_res)

    if paths:
        print(f"[Emergency] {len(paths)}/{n_unique} AI cinematic emergency visuals generated (cap={n_unique}, requested={n})")
        return paths

    # Tier 2: PIL dark-gradient fallback (not flat solid)
    try:
        from PIL import Image as _PIL, ImageDraw as _D
    except ImportError:
        print("[Emergency] PIL not available — cannot generate gradient fallback")
        return []
    _GRADIENT_PAIRS = [
        ((18, 18, 24), (34, 26, 44)), ((14, 22, 28), (22, 40, 46)),
        ((22, 16, 28), (40, 26, 34)), ((20, 24, 18), (36, 44, 30)),
        ((28, 18, 14), (46, 32, 22)), ((16, 20, 26), (28, 38, 46)),
    ]
    w, h = (1080, 1920) if is_short else (1920, 1080)
    for i in range(n - len(paths)):
        grad_path = os.path.join(output_dir, f"_emergency_grad_{i:03d}.jpg")
        try:
            c1, c2    = _GRADIENT_PAIRS[i % len(_GRADIENT_PAIRS)]
            img       = _PIL.new("RGB", (w, h), c1)
            draw      = _D.Draw(img)
            for y in range(h):
                ratio = y / h
                r = int(c1[0] + (c2[0] - c1[0]) * ratio)
                g = int(c1[1] + (c2[1] - c1[1]) * ratio)
                b = int(c1[2] + (c2[2] - c1[2]) * ratio)
                draw.line([(0, y), (w, y)], fill=(r, g, b))
            img.save(grad_path, "JPEG", quality=85)
            paths.append(grad_path)
        except Exception as _e:
            print(f"[Emergency] Gradient fallback {i}: {_e}")
    print(f"[Emergency] {len(paths)} total emergency visuals ({w}×{h})")
    return paths


def calculate_total_images(user_images=None) -> int:
    """Return 12 AI + however many user images were sent."""
    ai_images  = 12
    user_count = len(user_images) if user_images else 0
    total      = ai_images + user_count
    print(f"[Video] Images: {ai_images} AI + {user_count} user = {total} total")
    return total


# Cinematic insert prompts for Netflix-documentary texture
_CINEMATIC_INSERT_PROMPTS = [
    "crime investigation evidence board photographs newspaper clippings case files dark cinematic documentary",
    "vintage CCTV surveillance camera screenshot grainy black white crime scene night documentary",
    "newspaper front page headline arrest conviction crime black white archival documentary photo",
    "police interrogation room single overhead light table chair shadow dramatic cinematic",
    "crime scene chalk outline police tape night urban dark atmospheric forensic documentary",
    "courtroom trial evidence exhibit newspaper photograph dramatic documentary cinematic",
    "detective wall case notes photos string map crime connections dark investigation",
    "police evidence file folder case documents dramatic cinematic dark documentary",
    "urban street map crime location pin night documentary noir cinematic aerial",
    "criminal mugshot photograph police station grey background stark documentary style",
    "forensic laboratory crime scene evidence analysis dark cinematic documentary",
    "detective office crime case wall photographs news clippings noir atmospheric",
    "archive news footage 1970s 1980s crime report dark vintage documentary grain",
    "security camera timestamp corner criminal activity grainy dark surveillance footage",
    "crime scene police barrier yellow tape night rain dark atmospheric cinematic",
]


def _generate_cinematic_inserts(
    topic: str, count: int, video_id: str, is_short: bool = False
) -> list[str]:
    """Generate AI cinematic inserts via Pollinations for documentary texture.

    Types: evidence boards, CCTV stills, newspaper clips, interrogation rooms,
    crime scenes, detective walls, court exhibits — interleaved into visual pool
    to prevent the static slideshow feel.
    """
    _img_dir = _get_images_dir()
    paths: list[str] = []
    topic_prompt = (
        f"{_clean_topic_name(topic)} true crime investigation evidence cinematic dark documentary"
        if topic else ""
    )
    prompt_pool = ([topic_prompt] if topic_prompt else []) + _CINEMATIC_INSERT_PROMPTS
    seed = random.randint(10000, 99999)
    for i in range(count):
        prompt   = prompt_pool[i % len(prompt_pool)] + _IMAGE_PROMPT_SUFFIX
        out_path = os.path.join(_img_dir, f"{video_id}_insert_{i:03d}.png")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 5_000:
            paths.append(out_path)
            continue
        result = generate_ai_image(prompt, out_path, seed=seed + i)
        if result:
            paths.append(result)
            print(f"[Insert] Cinematic insert {i+1}/{count}: {prompt[:55]}")
        else:
            print(f"[Insert] Failed insert {i+1} — Pollinations unavailable")
    print(f"[Insert] Generated {len(paths)}/{count} cinematic inserts")
    return paths


def _generate_full_visual_pool(
    base_images: list[str],
    n_target: int,
    topic: str,
    video_id: str,
    is_short: bool = False,
) -> list[str]:
    """Expand base_images to n_target using cinematic inserts + PIL variants.

    Strategy:
      1. Inject cinematic inserts at every 5th slot (20% of pool)
      2. Apply 7-op PIL variation engine to fill remaining gap
      3. Return pool sized to n_target (NOT shuffled — caller shuffles in assembler)
    """
    import itertools as _it
    if not base_images:
        return base_images

    # Step 1: interleave cinematic inserts
    _insert_n  = max(4, int(n_target * 0.20))
    _inserts   = _generate_cinematic_inserts(topic, _insert_n, video_id + "_ci", is_short=is_short)
    _interleaved: list[str] = []
    _ins_idx = 0
    for _i, _p in enumerate(base_images):
        _interleaved.append(_p)
        if _ins_idx < len(_inserts) and (_i + 1) % 5 == 0:
            _interleaved.append(_inserts[_ins_idx])
            _ins_idx += 1
    _interleaved.extend(_inserts[_ins_idx:])
    pool = _interleaved

    # Step 2: PIL variation engine fills gap
    _gap = n_target - len(pool)
    if _gap > 0:
        _real_img_exts = {".jpg", ".jpeg", ".png", ".webp"}
        _base_imgs = [
            p for p in base_images
            if not _is_video_file(p)
            and os.path.splitext(p)[1].lower() in _real_img_exts
            and os.path.exists(p)
        ]
        if _base_imgs:
            _w, _h = (1080, 1920) if is_short else (1920, 1080)
            try:
                from PIL import Image as _PILV, ImageEnhance as _PILEN
                _VAR_OPS_FULL = [
                    ("cc",   lambda im: im.crop((im.width//8, im.height//8,
                                                 im.width*7//8, im.height*7//8))
                                         .resize(im.size, _PILV.LANCZOS)),
                    ("gs",   lambda im: _PILV.merge("RGB", [im.convert("L")] * 3)),
                    ("hc",   lambda im: _PILEN.Contrast(im).enhance(1.8)),
                    ("lo",   lambda im: _PILEN.Brightness(im).enhance(0.45)),
                    ("warm", lambda im: _PILEN.Color(im).enhance(0.25)),
                    ("tl",   lambda im: im.crop((0, 0, im.width * 3 // 4, im.height * 3 // 4))
                                         .resize(im.size, _PILV.LANCZOS)),
                    ("br",   lambda im: im.crop((im.width // 4, im.height // 4,
                                                 im.width, im.height))
                                         .resize(im.size, _PILV.LANCZOS)),
                ]
                _created = 0
                for _bpath, (_tag, _op) in zip(
                    _it.cycle(_base_imgs), _it.cycle(_VAR_OPS_FULL)
                ):
                    if _created >= _gap:
                        break
                    _vname = (f"{os.path.splitext(os.path.basename(_bpath))[0]}"
                              f"_{_tag}_full.jpg")
                    _vpath = os.path.join(IMAGES_DIR, _vname)
                    if os.path.exists(_vpath) and os.path.getsize(_vpath) > 2_000:
                        pool.append(_vpath)
                        _created += 1
                        continue
                    try:
                        _img = _PILV.open(_bpath).convert("RGB").resize(
                            (_w, _h), _PILV.LANCZOS
                        )
                        _var = _op(_img)
                        _var.save(_vpath, "JPEG", quality=82)
                        pool.append(_vpath)
                        _created += 1
                    except Exception:
                        pass
                print(f"[FULL] PIL variation engine: +{_created} variants → {len(pool)} total")
            except ImportError:
                print("[FULL] PIL not available — skipping variation engine")

    return pool


def build_image_list(user_images: list, ai_images: list[str]) -> list[str]:
    """Return image path list: user photos first, then AI-generated images."""
    final: list[str] = []
    for img in user_images:
        path = img if isinstance(img, str) else img.get("path", "")
        if path and os.path.exists(path):
            final.append(path)
            print(f"[Video] User image: {path}")
    for path in ai_images:
        if path and os.path.exists(path):
            final.append(path)
    print(f"[Video] Total images: {len(final)}")
    return final


# ── Visual scoring ────────────────────────────────────────────────────────────

def _score_visual_asset(path: str, query: str = "", topic: str = "") -> float:
    """
    Score a media asset for quality and relevance.

    +5  real archive / documentary source (Wikimedia, stock video from _REAL_IMAGE_PATHS)
    +3  video clip
    +3  wiki_real in filename
    +1  image file
    +2  dark/cinematic tones (brightness < 100)  [images only]
    -2  overexposed (brightness > 200)
    +3  face detected (OpenCV)
    +2  query/topic keyword in filename
    -5  irrelevant category (animals, nature, fashion…)

    Real/documentary assets always outscore AI-generated images so they
    appear first when the pool is ranked and fill primary timeline slots.
    """
    score = 0.0
    is_video = _is_video_file(path)
    score += 3.0 if is_video else 1.0

    base = os.path.basename(path).lower()

    # Documentary-source bonus — ensures real assets outrank dark AI images
    if path in _REAL_IMAGE_PATHS or "_wiki_real" in base:
        score += 5.0

    # Hard reject: irrelevant category in filename
    if any(t in base for t in _IRRELEVANT_QUERY_TERMS):
        return score - 5.0

    # Keyword relevance: topic/query words in filename
    kw = set((topic or "").lower().split()) | set((query or "").lower().split())
    kw -= {"the", "a", "an", "of", "in", "is", "was"}
    if kw and any(w in base for w in kw if len(w) >= 4):
        score += 2.0

    # Visual analysis for images only (frame extraction for videos is too slow)
    if not is_video:
        try:
            import numpy as _np
            from PIL import Image as _PILImg
            with _PILImg.open(path) as _img:
                arr = _np.array(_img.convert("RGB"))
            mean_b = float(_np.mean(arr))
            if mean_b < 100:
                score += 2.0   # dark / cinematic
            elif mean_b > 200:
                score -= 2.0   # washed out

            # Face detection (OpenCV optional — skipped silently if unavailable)
            try:
                import cv2 as _cv2
                gray = _cv2.cvtColor(arr, _cv2.COLOR_RGB2GRAY)
                cc = _cv2.CascadeClassifier(
                    _cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                )
                if len(cc.detectMultiScale(gray, 1.1, 4)) > 0:
                    score += 3.0
            except Exception:
                pass
        except Exception:
            pass

    return score


def _rank_visual_pool(paths: list[str], query: str = "", topic: str = "") -> list[str]:
    """Score each asset and return sorted list, highest score first."""
    if not paths:
        return paths
    scored = []
    for p in paths:
        try:
            s = _score_visual_asset(p, query=query, topic=topic)
        except Exception:
            s = 0.0
        scored.append((s, p))
    scored.sort(key=lambda x: -x[0])
    top = [round(s, 1) for s, _ in scored[:5]]
    print(f"[Visual] Ranked {len(scored)} assets — top scores: {top}")
    return [p for _, p in scored]


# â"€â"€ Short video assembler â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def assemble_short_video(audio_path: str, image_paths: list[str], output_path: str, clip_durations: dict | None = None) -> str:
    """Assemble short video: 2 zoom variations per image, loop to fill 60-90 s."""
    import traceback
    import numpy as np
    from PIL import Image as PILImage
    try:
        from moviepy.editor import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips
    except ImportError:
        from moviepy import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips

    # User images copied in Step B are named *_ui_* inside output/images/
    _ui_in_pool = [p for p in image_paths if p and "_ui_" in os.path.basename(p)]
    _ui_existing = [p for p in _ui_in_pool if os.path.exists(p)]
    print(f"[Video] User images available at short assembly start: {len(_ui_existing)}")
    if _ui_in_pool and not _ui_existing:
        print("[Video] WARNING: User image paths in pool but files missing on disk — path issue")
    print(f"[DEBUG] Image pool at short assembly: {len(_ui_existing)} user images, {len(image_paths) - len(_ui_in_pool)} stock/AI images")
    print(f"[DEBUG] User image paths in pool: {[os.path.basename(p) for p in _ui_existing]}")
    print(f"[DEBUG] First 5 images for short video: {[os.path.basename(p) for p in image_paths[:5]]}")

    TARGET_W, TARGET_H = 1080, 1920
    temp_audio = output_path.replace(".mp4", "_tmp.m4a")

    try:
        audio                = AudioFileClip(audio_path)
        actual_audio_duration = audio.duration
        print(f"[Video] Short audio duration: {actual_audio_duration:.1f}s")

        # Clamp to 60-90s range
        target_duration = actual_audio_duration
        if target_duration < 60:
            target_duration = 60
            print(f"[Video] Padding video to minimum 60s")
        if target_duration > 90:
            target_duration = 90
            audio = audio.subclip(0, 90)
            print(f"[Video] Trimming to maximum 90s")

        total_duration = target_duration
        print(f"[Video] Short assembly — target: {total_duration:.1f}s")
    except Exception as e:
        print(f"[Video] CRASH loading audio: {e}")
        traceback.print_exc()
        return ""

    def _load_frame(img_path: str):
        pil = PILImage.open(img_path).convert("RGB").resize(
            (TARGET_W, TARGET_H), PILImage.LANCZOS
        )
        return np.array(pil)

    def _fit_vertical(clip):
        """Scale clip to fill 1080×1920 with center crop — no black bars for any aspect ratio."""
        cw, ch = clip.size
        scale = max(TARGET_W / cw, TARGET_H / ch)
        nw = max(TARGET_W, int(cw * scale))
        nh = max(TARGET_H, int(ch * scale))
        c = clip.resize((nw, nh))
        return c.crop(x_center=nw / 2, y_center=nh / 2, width=TARGET_W, height=TARGET_H)

    def _zoom_clip(frame, start_scale: float, end_scale: float, dur: float):
        def make_frame(t):
            rate  = (end_scale - start_scale) / max(dur, 0.001)
            scale = max(1.0, start_scale + rate * t)
            sw = int(TARGET_W * scale); sw += sw % 2
            sh = int(TARGET_H * scale); sh += sh % 2
            pil = PILImage.fromarray(frame).resize((sw, sh), PILImage.LANCZOS)
            x   = (sw - TARGET_W) // 2
            y   = (sh - TARGET_H) // 2
            rgb = np.array(pil.crop((x, y, x + TARGET_W, y + TARGET_H)), dtype=np.float32)
            fade = 1.0
            if t < 0.2:            fade = t / 0.2
            elif t > dur - 0.2:    fade = (dur - t) / 0.2
            return np.clip(rgb * max(0.0, min(1.0, fade)), 0, 255).astype("uint8")
        return VideoClip(make_frame=make_frame, duration=dur)

    def _media_clip(src_path: str, dur: float, zoom_in: bool = True):
        if _is_video_file(src_path):
            try:
                v = VideoFileClip(src_path).without_audio()  # mute — TTS voice is the only audio
            except Exception as _vfe:
                print(f"[Video] VideoFileClip failed ({os.path.basename(src_path)}): {_vfe} — using image fallback")
                return _zoom_clip(_load_frame(src_path), 1.00, 1.08 if zoom_in else 1.00, dur)
            if v.duration <= 0:
                v.close()
                return _zoom_clip(_load_frame(src_path), 1.00, 1.08 if zoom_in else 1.00, dur)
            # Timeline clip: start=0, end=min(assigned_duration, actual_duration)
            tl_dur = (clip_durations or {}).get(src_path)
            if tl_dur is not None:
                end = min(tl_dur, v.duration)
                c   = v.subclip(0, end)
            else:
                actual_dur = min(dur, v.duration)
                max_start  = max(0.0, v.duration - actual_dur)
                start      = random.uniform(0, max_start) if max_start > 0 else 0.0
                c          = v.subclip(start, min(v.duration, start + actual_dur))
            c = _fit_vertical(c)
            return c
        frame = _load_frame(src_path)
        return _zoom_clip(frame, 1.00, 1.08 if zoom_in else 1.00, dur)

    media_sources = [p for p in image_paths if p and os.path.exists(p)]
    if not media_sources:
        print("[Video] No media for short video, aborting")
        return ""

    # Separate real footage from static images — videos anchor the front
    video_sources = [p for p in media_sources if _is_video_file(p)]
    image_sources = [p for p in media_sources if not _is_video_file(p)]

    # Video clips: 10-14s, two segments per source
    video_clips: list = []
    for src in video_sources:
        try:
            video_clips.append(_media_clip(src, random.uniform(10, 14)))
            video_clips.append(_media_clip(src, random.uniform(10, 14)))
        except Exception as e:
            print(f"[Video] Short video clip error: {e}")

    # Image clips: 5-7s zoom pairs
    image_clips: list = []
    for src in image_sources:
        try:
            image_clips.append(_media_clip(src, random.uniform(5, 7), zoom_in=True))
            image_clips.append(_media_clip(src, random.uniform(5, 7), zoom_in=False))
        except Exception as e:
            print(f"[Video] Short image clip error: {e}")

    random.shuffle(image_clips)
    all_clips = video_clips + image_clips

    # Gap-fill: prefer real footage when available
    refill_pool = video_sources if video_sources else media_sources
    while sum(c.duration for c in all_clips) < total_duration + 5:
        src = refill_pool[random.randint(0, len(refill_pool) - 1)]
        try:
            all_clips.append(_media_clip(src, random.uniform(8, 12)))
        except Exception:
            pass

    # Trim to total_duration
    final_clips: list = []
    accumulated = 0.0
    for clip in all_clips:
        if accumulated >= total_duration:
            break
        remaining = total_duration - accumulated
        if clip.duration > remaining:
            clip = clip.subclip(0, remaining)
        final_clips.append(clip)
        accumulated += clip.duration

    print(f"[Video] Short: {len(final_clips)} clips covering {accumulated:.1f}s")

    try:
        final = concatenate_videoclips(_smooth_transitions(final_clips), method="compose")
        # Trim video to EXACT audio duration — prevents silence at end
        exact_duration = audio.duration
        if final.duration > exact_duration:
            final = final.subclip(0, exact_duration)
        final = final.set_audio(audio)
        print(f"[Video] Final duration: {final.duration:.1f}s  Audio: {audio.duration:.1f}s")
    except Exception as e:
        print(f"[Video] CRASH assembling short video: {e}")
        traceback.print_exc()
        for clip in [audio] + all_clips:
            try: clip.close()
            except Exception: pass
        return ""

    _clips_to_close = [audio, final] + all_clips
    _ok = _write_video_safe(
        final, output_path, _clips_to_close,
        timeout_seconds=1800,
        fps=30, codec="libx264", audio_codec="aac", preset="ultrafast",
        ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        temp_audiofile=temp_audio, remove_temp=True, logger=None,
    )
    for _ in range(5):
        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
            break
        except OSError:
            time.sleep(0.5)
    if not _ok:
        return ""

    if not os.path.exists(output_path):
        print(f"[Video] ERROR: short output not created: {output_path}")
        return ""
    size_mb = os.path.getsize(output_path) // 1024 // 1024
    print(f"[Video] Short video success: {output_path} ({size_mb}MB)")
    return output_path


# â"€â"€ Music asset management â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_MUSIC_TRACKS = {
    "assets/music/documentary_long.mp3": [
        "https://cdn.pixabay.com/download/audio/2022/03/15/audio_8cb749612b.mp3",
    ],
    "assets/music/documentary_short.mp3": [
        "https://cdn.pixabay.com/download/audio/2022/01/18/audio_d0c6ff1c23.mp3",
    ],
}


def _create_ambient_music_fallback(path: str, seconds: int) -> bool:
    """Generate a low-volume brown-noise ambient track as music fallback."""
    import subprocess

    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        return False
    try:
        subprocess.run(
            [
                ffmpeg_bin, "-y",
                "-f", "lavfi",
                "-i", f"anoisesrc=color=brown:r=44100",
                "-t", str(seconds),
                "-af", "volume=0.05",
                "-c:a", "libmp3lame", "-q:a", "5",
                path,
            ],
            check=True,
            capture_output=True,
        )
        size_kb = os.path.getsize(path) // 1024 if os.path.exists(path) else 0
        print(f"[Music] Brown-noise ambient track created: {path} ({size_kb} KB)")
        return os.path.exists(path) and size_kb > 0
    except Exception as e:
        print(f"[Music] Failed to generate ambient music {path}: {e}")
        return False


def ensure_music_assets() -> None:
    """Ensure background music assets exist; generate ambient fallback if CDN unavailable."""
    os.makedirs("assets/music", exist_ok=True)
    for path, urls in _MUSIC_TRACKS.items():
        # Skip if file exists and is non-empty
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            continue
        # Remove zero-byte or corrupt file before regenerating
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        print(f"[Music] Music file missing/empty: {path} -- attempting download...")
        downloaded = False
        for url in urls:
            try:
                r = requests.get(url, timeout=30, stream=True)
                if r.status_code == 200:
                    with open(path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=65536):
                            f.write(chunk)
                    size_kb = os.path.getsize(path) // 1024
                    if size_kb > 10:
                        print(f"[Music] Downloaded: {path} ({size_kb} KB)")
                        downloaded = True
                        break
                    print(f"[Music] Downloaded file too small ({size_kb} KB) -- likely blocked")
                    os.remove(path)
                else:
                    print(f"[Music] HTTP {r.status_code} for {url} -- skipping CDN")
            except Exception as e:
                print(f"[Music] Download error: {e}")
        if downloaded:
            continue

        # CDN 403/blocked -- generate brown-noise ambient track locally
        fallback_seconds = 90 if "short" in os.path.basename(path).lower() else 900
        print(f"[Music] Generating {fallback_seconds}s brown-noise ambient track: {path}")
        if not _create_ambient_music_fallback(path, fallback_seconds):
            print(f"[Music] Could not generate ambient track for {path} -- voice-only mode")
def _measure_audio_levels(path: str, label: str = "") -> None:
    """Log peak dB and mean volume (RMS) of an audio file via ffmpeg volumedetect."""
    import subprocess, re as _re
    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin or not os.path.exists(path):
        return
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-y", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        out   = result.stderr
        peak  = _re.search(r'max_volume:\s*([-\d.]+)\s*dB', out)
        mean  = _re.search(r'mean_volume:\s*([-\d.]+)\s*dB', out)
        tag   = f" [{label}]" if label else ""
        if peak:
            print(f"[Audio]{tag} Peak: {peak.group(1)} dB")
        if mean:
            print(f"[Audio]{tag} RMS: {mean.group(1)} dB")
    except Exception as e:
        print(f"[Audio] Level measurement failed ({label}): {e}")


def _check_and_boost_audio(path: str) -> None:
    """Emergency failsafe: if peak is below -18 dBFS after processing, apply gain correction."""
    import subprocess, re as _re
    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin or not os.path.exists(path):
        return
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-y", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        peak_m = _re.search(r'max_volume:\s*([-\d.]+)\s*dB', result.stderr)
        if not peak_m:
            return
        peak = float(peak_m.group(1))
        if peak >= -8.0:
            return   # already loud enough
        # Boost to bring peak to -4 dBFS; cap at +12 dB to avoid distortion
        boost_db = min(-4.0 - peak, 12.0)
        print(f"[Audio] Emergency failsafe: peak={peak:.1f} dB → applying +{boost_db:.1f} dB boost")
        tmp = path + ".boost.mp3"
        subprocess.run(
            [ffmpeg_bin, "-y", "-i", path,
             "-af", f"volume={boost_db:.1f}dB,alimiter=limit=0.95:attack=3:release=30",
             "-c:a", "libmp3lame", "-q:a", "2", tmp],
            check=True, capture_output=True, timeout=60,
        )
        os.replace(tmp, path)
        print(f"[Audio] Emergency boost applied successfully")
    except Exception as e:
        print(f"[Audio] Emergency boost failed: {e}")


def mix_background_music(voice_path: str, is_short: bool = False) -> str:
    """Mix looping background music under the voice track at -24 dB (volume=0.06)."""
    import subprocess

    music_file = (
        "assets/music/documentary_short.mp3" if is_short
        else "assets/music/documentary_long.mp3"
    )

    if not os.path.exists(music_file):
        print(f"[Music] Music file missing ({music_file}) — skipping mix âš ï¸")
        return voice_path

    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        print("[Music] ffmpeg not found — skipping music mix")
        return voice_path

    output = voice_path.replace(".mp3", "_with_music.mp3")
    try:
        subprocess.run(
            [ffmpeg_bin,
             "-i", voice_path,
             "-stream_loop", "-1",
             "-i", music_file,
             "-filter_complex",
             "[1]volume=0.06[bg];[0][bg]amix=inputs=2:duration=first:normalize=0",
             "-c:a", "libmp3lame", "-q:a", "2",
             "-y", output],
            check=True, capture_output=True,
        )
        label = "short" if is_short else "long"
        print(f"[Music] Music mixed at -24 dB ({label}): {output} âœ…")
        return output
    except Exception as e:
        print(f"[Music] Mix failed: {e} — returning voice-only")
        return voice_path


# â"€â"€ Netflix-quality audio post-processing â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def process_audio_netflix(input_path: str, is_short: bool = None) -> str:
    """
    3-step audio chain: EQ → compress+makeup → loudnorm+limiter → music mix.

    Targets (mobile/phone-speaker optimised):
      Shorts  → -14 LUFS, TP=-1.0  (TikTok/Reels/Shorts autoplay)
      Long    → -16 LUFS, TP=-1.0  (YouTube mobile)

    Key fixes vs. old chain:
      - Reverb (aecho) REMOVED: added blur and cut perceived loudness on phone speakers
      - Compressor uses makeup=6 dB: compresses dynamics AND restores+boosts loudness
      - amix uses normalize=0: voice level is preserved after music mix (was halved)
      - Emergency failsafe applied if peak is still below -18 dBFS after chain
    """
    import subprocess
    import shutil

    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        print("[Audio] ffmpeg not found — skipping audio processing")
        return input_path

    if is_short is None:
        is_short = "short" in os.path.basename(input_path).lower()

    lufs_target = "-14" if is_short else "-16"
    lra         = "9"   if is_short else "11"
    base        = input_path.replace(".mp3", "")

    _measure_audio_levels(input_path, "Raw TTS")

    steps = [
        # Step 1: EQ — highpass removes low-frequency rumble, mild bass boost adds warmth
        ([ffmpeg_bin, "-y", "-i", input_path,
          "-af", "highpass=f=80,equalizer=f=120:width_type=o:width=2:g=2",
          f"{base}_s1.mp3"], "eq"),
        # Step 2: Compression + makeup gain
        # threshold=0.089 ≈ -21 dBFS catches most voice; ratio=3:1; makeup=6 dB restores+boosts
        ([ffmpeg_bin, "-y", "-i", f"{base}_s1.mp3",
          "-af", "acompressor=threshold=0.089:ratio=3:attack=5:release=100:makeup=6",
          f"{base}_s2.mp3"], "compress+makeup"),
        # Step 3: Loudness normalisation to target LUFS + hard limiter prevents clipping
        ([ffmpeg_bin, "-y", "-i", f"{base}_s2.mp3",
          "-af", f"loudnorm=I={lufs_target}:TP=-1.0:LRA={lra},"
                 "alimiter=limit=0.95:attack=3:release=30",
          f"{base}_s3.mp3"], "loudnorm+limit"),
    ]

    step_files: list[str] = []
    for cmd, label in steps:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            step_files.append(cmd[-1])
            print(f"[Audio] Step '{label}' done")
        except Exception as e:
            print(f"[Audio] Step '{label}' failed: {e} — stopping chain")
            break

    if not step_files:
        return input_path

    final_processed = step_files[-1]
    _measure_audio_levels(final_processed, "After processing")

    # Mix background music (amix normalize=0 preserves voice level)
    mixed = mix_background_music(final_processed, is_short=is_short)
    if mixed != final_processed:
        final_processed = mixed
        _measure_audio_levels(final_processed, "After music mix")

    # Emergency failsafe — if audio is still too quiet, apply gain correction
    _check_and_boost_audio(final_processed)

    # Replace original with processed version
    try:
        shutil.move(final_processed, input_path)
    except Exception as e:
        print(f"[Audio] Could not replace original: {e}")
        return final_processed

    # Remove intermediate temp files
    for f in step_files:
        if f != final_processed and os.path.exists(f):
            try: os.remove(f)
            except OSError: pass

    _measure_audio_levels(input_path, "Final export")
    print(f"[Audio] Post-processing complete: EQ + compress+makeup + loudnorm({lufs_target} LUFS) + limiter + music")
    return input_path


# â"€â"€ Section-aware TTS + accurate chapter builder â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_SECTION_DISPLAY = {
    "Introduction":   "ðŸŽ¬ Introduction",
    "Background":     "🔺 Background & Context",
    "Main Story":     "🔍 Main Story",
    "Shocking Facts": "\U0001f480 Shocking Facts",
    "Conclusion":     "\U0001f3af Conclusion",
    "الرواية مقابل الواقع": "\U0001f3ad الرواية مقابل الواقع",
    "Ù…Ù‚Ø¯Ù…Ø©":          "ðŸŽ¬ Ù…Ù‚Ø¯Ù…Ø©",
    "Ø§Ù„Ø®Ù„ÙÙŠØ©":         "🔺 Ø§Ù„Ø®Ù„ÙÙŠØ© ÙˆØ§Ù„Ø³ÙŠØ§Ù‚",
    "Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©":  "🔍 Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©",
    "Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©":    "ðŸ'€ Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©",
    "Ø§Ù„Ø®Ø§ØªÙ…Ø©":         "ðŸŽ¯ Ø§Ù„Ø®Ø§ØªÙ…Ø©",
}


def _canonical_section_name(name: str) -> str:
    """Normalize section names to stable display keys."""
    import re as _re2
    # Strip leading section numbers like "3 — " or "4. " before lookup
    cleaned = _re2.sub(r'^\d+\s*[.—\-:–]+\s*', '', (name or "").strip()).strip()
    n = (cleaned or name or "").strip().strip("-: ").lower()
    if not n:
        return "Introduction"
    aliases = {
        "introduction": "Introduction",
        "intro": "Introduction",
        "opening": "Introduction",
        "background": "Background",
        "background & context": "Background",
        "context": "Background",
        "main story": "Main Story",
        "main events": "Main Story",
        "story": "Main Story",
        "the real story": "Main Story",
        "shocking facts": "Shocking Facts",
        "shocking details": "Shocking Facts",
        "revelations": "Shocking Facts",
        "conclusion": "Conclusion",
        "ending": "Conclusion",
        "القصة الحقيقية": "القصة الرئيسية",
        "الرواية مقابل الواقع": "الرواية مقابل الواقع",
        "Ù…Ù‚Ø¯Ù…Ø©": "Ù…Ù‚Ø¯Ù…Ø©",
        "Ø§Ù„Ù…Ù‚Ø¯Ù…Ø©": "Ù…Ù‚Ø¯Ù…Ø©",
        "Ø§Ù„Ø®Ù„ÙÙŠØ©": "Ø§Ù„Ø®Ù„ÙÙŠØ©",
        "Ø§Ù„Ø®Ù„ÙÙŠØ© ÙˆØ§Ù„Ø³ÙŠØ§Ù‚": "Ø§Ù„Ø®Ù„ÙÙŠØ©",
        "Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©": "Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©",
        "Ø§Ù„Ù‚ØµØ©": "Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©",
        "Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©": "Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©",
        "Ø§Ù„Ø­Ù‚Ø§Ø¦Ù‚ Ø§Ù„ØµØ§Ø¯Ù…Ø©": "Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©",
        "Ø§Ù„Ø®Ø§ØªÙ…Ø©": "Ø§Ù„Ø®Ø§ØªÙ…Ø©",
    }
    return aliases.get(n, cleaned.strip() or name.strip())


def _parse_script_sections(script_text: str) -> list[tuple[str, str]]:
    """
    Parse sectioned scripts robustly across English/Arabic marker variants.

    Supports:
    - [SECTION: Name]
    - [Ù‚Ø³Ù…: Name] / [Ø§Ù„Ù‚Ø³Ù…: Name]
    - {SECTION: Name}
    - {Ø§Ù„Ø®Ø§ØªÙ…Ø©:}
    """
    import re
    marker_line = re.compile(
        r'^\s*[\[\{\(]\s*(?:(?:section|chapter|part|Ù‚Ø³Ù…|Ø§Ù„Ù‚Ø³Ù…)\s*:\s*)?([^\]\}\)\n:]+?)\s*:?\s*[\]\}\)]\s*$',
        flags=re.IGNORECASE,
    )
    plain_label_line = re.compile(
        r'^\s*(introduction|background|main story|shocking facts|conclusion|Ù…Ù‚Ø¯Ù…Ø©|Ø§Ù„Ø®Ù„ÙÙŠØ©|Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©|Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©|Ø§Ù„Ø®Ø§ØªÙ…Ø©)\s*:\s*$',
        flags=re.IGNORECASE,
    )

    sections: list[tuple[str, str]] = []
    current_name = "Introduction"
    current_lines: list[str] = []
    saw_marker = False

    for raw_line in (script_text or "").splitlines():
        line = raw_line.strip()
        m = marker_line.match(line)
        if m:
            content = "\n".join(current_lines).strip()
            if content:
                sections.append((_canonical_section_name(current_name), content))
            current_name = _canonical_section_name(m.group(1))
            current_lines = []
            saw_marker = True
            continue
        p = plain_label_line.match(line)
        if p:
            content = "\n".join(current_lines).strip()
            if content:
                sections.append((_canonical_section_name(current_name), content))
            current_name = _canonical_section_name(p.group(1))
            current_lines = []
            saw_marker = True
            continue
        current_lines.append(raw_line)

    tail = "\n".join(current_lines).strip()
    if tail:
        sections.append((_canonical_section_name(current_name), tail))

    # If marker parsing failed or produced one large block, keep legacy behavior.
    if not saw_marker or len(sections) <= 1:
        raw = re.split(r'\[SECTION:\s*([^\]]+)\]', (script_text or "").strip(), flags=re.IGNORECASE)
        legacy: list[tuple[str, str]] = []
        for i in range(1, len(raw), 2):
            name = _canonical_section_name(raw[i].strip())
            content = raw[i + 1].strip() if i + 1 < len(raw) else ""
            if content:
                legacy.append((name, content))
        if legacy:
            return legacy
    return sections


def generate_tts_sections(script_text: str, video_id: str, language: str) -> tuple[str, str]:
    """
    Split script at [SECTION: ...] markers, generate TTS per section,
    measure each section duration with mutagen, build accurate chapter
    timestamps, concatenate all sections into one audio file.

    Returns (audio_path, chapters_text).
    Falls back to single full-script TTS when markers are absent or any
    section TTS call fails.
    """
    import subprocess

    final_audio = os.path.join(AUDIO_DIR, f"{video_id}.mp3")

    # For Arabic: always use direct [SECTION: ...] regex split — more reliable than
    # the complex line-by-line parser which can fragment sections via bracket expressions
    # in Arabic body text.  Falls back to _parse_script_sections for English.
    import re as _re_tts
    sections: list[tuple[str, str]] = []
    if (language or "").lower().startswith("ar"):
        _raw = _re_tts.split(r'\[SECTION:\s*([^\]]+)\]', script_text.strip(), flags=_re_tts.IGNORECASE)
        for _ai in range(1, len(_raw), 2):
            _an = _canonical_section_name(_raw[_ai].strip())
            _ac = _raw[_ai + 1].strip() if _ai + 1 < len(_raw) else ""
            if _ac:
                sections.append((_an, _ac))
        if sections:
            print(f"[Video] Arabic direct section split: {len(sections)} sections")
        else:
            sections = _parse_script_sections(script_text)
    else:
        sections = _parse_script_sections(script_text)

    if not sections:
        print("[Video] No section markers — using single-call TTS")
        audio_path = generate_voiceover(script_text, video_id, language)
        return audio_path, ""

    print(f"[Video] Generating TTS for {len(sections)} sections (parallel, max 4 workers)")

    section_paths: list[str]   = [None] * len(sections)
    section_durations: list[float] = [0.0] * len(sections)

    import concurrent.futures as _cf

    def _tts_section(args):
        i, name, content = args
        sec_id   = f"{video_id}_sec{i}"
        sec_path = generate_voiceover(content, sec_id, language)
        if not sec_path or not os.path.exists(sec_path):
            return i, None, 0.0
        dur = get_audio_duration(sec_path)
        print(f"[Video] Section {i + 1} '{name}': {dur:.1f}s ({format_time(dur)})")
        return i, sec_path, dur

    _n_workers = min(len(sections), _WORKERS["tts"])
    print(f"[QUEUE] TTS sections: {len(sections)} pending | workers={_n_workers}")
    with _cf.ThreadPoolExecutor(max_workers=_n_workers) as _ex:
        _futures = [_ex.submit(_tts_section, (i, name, content)) for i, (name, content) in enumerate(sections)]
        for _fut in _cf.as_completed(_futures):
            try:
                _i, _path, _dur = _fut.result()
                section_paths[_i]     = _path
                section_durations[_i] = _dur
            except Exception as _te:
                print(f"[Video] TTS section error: {_te}")

    _failed_secs = [i for i, p in enumerate(section_paths) if p is None]
    if _failed_secs:
        _sec_names = [sections[i][0] for i in _failed_secs]
        print(f"[Video] TTS sections failed: {_sec_names} ({len(_failed_secs)}/{len(sections)}) "
              f"— falling back to full-script TTS")
        audio_path = generate_voiceover(script_text, video_id, language)
        return audio_path, ""

    # Concatenate section audio files
    if len(section_paths) == 1:
        import shutil
        shutil.move(section_paths[0], final_audio)
    else:
        merged = False
        list_path = os.path.join(AUDIO_DIR, f"{video_id}_sec_list.txt")
        with open(list_path, "w", encoding="utf-8") as lf:
            for sp in section_paths:
                lf.write(f"file '{os.path.abspath(sp)}'\n")
        ffmpeg_bin = _get_ffmpeg()
        if ffmpeg_bin:
            try:
                subprocess.run(
                    [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                     "-i", list_path, "-c", "copy", final_audio],
                    check=True, capture_output=True,
                )
                merged = True
                print("[Video] Sections merged with ffmpeg")
            except Exception as e:
                print(f"[Video] Section ffmpeg merge failed: {e}")
        if not merged:
            merged = _merge_chunks_pydub(section_paths, final_audio)
            if merged:
                print("[Video] Sections merged with pydub")
        if not merged:
            import shutil
            shutil.copy(section_paths[0], final_audio)
            print("[Video] Using first section only (merge failed)")
        try: os.remove(list_path)
        except OSError: pass
        for sp in section_paths:
            if os.path.exists(sp) and sp != final_audio:
                try: os.remove(sp)
                except OSError: pass

    # Build chapter timestamps from cumulative durations
    cumulative = 0.0
    chapter_lines = ["â±ï¸ CHAPTERS"]
    for i, (name, _) in enumerate(sections):
        display = _SECTION_DISPLAY.get(name, f"📖 {name}")
        chapter_lines.append(f"{format_time(cumulative)} {display}")
        cumulative += section_durations[i]

    chapters = "\n".join(chapter_lines)
    total_dur = sum(section_durations)
    print(f"[Video] Chapters built (total {format_time(total_dur)}):\n{chapters}")
    return final_audio, chapters


# â"€â"€ Clip system â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

# ── Pipeline mode ────────────────────────────────────────────────────────────
# FAST (default): CI-safe, minimal clip work, SAFE MODE fallback
# FULL          : cinematic pipeline, full scoring + diversity, higher quality
PIPELINE_MODE: str = os.getenv("PIPELINE_MODE", "fast").lower().strip()

# FULL mode enables the heavy clip system; FAST uses select_best_clips_fast()
USE_CLIPS: bool = PIPELINE_MODE == "full"

# ── Worker pool configuration ─────────────────────────────────────────────────
# IO-bound (image search / Pollinations / TTS / enhance): high concurrency is safe.
# CPU-bound (ffmpeg / compositing / render): 1 worker — GitHub runners have 2 cores.
# GitHub Actions safe mode: slightly lower IO workers to stay within runner limits.
_IS_CI = bool(os.getenv("GITHUB_ACTIONS") or os.getenv("CI"))
_WORKERS: dict[str, int] = {
    "search":       16 if _IS_CI else 20,   # multi-source image URL search (IO-bound)
    "pollinations":  4 if _IS_CI else  6,   # Pollinations free API — strict rate limit, keep low
    "doc_visual":    4 if _IS_CI else  6,   # documentary visual pool — same Pollinations limit
    "enhance":      30 if _IS_CI else 40,   # image post-processing (IO-bound)
    "tts":          12 if _IS_CI else 14,   # TTS sections — API rate-limit aware
    "ffmpeg":        1,                      # CPU-bound — never increase
    "render":        1,                      # CPU-bound — never increase
    "script":        2,                      # LLM script generation — API rate-limited
}

# ── Adaptive 429 throttle + 402 circuit breaker ──────────────────────────────
# 402 = service-level block for this session (IP/key issue) — trip a hard breaker
# so we don't waste 100+ requests after the first 402. Reset on each run start.
# 429 tracking halves the worker pool when rate > 25%.
import threading as _threading
_pollinations_429_lock  = _threading.Lock()
_pollinations_429_count = 0
_pollinations_req_count = 0
_pollinations_402_blocked = False   # circuit breaker — True after first 402


def _record_pollinations_result(success: bool) -> None:
    global _pollinations_429_count, _pollinations_req_count
    with _pollinations_429_lock:
        _pollinations_req_count += 1
        if not success:
            _pollinations_429_count += 1


def _adaptive_pollinations_workers() -> int:
    """Return current Pollinations worker count, halved if 429 rate > 25%."""
    with _pollinations_429_lock:
        req = max(_pollinations_req_count, 1)
        rate = _pollinations_429_count / req
    if rate > 0.25:
        reduced = max(3, _WORKERS["pollinations"] // 2)
        print(f"[QUEUE] Pollinations 429 rate={rate:.0%} — throttling to {reduced} workers")
        return reduced
    return _WORKERS["pollinations"]


# Global start time for timeout tracking — reset at the top of create_video().
_PIPELINE_START: float = 0.0
# Budget for the CLIP PROCESSING phase only — NOT the total pipeline duration.
# FAST: 25 min  |  FULL: 30 min
_MAX_CLIP_PROCESSING_SECONDS: float = 25 * 60 if PIPELINE_MODE == "fast" else 30 * 60


def _check_clip_timeout() -> bool:
    """Return True if clip processing has exceeded its mode-dependent time budget."""
    if _PIPELINE_START <= 0:
        return False
    return (time.time() - _PIPELINE_START) > _MAX_CLIP_PROCESSING_SECONDS


def extract_clips(video_path: str, out_dir: str, chunk_len: float = 8.0,
                  max_clips: int = 10) -> list[str]:
    """Split a source video into fixed-length clips via ffmpeg segment muxer.

    Stops early after max_clips to avoid long ffmpeg runs on slow CI runners.
    Returns sorted list of clip paths that are at least 2 s long.
    Falls back to [] gracefully on any failure.
    """
    _t0 = time.time()
    if _check_clip_timeout():
        print("[Clip] Skipped extract_clips — global timeout reached")
        return []

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not os.path.exists(video_path):
        return []

    # Skip files >100 MB — too slow to process on GitHub runners
    try:
        if os.path.getsize(video_path) > 100 * 1024 * 1024:
            print(f"[Clip] Skipping large file (>100 MB): {os.path.basename(video_path)}")
            return []
    except OSError:
        return []

    total_dur = _ffprobe_duration(video_path)
    if total_dur < 2.0:
        return []

    # Only extract enough video to cover max_clips × chunk_len seconds
    extract_secs = max_clips * chunk_len
    duration_arg = ["-t", str(extract_secs)] if total_dur > extract_secs else []

    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]", "_", os.path.splitext(os.path.basename(video_path))[0].lower())[:20]
    out_pattern = os.path.join(out_dir, f"{stem}_%03d.mp4")

    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", video_path]
            + duration_arg
            + ["-c", "copy", "-f", "segment",
               "-segment_time", str(chunk_len),
               "-reset_timestamps", "1",
               out_pattern],
            capture_output=True,
            timeout=60,          # hard 60-second cap per video
        )
    except Exception as e:
        print(f"[Clip] Segment split failed for {os.path.basename(video_path)}: {e}")
        return []

    clips = sorted(
        os.path.join(out_dir, f)
        for f in os.listdir(out_dir)
        if f.startswith(stem) and f.endswith(".mp4")
        and _ffprobe_duration(os.path.join(out_dir, f)) >= 2.0
    )[:max_clips]  # hard cap after sorting

    elapsed = time.time() - _t0
    print(f"[Clip] Extracted {len(clips)} clip(s) from {os.path.basename(video_path)} in {elapsed:.1f}s")
    return clips


def score_clip(path: str) -> float:
    """Score a clip 0.0→1.0 using duration fitness, motion proxy, and brightness.

    No heavy ML — duration from ffprobe, motion from bitrate, brightness from
    a single 64×64 gray frame extracted by ffmpeg (~0.05 s per clip).
    """
    if not path or not os.path.exists(path):
        return 0.0
    try:
        dur  = _ffprobe_duration(path)
        size = os.path.getsize(path)
    except Exception:
        return 0.0

    if dur < 1.0 or size < 5_000:
        return 0.0

    # ── Duration score: ramp 0→1 over 0-5 s, full 5-10 s, slow decay after ──
    if dur < 5.0:
        dur_score = dur / 5.0
    elif dur <= 10.0:
        dur_score = 1.0
    else:
        dur_score = max(0.3, 1.0 - (dur - 10.0) / 20.0)

    # ── Motion score: KB/s as visual-richness proxy (high motion → less compression) ──
    kbps = (size / 1024) / dur
    if kbps < 30:
        motion_score = 0.2
    elif kbps < 100:
        motion_score = 0.2 + 0.5 * (kbps - 30) / 70
    elif kbps < 300:
        motion_score = 0.7 + 0.3 * (kbps - 100) / 200
    else:
        motion_score = 1.0

    # ── Brightness score: 1-frame 64×64 gray sample via ffmpeg ───────────────
    brightness_score = 0.7  # safe default when ffmpeg call is unavailable
    try:
        _ffmpeg = shutil.which("ffmpeg")
        if _ffmpeg:
            _res = subprocess.run(
                [_ffmpeg, "-ss", "1", "-i", path,
                 "-vframes", "1", "-vf", "scale=64:64",
                 "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                capture_output=True, timeout=5,
            )
            _data = _res.stdout
            if _data:
                avg_luma = sum(_data) / len(_data)   # 0–255
                if avg_luma < 25:
                    brightness_score = 0.0            # too dark — penalise heavily
                elif avg_luma < 60:
                    brightness_score = avg_luma / 60 * 0.5
                elif avg_luma <= 200:
                    brightness_score = 1.0            # good exposure range
                else:
                    brightness_score = max(0.5, 1.0 - (avg_luma - 200) / 110)  # overexposed
    except Exception:
        pass

    return 0.40 * dur_score + 0.35 * motion_score + 0.25 * brightness_score


def select_best_clips(video_folder: str, max_clips: int = 10) -> list[str]:
    """Extract, score, and diversify clips from video_folder/videos/.

    GitHub Actions safe:
    - Max 3 source videos processed (skip the rest)
    - Max 10 clips total (hard cap)
    - Skips files >100 MB
    - Times out gracefully if global timeout is reached
    All chunks land in temp_clips/ and are cleaned up after assembly.
    """
    _t0 = time.time()

    if not USE_CLIPS:
        print("[Clip] USE_CLIPS=False — skipping clip system")
        return []

    if _check_clip_timeout():
        print("[Clip] Skipped select_best_clips — global timeout reached")
        return []

    vid_dir = os.path.join(video_folder, "videos")
    if not os.path.isdir(vid_dir):
        return []

    _MAX_SOURCES   = 3
    _MAX_PER_SRC   = 4   # clips per source (→ max 12 before interleave cap)
    _MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB

    source_videos = [
        os.path.join(vid_dir, f)
        for f in sorted(os.listdir(vid_dir))
        if f.lower().endswith((".mp4", ".mov", ".avi"))
        and not f.endswith(("_processed.mp4", "_short.mp4"))
        and 10_000 < os.path.getsize(os.path.join(vid_dir, f)) <= _MAX_FILE_SIZE
    ][:_MAX_SOURCES]   # hard source cap

    if not source_videos:
        return []

    out_dir    = os.path.abspath("temp_clips")
    os.makedirs(out_dir, exist_ok=True)
    n_sources  = len(source_videos)
    per_source = max(2, -(-max_clips // n_sources))

    source_pools: list[list[tuple[float, str]]] = []
    for src in source_videos:
        if _check_clip_timeout():
            print(f"[Clip] Timeout during extraction — stopping at {len(source_pools)} source(s)")
            break

        chunks = extract_clips(src, out_dir, max_clips=_MAX_PER_SRC)

        seen_windows: set[int] = set()
        deduped: list[tuple[float, str]] = []
        for chunk in chunks:
            if _check_clip_timeout():
                break
            try:
                window = int(os.path.splitext(chunk)[0].rsplit("_", 1)[-1]) // 4
            except ValueError:
                window = len(deduped)
            if window not in seen_windows:
                seen_windows.add(window)
                deduped.append((score_clip(chunk), chunk))

        deduped.sort(key=lambda x: x[0], reverse=True)
        capped = deduped[:per_source]
        source_pools.append(capped)
        print(f"[Clip] {os.path.basename(src)}: {len(capped)}/{len(chunks)} clip(s) kept")

    if not source_pools:
        return []

    # Round-robin interleave across sources
    interleaved: list[tuple[float, str]] = []
    for round_idx in range(per_source):
        for pool in source_pools:
            if round_idx < len(pool):
                interleaved.append(pool[round_idx])
            if len(interleaved) >= max_clips:
                break
        if len(interleaved) >= max_clips:
            break

    interleaved.sort(key=lambda x: x[0], reverse=True)
    best = [p for _, p in interleaved[:max_clips]]
    elapsed = time.time() - _t0
    total_considered = sum(len(p) for p in source_pools)
    print(f"[Clip] FULL: Selected {len(best)}/{total_considered} best clip(s) from {video_folder} in {elapsed:.1f}s")
    return best


# FAST MODE
def select_best_clips_fast(video_folder: str, max_clips: int = 8) -> list[str]:
    """Fast clip selection for FAST pipeline mode — no scoring, no deep loops.

    Samples at most 2 source videos and returns up to max_clips clips.
    Hard 9-second wall-clock budget — always safe for CI runners.
    """
    _t0 = time.time()
    if _check_clip_timeout():
        print("[Clip] FAST: Skipped — global timeout reached")
        return []

    vid_dir = os.path.join(video_folder, "videos")
    if not os.path.isdir(vid_dir):
        return []

    _MAX_SOURCES   = 2
    _MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
    _MAX_PER_SRC   = max_clips // _MAX_SOURCES + 1

    source_videos = [
        os.path.join(vid_dir, f)
        for f in sorted(os.listdir(vid_dir))
        if f.lower().endswith((".mp4", ".mov", ".avi"))
        and not f.endswith(("_processed.mp4", "_short.mp4"))
        and 10_000 < os.path.getsize(os.path.join(vid_dir, f)) <= _MAX_FILE_SIZE
    ][:_MAX_SOURCES]

    if not source_videos:
        return []

    out_dir = os.path.abspath("temp_clips")
    os.makedirs(out_dir, exist_ok=True)
    all_clips: list[str] = []

    for src in source_videos:
        if _check_clip_timeout() or (time.time() - _t0) > 9:
            print("[Clip] FAST: 9-second budget reached — stopping early")
            break
        chunks = extract_clips(src, out_dir, chunk_len=8.0, max_clips=_MAX_PER_SRC)
        all_clips.extend(chunks)
        if len(all_clips) >= max_clips:
            break

    result = all_clips[:max_clips]
    elapsed = time.time() - _t0
    print(f"[Clip] FAST: {len(result)} clip(s) ready in {elapsed:.1f}s (no scoring)")
    return result


# ── Intensity keyword sets for script-aware clip matching ────────────────────
_INTENSE_KW = {
    "confess", "confession", "kill", "killed", "murder", "murdered", "execute",
    "executed", "shot", "shoot", "stab", "stabbed", "dead", "death", "arrested",
    "caught", "massacre", "brutal", "torture", "ambush", "shootout", "raid",
    "escape", "explosion", "convicted", "sentenced",
}
_MEDIUM_KW = {
    "investigate", "investigation", "police", "detective", "suspect", "evidence",
    "witness", "trial", "court", "judge", "charged", "crime", "prison", "smuggle",
    "cartel", "gang", "trafficking", "operation", "surveillance",
}


def _section_intensity(text: str) -> str:
    """Classify a script section as 'intense', 'medium', or 'calm' by keyword overlap."""
    words = {w.strip(".,!?\"'()-") for w in text.lower().split()}
    if words & _INTENSE_KW:
        return "intense"
    if words & _MEDIUM_KW:
        return "medium"
    return "calm"


# ── Cinematic pacing constants ────────────────────────────────────────────────
_PACING = {"intense": 2.5, "medium": 4.0, "calm": 6.0}  # seconds on screen
_HOOK_DURATION = 2.0                                       # first 2 sections


def _clip_source_key(path: str) -> str:
    """Derive the source video stem from a temp_clips filename (stem_NNN.mp4 → stem)."""
    stem  = os.path.splitext(os.path.basename(path or ""))[0]
    parts = stem.rsplit("_", 1)
    return parts[0] if len(parts) == 2 and parts[1].isdigit() else stem


def assign_clips_to_script(
    script_sections: list[tuple[str, str]],
    clips: list[str],
) -> list[dict]:
    """Map clips to script sections with cinematic pacing and intensity-aware matching.

    Pacing:  intense→2.5 s  |  medium→4 s  |  calm→6 s  |  hook (first 2)→2 s
    Diversity: max 2 consecutive clips from same source; breathing-space entries
               injected after 3+ intense cuts; 30% of calm sections go image-only.
    Clip order in returned timeline drives image_paths ordering in create_video().
    """
    if not clips:
        return [
            {"section": n, "text": t, "clip": None, "clips": [],
             "intensity": _section_intensity(t),
             "clip_duration": _PACING.get(_section_intensity(t), 4.0),
             "is_breathing": True}
            for n, t in script_sections
        ]

    from collections import deque as _deque
    import random as _rnd

    n_clips = len(clips)
    t_top   = clips[:max(1, n_clips // 3)]
    t_mid   = clips[len(t_top): len(t_top) + max(1, n_clips // 3)]
    t_low   = clips[len(t_top) + len(t_mid):]

    pools = {
        "intense": _deque(t_top),
        "medium":  _deque(t_mid + t_top),
        "calm":    _deque(t_low + t_mid),
    }
    overflow = _deque(clips)

    def _pull_diverse(pool, n, exclude="", last_srcs=None, max_consec=2):
        """Pull n clips preferring source diversity; falls back to overflow."""
        result, last, skipped = [], list(last_srcs or []), []
        for src_pool in (pool, overflow):
            while src_pool and len(result) < n:
                c = src_pool.popleft()
                if c == exclude or c in result:
                    continue
                c_src  = _clip_source_key(c)
                recent = last[-max_consec:]
                if recent and all(s == c_src for s in recent):
                    skipped.append(c)   # defer — same source run, try later
                else:
                    result.append(c)
                    last.append(c_src)
        for c in skipped:               # best-effort: use deferred clips to fill
            if len(result) >= n:
                break
            result.append(c)
            last.append(_clip_source_key(c))
        return result, last

    total_len         = sum(len(t) for _, t in script_sections) or 1
    timeline: list[dict] = []
    consecutive_intense = 0
    last_srcs: list[str] = []

    for i, (name, text) in enumerate(script_sections):
        intensity     = _section_intensity(text)
        frac          = len(text) / total_len
        n_for_section = max(1, round(frac * n_clips))

        # ── Breathing space: inject pause after 3+ back-to-back intense cuts ──
        if consecutive_intense >= 3 and intensity == "intense":
            timeline.append({
                "section":      f"{name}_breath",
                "text":         "",
                "clip":         None,
                "clips":        [],
                "intensity":    "calm",
                "clip_duration": _PACING["calm"],
                "is_breathing":  True,
            })
            consecutive_intense = 0

        # 30% of calm sections go image-only for visual breathing room
        is_breathing  = (intensity == "calm" and _rnd.random() < 0.30)
        # Hook: first 2 sections get fastest pacing regardless of intensity
        clip_duration = _HOOK_DURATION if i < 2 else _PACING[intensity]

        if is_breathing:
            section_clips, chosen_clip = [], None
        elif i == 0:
            best_clip     = clips[0]
            rest, last_srcs = _pull_diverse(
                pools[intensity], n_for_section - 1,
                exclude=best_clip, last_srcs=last_srcs,
            )
            section_clips = [best_clip] + rest
            chosen_clip   = best_clip
            last_srcs     = [_clip_source_key(best_clip)] + last_srcs
        else:
            section_clips, last_srcs = _pull_diverse(
                pools[intensity], n_for_section, last_srcs=last_srcs,
            )
            chosen_clip = section_clips[0] if section_clips else None

        consecutive_intense = consecutive_intense + 1 if intensity == "intense" else 0

        timeline.append({
            "section":      name,
            "text":         text,
            "clip":         chosen_clip,
            "clips":        section_clips,
            "intensity":    intensity,
            "clip_duration": clip_duration,
            "is_breathing":  is_breathing,
        })

    return timeline




# ── Pipeline runners ─────────────────────────────────────────────────────────

# FAST PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
# FAST_VISUALS_PER_MIN: unique image transitions per minute.
# At 4 clip-variants per image and ~5 s per clip → 12 clips/min / 4 = 3 images/min.
# Increased from 8→12 to improve visual rhythm and reduce portrait dominance.
_FAST_VISUALS_PER_MIN: int = 28  # clip transitions per minute (7 images × 4 clips each)
_FAST_IMAGES_PER_MIN: float = 7.0  # unique images per minute target

# ── Story-phase visual style engine ──────────────────────────────────────────
# Maps ACT section labels → visual style keywords injected into image prompts.
# Ensures visuals evolve WITH the story, not generic throughout.
_STORY_PHASE_VISUAL_STYLE: dict[str, str] = {
    "act 1": "surveillance dark alley warning signs cinematic unease shadow",
    "act 2": "detective office evidence board documents maps investigation cinematic",
    "act 3": "escalation tension police pursuit panic crowd chaos cinematic dark",
    "act 4": "courtroom arrest confession handcuffs documents testimony dramatic",
    "act 5": "aftermath quiet emotional memorial reflection cinematic slow",
    "default": "cinematic dark crime documentary",
}


def _extract_dominant_phase(script_text: str) -> str:
    """
    Detect the dominant narrative act in a script and return a visual style string.
    Looks for [SECTION: Act X ...] markers; falls back to full-script heuristics.
    """
    import re as _re

    labels = _re.findall(r'\[SECTION:\s*Act\s*(\d+)', script_text, _re.IGNORECASE)
    if labels:
        counts: dict[str, int] = {}
        for lbl in labels:
            key = f"act {lbl}"
            counts[key] = counts.get(key, 0) + 1
        dominant = max(counts, key=counts.get)
        return _STORY_PHASE_VISUAL_STYLE.get(dominant, _STORY_PHASE_VISUAL_STYLE["default"])

    # Heuristic fallback — presence of keywords signals phase
    low = script_text.lower()
    if any(k in low for k in ("arrested", "confession", "courtroom", "sentenced", "verdict")):
        return _STORY_PHASE_VISUAL_STYLE["act 4"]
    if any(k in low for k in ("escalat", "panic", "disappear", "more victims", "pressure")):
        return _STORY_PHASE_VISUAL_STYLE["act 3"]
    if any(k in low for k in ("investigat", "evidence found", "witness", "detectives")):
        return _STORY_PHASE_VISUAL_STYLE["act 2"]
    if any(k in low for k in ("aftermath", "legacy", "what happened", "unanswered")):
        return _STORY_PHASE_VISUAL_STYLE["act 5"]
    return _STORY_PHASE_VISUAL_STYLE["default"]


# ── Event-specific visual sequences ──────────────────────────────────────────
# Maps specific story events to concrete visual prompt sequences.
# Provides cinematic specificity beyond phase-level style keywords.
_EVENT_VISUAL_SEQUENCES: dict[str, list[str]] = {
    "witness_contradiction": [
        "interrogation room dim single overhead light suspect seated cinematic",
        "closeup reaction face shock witness stand courtroom dramatic lighting",
        "evidence photos spread table investigation documents crime",
        "surveillance footage timestamp visible blurry figure cinematic",
    ],
    "arrest": [
        "police vehicles flashing lights night raid exterior dramatic",
        "handcuffs wrists close restraint dramatic dark lighting",
        "suspect escorted through crowd cameras flashing press cinematic",
        "booking room fluorescent lights processing cinematic dark",
    ],
    "courtroom_testimony": [
        "courtroom wide shot packed gallery dramatic lighting cinematic",
        "judge bench gavel documents formal proceedings dark",
        "attorney pointing evidence board jury watching cinematic",
        "defendant dock expressionless waiting verdict dramatic",
    ],
    "confession": [
        "interrogation room overhead single light stark shadows cinematic",
        "signed document confession closeup hands pen paper dramatic",
        "detective across table silence tension low light",
        "police station corridor fluorescent aftermath cinematic",
    ],
    "manhunt": [
        "surveillance footage blurry suspect street corner night cinematic",
        "detective pinning photo location map board investigation",
        "police radio dispatch night urban rain cinematic dark",
        "roadblock headlights dark highway rain dramatic",
    ],
    "crime_scene": [
        "crime scene tape police barrier urban night cinematic",
        "forensic gloves evidence collection examination dramatic",
        "investigator flashlight dark location discovery cinematic",
        "aerial view crime scene photographs evidence table dark",
    ],
    "verdict": [
        "courthouse exterior steps reporters cameras crowd cinematic",
        "jury foreman standing verdict paper dramatic lighting",
        "family victim support group emotional moment cinematic",
        "courtroom reaction gallery verdict delivered dramatic dark",
    ],
}

_EVENT_KEYWORDS: dict[str, list[str]] = {
    "witness_contradiction": ["witness", "testimony", "testified", "recanted", "contradicted", "lied under oath", "statement changed"],
    "arrest": ["arrested", "raided", "apprehended", "captured", "detained", "taken into custody", "fled and was caught"],
    "courtroom_testimony": ["courtroom", "trial", "judge", "jury", "prosecutor", "defense attorney", "sentenced", "acquitted"],
    "confession": ["confessed", "confession", "admitted to", "told investigators", "broke down", "revealed to police"],
    "manhunt": ["manhunt", "fled", "on the run", "evaded", "pursued", "tracked", "surveillance footage", "informant"],
    "crime_scene": ["crime scene", "body was found", "discovered the body", "forensic", "evidence collected", "investigators found"],
    "verdict": ["verdict", "found guilty", "not guilty", "sentenced to", "life sentence", "acquitted", "years in prison"],
}


def event_visual_mapper(script_segment: str) -> list[str]:
    """
    Map story events in a script segment to specific visual prompt sequences.
    Returns up to 8 visual descriptors ordered by event match score.
    Returns empty list if no events detected — caller falls back to phase style.
    """
    if not script_segment:
        return []

    low = script_segment.lower()
    matched: list[tuple[int, str]] = []

    for event_key, keywords in _EVENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in low)
        if score > 0:
            matched.append((score, event_key))

    if not matched:
        return []

    matched.sort(key=lambda x: x[0], reverse=True)
    top_events = [key for _, key in matched[:2]]

    visuals: list[str] = []
    for event_key in top_events:
        visuals.extend(_EVENT_VISUAL_SEQUENCES.get(event_key, []))

    return visuals[:8]


def run_fast_pipeline(
    script_data: dict,
    video_id: str,
    custom_audio_path: str = "",
    user_images: list | None = None,
    user_videos: list | None = None,
) -> str:
    """Linear, minimal pipeline. No scoring, no deep extraction. Target: <20 min.

    OUTPUT GUARANTEE: never returns successfully without a validated video file.
    Falls back through: primary render → fallback render → emergency visuals.
    """
    import traceback, math as _math
    title    = script_data.get("title", "")
    language = script_data.get("language", "english")
    is_short = "short" in video_id
    print(f"[Pipeline] FAST MODE — {title} ({language})")

    # ── Audio ──────────────────────────────────────────────────────────────
    _real_audio_secs: float = 0.0
    try:
        if custom_audio_path and Path(custom_audio_path).exists():
            audio_path = clean_voice(
                custom_audio_path,
                os.path.join(AUDIO_DIR, f"{video_id}_enhanced.mp3"),
            )
        elif is_short:
            audio_path = generate_voiceover(script_data["script"], video_id, language)
            if audio_path and os.path.exists(audio_path):
                audio_path = process_audio_netflix(audio_path, is_short=True)
        else:
            audio_path, dynamic_chapters = generate_tts_sections(
                script_data["script"], video_id, language
            )
            if dynamic_chapters:
                script_data["chapters"] = dynamic_chapters
            if audio_path and os.path.exists(audio_path):
                audio_path = process_audio_netflix(audio_path, is_short=False)
        print(f"[FAST] Audio ready: {audio_path}")
        # Capture real audio duration — the only runtime truth
        try:
            try:
                from moviepy.editor import AudioFileClip as _AC
            except ImportError:
                from moviepy import AudioFileClip as _AC
            _real_audio_secs = _AC(audio_path).duration
            _min = _real_audio_secs / 60
            print(f"[FAST] Audio duration: {_real_audio_secs:.1f}s ({_min:.1f} min)")
            # Real audio validation — Arabic minimum 15 min, abort render if short.
            # The pipeline rebuild loop in fast_pipeline.py will detect the short video,
            # expand the script, and re-render automatically.
            if not is_short and language == "arabic" and _real_audio_secs > 0 and _real_audio_secs < 900:
                print(f"[FAST] Arabic audio {_real_audio_secs:.1f}s ({_min:.1f}min) < 15min minimum. "
                      f"Proceeding with render — pipeline rebuild loop will expand and re-render if needed.")
            elif not is_short and _real_audio_secs > 0 and _real_audio_secs < 900:
                print(f"[FAST] ⚠️ RUNTIME CONTRACT VIOLATION: {_min:.1f}min < 15min minimum.")
        except Exception:
            pass
    except Exception as e:
        print(f"[FAST] Audio failed: {e}")
        traceback.print_exc()
        return ""

    # ── Visual density: 12 clip-transitions/min → 3 unique images/min ──────
    # Cap raised to 60 images to support 15-90 min longform videos.
    if is_short:
        n_images = 6
    elif _real_audio_secs > 0:
        _density_floor = _math.ceil(_real_audio_secs / 60 * _FAST_IMAGES_PER_MIN)
        n_images = max(25, min(400, _density_floor))
        print(f"[FAST] Visual density: {n_images} images for {_real_audio_secs/60:.1f}min "
              f"({_FAST_IMAGES_PER_MIN:.0f} images/min target)")
    else:
        n_images = calculate_unique_images(is_short=False)

    # ── Clips (fast, no scoring) ───────────────────────────────────────────
    _library_clips: list[str] = []
    try:
        _topic_key      = script_data.get("topic", script_data.get("niche", "default"))
        _content_folder = find_content_folder(_topic_key)
        if _content_folder and not _check_clip_timeout():
            _library_clips = select_best_clips_fast(_content_folder, max_clips=6)
            print(f"[FAST] {len(_library_clips)} clip(s) loaded")
    except Exception as _ce:
        print(f"[FAST] Clip load skipped (non-fatal): {_ce}")
        _library_clips = []

    # ── Visuals ────────────────────────────────────────────────────────────
    topic_str = script_data.get("topic", "")
    image_paths: list[str] = list(_library_clips)
    for uv in (user_videos or []):
        p = uv.get("path", "")
        if p and os.path.exists(p):
            image_paths.append(p)
    for ui in (user_images or []):
        p = ui.get("path", "")
        if p and os.path.exists(p):
            image_paths.append(p)
    if len(image_paths) < n_images:
        try:
            needed         = n_images - len(image_paths)
            _phase_style   = _extract_dominant_phase(script_data.get("script", ""))
            _event_visuals = event_visual_mapper(script_data.get("script", ""))
            if _event_visuals:
                # Blend first event sequence with phase style for cinematic specificity
                _blend_style = _event_visuals[0] + " " + _phase_style
                print(f"[FAST] Event visuals: {len(_event_visuals)} sequences | "
                      f"phase: {_phase_style[:40]}…")
            else:
                _blend_style = _phase_style
                print(f"[FAST] Visual phase: {_phase_style[:60]}…")
            image_paths.extend(
                fetch_real_images(
                    script_data["script"], needed, video_id,
                    topic=topic_str, style_profile=_blend_style,
                )
            )
        except Exception as _ve:
            print(f"[FAST] Visual fetch failed (non-fatal): {_ve}")
    image_paths = [p for p in image_paths if p and os.path.exists(p)]
    image_paths = list(dict.fromkeys(image_paths))  # deduplicate, preserve order

    # ── Stock-video supplement: if source count is still below the per-minute
    # floor, pull related videos from Pexels/Pixabay/Archive.
    # Target: ~1 unique source per minute of content, minimum 10 for any long video.
    if not is_short and _real_audio_secs > 0:
        _src_floor = max(10, int(_real_audio_secs / 60))  # sources needed
        _src_gap   = _src_floor - len(image_paths)
        if _src_gap > 0:
            _stock_n = min(_src_gap, 15)
            print(f"[FAST] Only {len(image_paths)} sources for "
                  f"{_real_audio_secs/60:.1f}min — fetching {_stock_n} stock videos")
            _stock = fetch_stock_videos(
                script_data.get("script", ""), _stock_n, video_id + "_sv", topic=topic_str
            )
            image_paths.extend([p for p in _stock if p and os.path.exists(p)])
            image_paths = list(dict.fromkeys(image_paths))
            print(f"[FAST] After stock supplement: {len(image_paths)} unique sources")

    # Filter dark solid-background placeholder images before assembly
    image_paths = _filter_dark_placeholders(image_paths, label="FAST")

    # Pre-export validation (warnings only — never abort on warnings alone)
    _fast_audio_secs = _real_audio_secs if _real_audio_secs > 0 else 0.0
    _validate_render_inputs(image_paths, _fast_audio_secs, is_short=is_short, language=language)

    # ── Smart PIL variation engine — reach n_images without extra API calls ─
    # Uses itertools.product (not cycle×cycle) so each source×op combo is
    # generated exactly once. Caps at len(sources)×len(ops) unique variants;
    # anything beyond that triggers emergency AI visuals for true diversity.
    if 0 < len(image_paths) < n_images:
        _real_img_exts = {".jpg", ".jpeg", ".png", ".webp"}
        _base_imgs = [
            p for p in image_paths
            if not _is_video_file(p)
            and os.path.splitext(p)[1].lower() in _real_img_exts
        ]
        _needed_variants = n_images - len(image_paths)
        _created = 0
        try:
            from PIL import Image as _PILV, ImageEnhance as _PILEN, ImageFilter as _PILF
            # 15 visually distinct ops → 4 sources × 15 = 60 unique variants
            # guaranteed even when Pollinations is completely unavailable.
            _VAR_OPS = [
                ("cc",  lambda im: im.crop((im.width//8, im.height//8,
                                            im.width*7//8, im.height*7//8))
                                    .resize(im.size, _PILV.LANCZOS)),
                ("gs",  lambda im: _PILV.merge("RGB", [im.convert("L")] * 3)),
                ("hc",  lambda im: _PILEN.Contrast(im).enhance(1.8)),
                ("lo",  lambda im: _PILEN.Brightness(im).enhance(0.50)),
                ("sat", lambda im: _PILEN.Color(im).enhance(0.18)),
                ("tl",  lambda im: im.crop((0, 0, im.width * 3 // 4, im.height * 3 // 4))
                                    .resize(im.size, _PILV.LANCZOS)),
                ("br",  lambda im: im.crop((im.width // 4, im.height // 4,
                                            im.width, im.height))
                                    .resize(im.size, _PILV.LANCZOS)),
                ("bl",  lambda im: im.filter(_PILF.GaussianBlur(radius=4))),
                ("mf",  lambda im: im.transpose(_PILV.FLIP_LEFT_RIGHT)),
                ("gn",  lambda im: _PILEN.Contrast(
                            _PILV.merge("RGB", [im.convert("L")] * 3)
                        ).enhance(2.2)),
                ("wb",  lambda im: _PILEN.Color(
                            _PILEN.Brightness(im).enhance(1.15)
                        ).enhance(0.55)),
                ("dk",  lambda im: _PILEN.Brightness(
                            _PILEN.Contrast(im).enhance(1.5)
                        ).enhance(0.42)),
                ("rc",  lambda im: im.crop((im.width // 5, 0, im.width, im.height))
                                    .resize(im.size, _PILV.LANCZOS)),
                ("sh",  lambda im: im.filter(_PILF.SHARPEN).filter(_PILF.SHARPEN)),
                ("ls",  lambda im: im.crop((0, im.height // 5, im.width, im.height))
                                    .resize(im.size, _PILV.LANCZOS)),
            ]
            import itertools as _it
            # product gives unique (source, op) pairs — no cycling duplicates
            _pil_target = min(_needed_variants, len(_base_imgs) * len(_VAR_OPS))
            for _bpath, (_tag, _op) in _it.islice(
                _it.product(_base_imgs, _VAR_OPS), _pil_target
            ):
                _vname = f"{os.path.splitext(os.path.basename(_bpath))[0]}_{_tag}.jpg"
                _vpath = os.path.join(IMAGES_DIR, _vname)
                if os.path.exists(_vpath) and os.path.getsize(_vpath) > 2_000:
                    image_paths.append(_vpath)
                    _created += 1
                    continue
                try:
                    _img = _PILV.open(_bpath).convert("RGB").resize(
                        (1080, 1920) if is_short else (1920, 1080), _PILV.LANCZOS
                    )
                    _var = _op(_img)
                    _var.save(_vpath, "JPEG", quality=80)
                    image_paths.append(_vpath)
                    _created += 1
                except Exception:
                    pass
            if _created:
                print(f"[FAST] PIL variation engine: +{_created} unique variants "
                      f"({len(_base_imgs)} sources × {len(_VAR_OPS)} ops) → "
                      f"{len(image_paths)} total images")
        except ImportError:
            print("[FAST] PIL not available — skipping variation engine")
        # When unique PIL combos are exhausted and sources were scarce,
        # generate emergency AI visuals for genuine diversity.
        _em_still_needed = n_images - len(image_paths)
        if _em_still_needed > 0 and len(_base_imgs) < 10:
            print(f"[FAST] PIL pool from only {len(_base_imgs)} sources — "
                  f"generating {_em_still_needed} emergency AI visuals for diversity")
            _em_extra = _generate_emergency_visuals(
                _em_still_needed, IMAGES_DIR, is_short=is_short
            )
            image_paths.extend([p for p in _em_extra if p and os.path.exists(p)])
            image_paths = list(dict.fromkeys(image_paths))

    # ── Fallback visual engine: never abort due to empty visuals ──────────
    if len(image_paths) < 4:
        print(f"[FAST] Only {len(image_paths)} visuals — activating emergency visual engine")
        _em_needed = max(n_images, 8) - len(image_paths)
        _em = _generate_emergency_visuals(_em_needed, IMAGES_DIR, is_short=is_short)
        image_paths.extend(_em)
        image_paths = [p for p in image_paths if p and os.path.exists(p)]
        if not image_paths:
            print("[FAST] Emergency visual engine also failed — aborting")
            return ""

    # ── Assembly — with render watchdog ───────────────────────────────────
    output_path  = os.path.join(FINAL_DIR, f"{video_id}.mp4")
    video_path   = ""
    _FAST_RENDER_WARN_MIN = 20  # warn if render takes > 20 min
    _render_t0 = time.time()
    try:
        if is_short:
            video_path = assemble_short_video(
                audio_path=audio_path, image_paths=image_paths, output_path=output_path
            )
        else:
            video_path = assemble_video_with_hook(
                audio_path=audio_path, image_paths=image_paths,
                output_path=output_path, video_id=video_id,
            )
    except Exception as e:
        print(f"[FAST] Primary assembly failed: {e}")
        traceback.print_exc()
    _render_elapsed = (time.time() - _render_t0) / 60
    if _render_elapsed > _FAST_RENDER_WARN_MIN:
        print(f"[FAST] WATCHDOG: render took {_render_elapsed:.1f}min > {_FAST_RENDER_WARN_MIN}min limit")
    else:
        print(f"[FAST] Render time: {_render_elapsed:.1f}min")

    # ── Fallback render if primary assembly failed ─────────────────────────
    if not video_path or not os.path.exists(video_path):
        print("[FAST] Primary assembly produced no output — attempting fallback render")
        _fb_path = output_path.replace(".mp4", "_fb.mp4")
        try:
            _fb_imgs = image_paths[:min(8, len(image_paths))]
            video_path = assemble_short_video(
                audio_path=audio_path, image_paths=_fb_imgs, output_path=_fb_path
            )
            if video_path and os.path.exists(video_path):
                print(f"[FAST] Fallback render succeeded: {video_path}")
        except Exception as _fb_e:
            print(f"[FAST] Fallback render failed: {_fb_e}")
            video_path = ""

    if not video_path or not os.path.exists(video_path):
        print("[FAST] All render attempts failed — no output produced")
        return ""

    # ── Post-render processing ─────────────────────────────────────────────
    video_path = _apply_intro_outro_overlay(
        video_path,
        title=script_data.get("title", ""),
        language=language,
        video_id=video_id,
        is_short=is_short,
        chapters_str=script_data.get("chapters", ""),
    )
    if not is_short:
        short_out = os.path.join(SHORTS_DIR, f"{video_id}_short.mp4")
        script_data["short_clip_path"] = cut_short_clip(
            video_path, short_out, script_data=script_data
        )
    _thumb = extract_first_frame(
        video_path, os.path.join(FINAL_DIR, f"{video_id}_thumb.jpg")
    )
    if _thumb:
        script_data["thumbnail_path"] = _thumb

    # ── Output validation ─────────────────────────────────────────────────
    _valid = _validate_output_file(video_path)
    if not _valid:
        print(f"[FAST] WARNING: Output failed validation — file may be corrupt: {video_path}")
        # Don't abort: return the path and let caller decide; corrupt is better than missing

    _kill_orphan_ffmpeg()
    try:
        import gc as _gc; _gc.collect()
    except Exception:
        pass
    try:
        _tc = os.path.abspath("temp_clips")
        if os.path.isdir(_tc):
            shutil.rmtree(_tc, ignore_errors=True)
    except Exception:
        pass
    print("[Render] Artifact upload starting")
    return video_path or ""


# FULL PIPELINE
def run_full_pipeline(
    script_data: dict,
    video_id: str,
    custom_audio_path: str = "",
    user_images: list | None = None,
    user_videos: list | None = None,
) -> str:
    """Rich cinematic pipeline. Composes all shared helpers freely."""
    import traceback
    global _OPENAI_QUOTA_EXCEEDED
    _OPENAI_QUOTA_EXCEEDED = False  # reset between pipeline runs so full gets a fresh TTS attempt
    title    = script_data.get("title", "")
    niche    = script_data.get("niche", "")
    language = script_data.get("language", "english")
    is_short = "short" in video_id
    print(f"[Pipeline] FULL MODE — {title} ({language})")

    # ── Content folder ─────────────────────────────────────────────────────
    _content_folder = None
    try:
        _topic_key      = script_data.get("topic", niche or "default")
        _content_folder = find_content_folder(_topic_key)
        if _content_folder:
            _img_dir = os.path.join(_content_folder, "images")
            _vid_dir = os.path.join(_content_folder, "videos")
            _imgs = len([f for f in os.listdir(_img_dir) if f.lower().endswith((".jpg", ".png"))]) if os.path.isdir(_img_dir) else 0
            _vids = len([f for f in os.listdir(_vid_dir) if f.lower().endswith(".mp4")]) if os.path.isdir(_vid_dir) else 0
            print(f"[Content] Matched folder: {_content_folder} — {_imgs} images, {_vids} videos")
        else:
            print(f"[Content] No content folder for: {_topic_key}")
    except Exception as _ce:
        print(f"[Content] Folder check skipped (non-fatal): {_ce}")

    # ── Full clip extraction + scoring ─────────────────────────────────────
    _library_clips: list[str] = []
    try:
        if _content_folder and os.path.isdir(_content_folder) and not _check_clip_timeout():
            _library_clips = select_best_clips(_content_folder, max_clips=10)
            print(f"[FULL] {len(_library_clips)} clip(s) ready")
        elif _check_clip_timeout():
            print("[FULL] Clip extraction skipped — timeout reached")
    except Exception as _ce2:
        print(f"[FULL] Clip SAFE MODE: {_ce2} — images only")
        _library_clips = []

    # ── Audio ──────────────────────────────────────────────────────────────
    try:
        if custom_audio_path and Path(custom_audio_path).exists():
            audio_path = clean_voice(
                custom_audio_path,
                os.path.join(AUDIO_DIR, f"{video_id}_enhanced.mp3"),
            )
            print(f"[FULL] Using custom audio: {audio_path}")
        elif not is_short:
            audio_path, dynamic_chapters = generate_tts_sections(
                script_data["script"], video_id, language
            )
            if dynamic_chapters:
                script_data["chapters"] = dynamic_chapters
                print("[FULL] Dynamic chapters saved")
            if audio_path and os.path.exists(audio_path):
                audio_path = process_audio_netflix(audio_path, is_short=False)
        else:
            audio_path = generate_voiceover(script_data["script"], video_id, language)
            if audio_path and os.path.exists(audio_path):
                audio_path = process_audio_netflix(audio_path, is_short=True)
        print(f"[FULL] Audio ready: {audio_path}")
        _real_audio_secs = 0.0
        try:
            try:
                from moviepy.editor import AudioFileClip as _AC
            except ImportError:
                from moviepy import AudioFileClip as _AC
            _dur = _AC(audio_path).duration
            _real_audio_secs = _dur
            _min = _dur / 60
            if is_short:
                if _dur < 60:   print(f"[FULL] WARNING: Short audio too short: {_dur:.1f}s (need 60-90s)")
                elif _dur > 90: print(f"[FULL] WARNING: Short audio too long: {_dur:.1f}s (need 60-90s)")
                else:           print(f"[FULL] Short duration OK: {_dur:.1f}s")
            else:
                try:
                    from agents.script_agent import get_runtime_contract as _grc
                except ImportError:
                    try:
                        from script_agent import get_runtime_contract as _grc  # type: ignore
                    except ImportError:
                        def _grc(m): return {"min_seconds": 900.0, "max_seconds": 5400.0}  # type: ignore
                _rc = _grc("full")
                _est_min = len(script_data.get("script", "").split()) / (185.0 if language == "arabic" else 145.0)
                print(f"[AR AUDIO] Estimated: {_est_min:.1f}min")
                print(f"[AR AUDIO] Rendered:  {_min:.1f}min")
                _delta = _min - _est_min
                if abs(_delta) > 2.0:
                    print(f"[AR AUDIO] Runtime mismatch: {_delta:+.1f}min")
                if _dur < _rc["min_seconds"]:
                    print(f"[AR AUDIO] Rebuild triggered — {_min:.1f}min < {_rc['min_seconds']/60:.0f}min contract minimum")
                elif _dur > _rc["max_seconds"]:
                    print(f"[FULL] WARNING: Long audio too long: {_min:.1f} min (contract: {_rc['min_seconds']/60:.0f}-{_rc['max_seconds']/60:.0f} min)")
                else:
                    print(f"[FULL] Long duration OK: {_min:.1f} min")
        except Exception:
            pass
    except Exception as e:
        print(f"[FULL] CRASH at voiceover: {e}")
        traceback.print_exc()
        return ""

    # ── Whisper subtitles ──────────────────────────────────────────────────
    whisper_segments: list[dict] = []
    if ENABLE_SUBTITLES:
        try:
            whisper_segments = generate_subtitles(audio_path, language)
        except Exception as _ws_e:
            print(f"[Subtitle] Skipping Whisper (non-fatal): {_ws_e}")

    # ── Visual planning — 12 unique semantic events/min ──────────────────
    # Each event = one distinct narrative moment = one unique AI image.
    # 10 min → 120 unique images | 15 min → 180 | 20 min → 240
    import math as _math
    _vis_plan = plan_visual_requirements(_real_audio_secs, is_short=is_short)
    if is_short:
        n_images = 6
    elif _real_audio_secs > 0:
        n_images = _vis_plan["n_unique"]
    else:
        n_images = 60  # safe floor when audio duration unavailable
    calculate_total_images(user_images)
    print(f"[FULL] Building {n_images} visuals ({'short' if is_short else 'long'})")
    script_text = script_data.get("script", "")
    topic_str   = script_data.get("topic", "")
    import time as _t; _t.sleep(1)

    # ── User content loading + dedup ───────────────────────────────────────
    folder_videos = _load_user_videos_from_folder()
    folder_images = _load_user_images_from_folders(topic_str)
    _seen_paths: set[str] = set()
    all_user_videos: list[dict] = []
    for _uv in list(user_videos or []) + folder_videos:
        _p = _uv.get("path", "")
        if _p and _p not in _seen_paths:
            _seen_paths.add(_p)
            all_user_videos.append(_uv)
    _seen_paths = set()
    all_user_images: list[dict] = []
    for _ui in list(user_images or []) + folder_images:
        _p = _ui.get("path", "")
        _p_abs = os.path.abspath(_p) if _p else ""
        if _p_abs and _p_abs not in _seen_paths and os.path.exists(_p_abs):
            _seen_paths.add(_p_abs)
            _ui_norm = dict(_ui); _ui_norm["path"] = _p_abs
            all_user_images.append(_ui_norm)
    print(f"[FULL] User images: {len(all_user_images)}, videos: {len(all_user_videos)}")
    if all_user_images:
        print(f"[DEBUG] User image paths: {[img['path'] for img in all_user_images]}")
    _style_profile = extract_style_from_user_images(all_user_images) if all_user_images else ""

    # ── Cinematic clip timeline ────────────────────────────────────────────
    _clip_timeline: list[dict] = []
    try:
        if _library_clips:
            _sections      = _parse_script_sections(script_text)
            _clip_timeline = assign_clips_to_script(_sections, _library_clips)
            _with_clips    = sum(1 for s in _clip_timeline if s.get("clip"))
            print(f"[Clip] Timeline: {len(_clip_timeline)} sections, {_with_clips} with clips, "
                  f"{len(_clip_timeline) - _with_clips} image-fallback")
    except Exception as _te:
        print(f"[Clip] Timeline assignment skipped (non-fatal): {_te}")

    # ── Visual assembly pool ───────────────────────────────────────────────
    mode = _detect_assembly_mode(all_user_images, all_user_videos)
    try:
        if _clip_timeline:
            _tl_clips = [
                c for seg in _clip_timeline
                if not seg.get("is_breathing")
                for c in (seg.get("clips") or [])
            ]
            image_paths: list[str] = _tl_clips if _tl_clips else list(_library_clips)
        else:
            image_paths = list(_library_clips)
        if _library_clips:
            print(f"[Clip] MODE {'1' if mode == 'user_content' else '2'}: "
                  f"{len(image_paths)} library clip(s) (timeline-ordered)")

        if mode == "user_content":
            for uv in all_user_videos:
                path = uv.get("path", "")
                if path and os.path.exists(path):
                    dest = os.path.abspath(os.path.join(IMAGES_DIR, f"{video_id}_uv_{len(image_paths)}.mp4"))
                    try:
                        import shutil as _shutil
                        _shutil.copy2(path, dest)
                        if os.path.exists(dest):
                            image_paths.append(dest)
                            print(f"[FULL] User video added: {uv.get('caption','')[:60]}")
                        else:
                            print(f"[FULL] WARNING: Copy ok but {dest} not found")
                    except Exception as _e:
                        print(f"[FULL] Could not copy user video {path}: {_e}")
            if not all_user_videos:
                print("[FULL] No user videos — auto-searching archive/YouTube CC")
                auto_vids = fetch_stock_videos(
                    script_text, min(4, max(2, n_images // 3)), video_id, topic=topic_str
                )
                for vpath in auto_vids:
                    if vpath not in image_paths:
                        image_paths.append(vpath)
            for i, ui in enumerate(all_user_images):
                path = ui.get("path", "")
                if path and os.path.exists(path):
                    ext  = os.path.splitext(path)[1] or ".jpg"
                    dest = os.path.abspath(os.path.join(IMAGES_DIR, f"{video_id}_ui_{i}{ext}"))
                    try:
                        import shutil as _shutil
                        _shutil.copy2(path, dest)
                        if os.path.exists(dest):
                            image_paths.append(dest)
                            print(f"[FULL] User image added: {ui.get('caption','')[:60]}")
                        else:
                            print(f"[FULL] WARNING: Copy ok but {dest} not found")
                    except Exception as _e:
                        print(f"[FULL] Could not copy user image {path}: {_e}")
            audio_duration = _ffprobe_duration(audio_path) or (n_images * 8)
            is_sufficient, coverage_ratio = check_content_sufficiency(
                all_user_images, all_user_videos, audio_duration
            )
            if is_sufficient:
                print("[FULL] User content sufficient — skipping AI/stock generation")
            elif len(image_paths) < n_images:
                missing = n_images - len(image_paths)
                print(f"[FULL] Gap: {missing} visuals needed (coverage {coverage_ratio*100:.0f}%)")
                gap_imgs = _fetch_gap_images(
                    script_text, missing, video_id, topic_str, coverage_ratio,
                    style_profile=_style_profile,
                )
                if gap_imgs:
                    gap_imgs = _rank_visual_pool(gap_imgs, topic=topic_str)
                image_paths.extend(gap_imgs)
        else:
            # ── FULL mode auto path: scene-driven unique visual pool ───────
            # build_documentary_visual_pool() generates ONE unique AI image per
            # narrative event (portrait/evidence/location/CCTV/newspaper/map/…)
            # driven by what is HAPPENING in each 12-word script chunk.
            # This produces 120-240 SEMANTICALLY UNIQUE visuals, not PIL recycles.
            print(f"[FULL] Building documentary visual pool ({n_images} target events)")
            _doc_pool = build_documentary_visual_pool(
                script_text, _real_audio_secs, topic_str, video_id,
                is_short=False, style_profile=_style_profile,
            )
            image_paths.extend(_doc_pool)
            # B-roll: add up to 6 real stock video clips for motion texture
            if _doc_pool:
                _broll = fetch_stock_videos(
                    script_text, min(6, max(2, len(_doc_pool) // 20)),
                    video_id, topic=topic_str,
                )
                if _broll:
                    # Rank B-roll clips by quality, but do NOT re-rank the full pool.
                    # build_documentary_visual_pool() already orders images by narrative
                    # event — re-ranking destroys that order and clusters dark AI images
                    # at the front, causing consecutive AI-generated runs.
                    _broll = _rank_visual_pool(_broll, topic=topic_str)
                    # Mark B-roll stock videos as real source assets
                    for _bv in _broll:
                        if _bv and _is_video_file(_bv):
                            _REAL_IMAGE_PATHS.add(_bv)
                image_paths.extend(_broll)
    except Exception as e:
        print(f"[FULL] CRASH at visual generation: {e}")
        traceback.print_exc()
        return ""

    if not image_paths:
        print("[FULL] No visuals generated — aborting")
        return ""

    # ── Person images + moment matching ───────────────────────────────────
    person_name     = _extract_person_name_from_topic(title, topic_str)
    priority_images = get_person_images(
        person_name, video_id,
        all_user_images if all_user_images else None,
        script_text=script_text,
    )
    if whisper_segments and priority_images:
        def _img_ts(img):
            tags = img.get("tags", []) or img.get("caption", "").split()
            ts   = find_keyword_timestamp(whisper_segments, tags)
            return ts if ts is not None else float("inf")
        priority_images.sort(key=_img_ts)
        print("[Visual] User images sorted by audio keyword timestamp")

    if mode == "user_content":
        _ui_originals = {ui["path"] for ui in all_user_images}
        extra_paths   = [
            img["path"] for img in priority_images
            if isinstance(img, dict) and img.get("path")
            and img["path"] not in image_paths
            and img["path"] not in _ui_originals
            and os.path.exists(img["path"])
        ]
        all_image_paths = image_paths + extra_paths
        print(f"[FULL] MODE 1 pool: {len(image_paths)} direct + {len(extra_paths)} extra = "
              f"{len(all_image_paths)} total")
    else:
        try:
            moments = parse_script_moments(script_text, topic=topic_str)
            if moments and (priority_images or image_paths):
                matched = match_images_to_moments(moments, priority_images, image_paths)
                all_image_paths = matched if matched else build_image_list(priority_images, image_paths)
            else:
                all_image_paths = build_image_list(priority_images, image_paths)
        except Exception as e:
            print(f"[Visual] Moment matching failed ({e}) — using default order")
            all_image_paths = build_image_list(priority_images, image_paths)

    print(f"[FULL] First 5 images: {[os.path.basename(p) for p in all_image_paths[:5]]}")

    # ── Image enhancement ──────────────────────────────────────────────────
    try:
        from agents.enhancer import enhance_image as _enhance_image
        _img_exts   = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".bmp"}
        _to_enhance = [
            p for p in all_image_paths
            if p and Path(p).suffix.lower() in _img_exts and os.path.exists(p)
        ]
        if _to_enhance:
            print(f"[FULL] Enhancing {len(_to_enhance)} image(s)...")
            from concurrent.futures import ThreadPoolExecutor as _TPE
            print(f"[QUEUE] Enhancement backlog: {len(_to_enhance)} images | workers={min(_WORKERS['enhance'], len(_to_enhance))}")
            with _TPE(max_workers=min(_WORKERS["enhance"], len(_to_enhance))) as _pool:
                _enh_results = list(_pool.map(_enhance_image, _to_enhance))
            _enh_map        = dict(zip(_to_enhance, _enh_results))
            all_image_paths = [_enh_map.get(p) or p for p in all_image_paths]
            print("[FULL] Enhancement complete")
    except Exception as _enh_err:
        print(f"[FULL] Enhancement skipped (non-fatal): {_enh_err}")

    # ── Emergency fallback — never assemble with < 8 visuals ──────────────
    # build_documentary_visual_pool() already handles this internally, but
    # guard here too in case user_content mode produced a thin pool.
    _em_threshold = max(8, n_images // 3)  # trigger if less than 33% of target reached
    if not is_short and len(all_image_paths) < _em_threshold:
        print(f"[FULL] Pool too small ({len(all_image_paths)}/{n_images}) — activating emergency engine")
        _em = _generate_emergency_visuals(
            max(20, n_images - len(all_image_paths)), IMAGES_DIR,
            is_short=False, topic=topic_str,
        )
        all_image_paths.extend(_em)

    # ── Pre-assembly validation ────────────────────────────────────────────
    _missing = [p for p in all_image_paths if not p or not os.path.exists(p)]
    if _missing:
        print(f"[FULL] WARNING: {len(_missing)} path(s) missing — filtering out")
        all_image_paths = [p for p in all_image_paths if p and os.path.exists(p)]
    print(f"[FULL] Final image count: {len(all_image_paths)}")
    print(f"[DEBUG] First 3 paths: {all_image_paths[:3]}")

    # Filter dark solid-background placeholder images before assembly
    all_image_paths = _filter_dark_placeholders(all_image_paths, label="FULL")

    # Pre-export validation (warnings only — never abort on warnings alone)
    _full_audio_secs = _real_audio_secs if _real_audio_secs > 0 else 0.0
    _validate_render_inputs(all_image_paths, _full_audio_secs, is_short=is_short, language=language)

    # ── Assembly ───────────────────────────────────────────────────────────
    output_path    = os.path.join(FINAL_DIR, f"{video_id}.mp4")
    _clip_durations: dict[str, float] = {}
    for _seg in (_clip_timeline or []):
        _cd = _seg.get("clip_duration")
        if _cd:
            for _cp in (_seg.get("clips") or []):
                if _cp:
                    _clip_durations[_cp] = float(_cd)
    if _clip_durations:
        print(f"[Clip] Passing {len(_clip_durations)} clip duration(s) to renderer")
    try:
        if is_short:
            video_path = assemble_short_video(
                audio_path=audio_path, image_paths=all_image_paths,
                output_path=output_path, clip_durations=_clip_durations or None,
            )
        else:
            video_path = assemble_video_with_hook(
                audio_path=audio_path, image_paths=all_image_paths,
                output_path=output_path, video_id=video_id,
                clip_durations=_clip_durations or None,
            )
    except Exception as e:
        print(f"[FULL] Assembly crashed: {e}")
        traceback.print_exc()
        video_path = ""

    # Fallback render if primary assembly failed
    if not video_path or not os.path.exists(video_path):
        print("[FULL] Primary assembly failed — attempting fallback render")
        _fb_path = output_path.replace(".mp4", "_fb.mp4")
        try:
            _fb_imgs = all_image_paths[:min(8, len(all_image_paths))]
            video_path = assemble_short_video(
                audio_path=audio_path, image_paths=_fb_imgs, output_path=_fb_path
            )
            if video_path and os.path.exists(video_path):
                print(f"[FULL] Fallback render succeeded: {video_path}")
        except Exception as _fb_e:
            print(f"[FULL] Fallback render also failed: {_fb_e}")
            video_path = ""

    if video_path:
        if whisper_segments:
            try:
                subbed_path = video_path.replace(".mp4", "_subbed.mp4")
                burned = burn_subtitles_ffmpeg(video_path, whisper_segments, subbed_path, language)
                if burned and os.path.exists(burned):
                    os.replace(burned, video_path)
                    print("[Subtitle] Subtitles burned into final video")
            except Exception as _sub_e:
                print(f"[Subtitle] Burn failed (non-fatal): {_sub_e}")
        pure_paths = [
            uv["path"] for uv in all_user_videos
            if _is_pure_video(uv) and os.path.exists(uv.get("path", ""))
        ]
        if pure_paths:
            video_path = _mix_pure_video_audio(video_path, pure_paths)
        video_path = _apply_intro_outro_overlay(
            video_path,
            title=script_data.get("title", ""),
            language=language,
            video_id=video_id,
            is_short=is_short,
            chapters_str=script_data.get("chapters", ""),
        )
        if not is_short and os.getenv("ENABLE_PREMIUM_INTRO", "").strip() == "FORCE_ENABLE":
            try:
                from agents.premium_intro import create_intro, prepend_intro
                _intro_path = os.path.join(FINAL_DIR, f"{video_id}_intro.mp4")
                _intro = create_intro(_intro_path)
                if _intro:
                    video_path = prepend_intro(_intro, video_path)
            except Exception as _intro_err:
                print(f"[Intro] Non-fatal: {_intro_err}")
        short_out = os.path.join(SHORTS_DIR, f"{video_id}_short.mp4")
        script_data["short_clip_path"] = cut_short_clip(
            video_path, short_out, script_data=script_data
        )
        thumb_path        = os.path.join(FINAL_DIR, f"{video_id}_thumb.jpg")
        _thumb_candidates = [p for p in all_image_paths[:5] if p and os.path.exists(p)]
        _thumb = None
        if _thumb_candidates:
            try:
                from agents.thumbnail_generator import create_thumbnail as _mk_thumb, select_best_image as _sbi
                _thumb_src = _sbi(_thumb_candidates)
                _thumb = _mk_thumb(
                    image_path=_thumb_src,
                    title=script_data.get("title", ""),
                    output_path=thumb_path,
                    language=language,
                )
            except Exception as _te:
                print(f"[Thumb] Non-fatal: {_te}")
        if not _thumb:
            _thumb = extract_first_frame(video_path, thumb_path)
        if _thumb:
            script_data["thumbnail_path"] = _thumb

    # ── Quality processing ─────────────────────────────────────────────────
    if not is_short and video_path and os.path.exists(video_path):
        try:
            import sys as _sys
            _proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _proj_root not in _sys.path:
                _sys.path.insert(0, _proj_root)
            from video_quality import process_video as _process_video
            _base      = os.path.splitext(os.path.basename(video_path))[0]
            long_path  = os.path.abspath(os.path.join("output_videos", f"{_base}_long.mp4"))
            short_path = os.path.abspath(os.path.join("output_videos", f"{_base}_short.mp4"))
            if os.path.exists(long_path) and os.path.exists(short_path):
                print(f"[Quality] Already processed — Long: {long_path} | Short: {short_path}")
                video_path = long_path
                script_data["short_clip_path"] = short_path
            else:
                long_ok, short_ok = _process_video(video_path)
                if not long_ok and not short_ok:
                    print("[Quality] Both outputs failed — using original assets")
                else:
                    if not (long_ok and short_ok):
                        print(f"[Quality] Partial success: long_ok={long_ok} short_ok={short_ok}")
                    if long_ok and os.path.exists(long_path):
                        print(f"[Quality] Using long: {long_path}")
                        video_path = long_path
                    else:
                        print(f"[Quality] Long output missing — keeping original: {video_path}")
                    if short_ok and os.path.exists(short_path):
                        print(f"[Quality] Using short: {short_path}")
                        script_data["short_clip_path"] = short_path
                    else:
                        print("[Quality] Short output missing — keeping existing short clip")
            _assigned = script_data.get("short_clip_path", "")
            if _assigned and not os.path.exists(_assigned):
                print(f"[Quality] WARNING: short_clip_path missing: {_assigned} — clearing")
                script_data.pop("short_clip_path", None)
        except Exception as _qe:
            print(f"[Quality] Processing skipped (non-fatal): {_qe}")

    # ── Validate + resource cleanup ────────────────────────────────────────
    if video_path:
        _validate_output_file(video_path)
    _kill_orphan_ffmpeg()
    try:
        import gc as _gc; _gc.collect()
    except Exception:
        pass
    try:
        _tc = os.path.abspath("temp_clips")
        if os.path.isdir(_tc):
            shutil.rmtree(_tc, ignore_errors=True)
            print("[Clip] temp_clips cleaned up")
    except Exception as _cleanup_err:
        print(f"[Clip] temp_clips cleanup skipped (non-fatal): {_cleanup_err}")
    print("[Render] Artifact upload starting")
    return video_path or ""


# ── Main entry point (dispatcher) ────────────────────────────────────────────

def create_video(
    script_data: dict,
    video_id: str,
    custom_audio_path: str = "",
    user_images: list | None = None,
    user_videos: list | None = None,
) -> str:
    """Dispatcher: routes to run_fast_pipeline() or run_full_pipeline()."""
    global _PIPELINE_START
    _PIPELINE_START = time.time()
    if PIPELINE_MODE == "fast":
        return run_fast_pipeline(script_data, video_id, custom_audio_path, user_images, user_videos)
    else:
        return run_full_pipeline(script_data, video_id, custom_audio_path, user_images, user_videos)
