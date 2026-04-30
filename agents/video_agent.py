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


def generate_voiceover_openai(text: str, language: str, output_path: str,
                              is_short: bool = False) -> str:
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
        voice = "alloy"
        speed = TTS_SPEED
        label = "Arabic"
    else:
        model = "tts-1"
        voice = "onyx"
        speed = TTS_SPEED
        label = "English"

    tts_instructions = None  # tts-1 does not support instructions param

    print(f"[Voice] TTS speed: {speed} ({label}) | model={model} voice={voice}")

    # ── Persistent hash cache ──────────────────────────────────────────────────
    _cache_key  = hashlib.sha256(
        f"{text}|{language}|{voice}|{model}".encode()
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
        chunks = _split_text(text, max_chars=4000)
        print(f"[Voice] OpenAI TTS: {len(chunks)} chunk(s)")

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


def _split_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split text preserving complete paragraphs; no content is ever dropped."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).lstrip("\n")
        else:
            if current:
                chunks.append(current.strip())
            # Paragraph itself too large — split on sentence boundaries
            if len(para) > max_chars:
                sentences = para.replace(". ", ".|").replace("! ", "!|").replace("? ", "?|").split("|")
                sub = ""
                for sent in sentences:
                    if len(sub) + len(sent) + 1 <= max_chars:
                        sub = (sub + " " + sent).lstrip()
                    else:
                        if sub:
                            chunks.append(sub.strip())
                        sub = sent
                current = sub
            else:
                current = para
    if current:
        chunks.append(current.strip())

    original_words = len(text.split())
    chunked_words = sum(len(c.split()) for c in chunks)
    print(f"[TTS] Chunks: {len(chunks)} | Original: {original_words} words | Chunked: {chunked_words} words")
    if chunked_words < original_words * 0.95:
        print("[TTS] âš ï¸ Content lost in chunking!")
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

    # Priority 1: OpenAI TTS
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key and not _OPENAI_QUOTA_EXCEEDED:
        print("[Voice] Trying OpenAI TTS...")
        _oai_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
        _is_short = "short" in filename.lower()
        result = generate_voiceover_openai(script_text, language, _oai_path, is_short=_is_short)
        if result:
            return result
        if _OPENAI_QUOTA_EXCEEDED:
            print("[TTS] OpenAI quota exceeded -> edge fallback")
        else:
            print("[Voice] OpenAI TTS failed — falling back to edge-tts")

    # Priority 2: edge-tts (backup — used only when OpenAI unavailable)
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
        if topic.lower() in data.get("topic", "").lower():
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
                images.append({
                    "path": downloaded,
                    "tags": ["real", "photo", "portrait", *person_name.lower().split()],
                    "caption": f"{person_name} real historical photo",
                })
                print(f"[Image] Priority 2 (Wikipedia): {downloaded}")

    return images




_IMAGE_PROMPT_SUFFIX = (
    ", dark cinematic documentary style, no text, "
    "no watermarks, photorealistic, high detail"
)


def build_image_prompt(chunk_text: str) -> str:
    """Groq-first image prompt generation from a script chunk; OpenAI fallback."""
    first_200 = " ".join(chunk_text.split()[:200])

    prompt = (
        "Read this script excerpt and write a specific visual image generation prompt "
        "(max 20 words) that represents the exact subject being described.\n\n"
        "Rules:\n- Name real places, real objects, real events\n- No human faces\n"
        "- Dark cinematic documentary style\n- Be specific not generic\n\n"
        "Examples:\n"
        "GOOD: 'Burned village Darfur Sudan desert, smoke ruins, golden hour, cinematic aerial view'\n"
        "BAD: 'dark crime documentary background'\n\n"
        f"Script excerpt: {first_200}\n\nReturn only the image prompt, nothing else."
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
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60, temperature=0.7,
            ).choices[0].message.content.strip().strip('"\'')
            if result:
                print(f"[Image] Chunk prompt (Groq): {result[:70]}")
                return f"{result}{_IMAGE_PROMPT_SUFFIX}"
        except Exception as e:
            print(f"[Image] Groq image prompt failed: {e}")

    # OpenAI fallback
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 60, "temperature": 0.7},
                timeout=30,
            )
            if r.status_code == 200:
                result = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                print(f"[Image] Chunk prompt (OpenAI): {result[:70]}")
                return f"{result}{_IMAGE_PROMPT_SUFFIX}"
        except Exception as e:
            print(f"[Image] build_image_prompt OpenAI failed: {e}")

    return f"true crime historical documentary scene cinematic dark{_IMAGE_PROMPT_SUFFIX}"


SCENE_PROMPTS: dict[str, list[str]] = {
    "hemedti": [
        "Aerial cinematic shot of Darfur desert Sudan, burned villages smoke rising, documentary realism, vertical 9:16",
        "Sudanese military commander portrait, RSF uniform, dark dramatic lighting, cinematic vertical 9:16",
        "Chad Sudan border region landscape, camel traders, desert market 1980s, cinematic documentary",
        "Gold mine illegal operation in African desert, armed guards, aerial view cinematic vertical",
        "Khartoum city Sudan aerial view, military presence, dramatic documentary style vertical 9:16",
        "International Criminal Court ICC building Den Haag, dramatic lighting documentary vertical 9:16",
        "Darfur genocide memorial, survivors, dramatic documentary style vertical 9:16 cinematic",
        "UAE Dubai skyline night, gold trading deal, cinematic documentary vertical 9:16",
        "Colombian mercenaries military training, documentary style dramatic vertical 9:16",
        "Sudan civil war 2023, destroyed buildings, documentary realism cinematic vertical 9:16",
        "Janjaweed militia horseback Sudan desert, historical dramatic cinematic vertical 9:16",
        "African Union UN peacekeepers Darfur, documentary cinematic vertical 9:16",
    ],
}


def get_scene_prompts(topic: str, research: dict) -> list[str] | None:
    """Return hardcoded scene prompts for known topics, or None for generic handling."""
    topic_lower = topic.lower()
    for key, prompts in SCENE_PROMPTS.items():
        if key in topic_lower:
            return prompts
    return None


def generate_image_prompts(script_text: str, count: int, topic: str = "", research: dict | None = None) -> list[str]:
    """Split script into [count] equal chunks, call OpenAI once per chunk.
    Returns list of [count] specific image prompts.
    Falls back gracefully per chunk if OpenAI call fails.
    """
    import re

    # Use hardcoded scene prompts for known topics
    if topic:
        scene = get_scene_prompts(topic, research or {})
        if scene:
            result = (scene * ((count // len(scene)) + 1))[:count]
            print(f"[Image] Using {len(result)} scene-based prompts for topic: {topic}")
            return result

    # Strip [SECTION: ...] markers so they don't pollute chunk text
    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()

    if not words:
        return [f"true crime historical documentary scene cinematic dark{_IMAGE_PROMPT_SUFFIX}"] * count

    chunk_size = max(1, len(words) // count)
    prompts: list[str] = []
    for i in range(count):
        start      = i * chunk_size
        end        = start + chunk_size if i < count - 1 else len(words)
        chunk_text = " ".join(words[start:end])
        prompts.append(build_image_prompt(chunk_text))
        if i < count - 1:
            time.sleep(1)

    print(f"[Image] Built {len(prompts)} chunk-specific prompts from script")
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


def generate_ai_image(prompt: str, output_path: str, seed: int = None) -> str:
    """Fetch an AI-generated image from Pollinations with retry + dark fallback."""
    import io
    from PIL import Image as PILImage

    output_path = output_path.replace(".jpg", ".png")
    encoded = requests.utils.quote(clean_prompt(prompt))
    _seed = seed if seed is not None else random.randint(1, 99999)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1920&nologo=true&seed={_seed}"
    )

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=120)
            if response.status_code == 200:
                img = PILImage.open(io.BytesIO(response.content)).convert("RGB")
                img = img.resize((1080, 1920), PILImage.LANCZOS)
                img.save(output_path, "PNG")
                print(f"[Image] Generated: {prompt[:60]}")
                time.sleep(5)
                return output_path
            elif response.status_code == 429:
                print(f"[Image] Rate limited, waiting 30s... (attempt {attempt + 1}/3)")
                time.sleep(30)
            else:
                print(f"[Image] Pollinations returned {response.status_code} (attempt {attempt + 1}/3)")
                time.sleep(10)
        except Exception as e:
            print(f"[Image] Attempt {attempt + 1} failed: {e}")
            time.sleep(15)

    # Fallback: solid dark background so assembly never crashes
    img = PILImage.new("RGB", (1080, 1920), color=(13, 13, 26))
    img.save(output_path, "PNG")
    print(f"[Image] Using dark background fallback for: {prompt[:60]}")
    return output_path


# â"€â"€ Real-photo fetching â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_IMAGE_MAGIC = {
    b"\xff\xd8\xff":         "jpeg",
    b"\x89\x50\x4e\x47":    "png",
    b"\x52\x49\x46\x46":    "webp",
    b"\x47\x49\x46\x38":    "gif",
}
_IMAGE_MIN_BYTES = 15_000   # 15 KB — reject placeholder/error images
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
    # Must end in a known image extension OR contain one in the path
    from urllib.parse import urlparse, unquote
    path = unquote(urlparse(url).path).lower()
    return any(path.endswith(ext) for ext in _VALID_IMAGE_EXTS)


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
        r = requests.get('https://commons.wikimedia.org/w/api.php', params=params, timeout=15)
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
    except Exception as e:
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
            timeout=30
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

    except Exception as e:
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
    "john douglas", "robert ressler", "ann burgess", "edmund kemper",
    "charles manson", "david berkowitz", "ted bundy", "jeffrey dahmer",
    "pablo escobar", "el chapo", "griselda blanco", "frank lucas",
    "henry hill", "al capone", "lucky luciano", "whitey bulger",
    "richard ramirez", "john wayne gacy", "btk", "dennis rader",
    "henry lee lucas", "aileen wuornos",
}


def _search_wikimedia_person_photo(person_name: str) -> str | None:
    """Fetch Wikipedia thumbnail for a real person via two endpoints."""
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
    """Groq-based fallback query generator when OpenAI is unavailable."""
    try:
        from agents.script_agent import _groq_call
    except ImportError:
        try:
            from script_agent import _groq_call
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
        if 2 <= len(result.split()) <= 8:
            return result
    except Exception as e:
        print(f"[Stock] Groq query failed: {e}")
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

    if _groq_call:
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
            print(f"[Stock] Groq keyword extraction failed: {e}")

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
        slug = topic.lower().replace(" ", "_").replace("-", "_")[:30]
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


def find_content_folder(topic: str) -> str | None:
    """Return path to content/<folder> matching this topic, or None."""
    topic_lower = topic.lower()
    folder_map = {
        'mindhunter':        'mindhunter',
        'al capone':         'al_capone',
        'capone':            'al_capone',
        'pablo escobar':     'pablo_escobar',
        'escobar':           'pablo_escobar',
        'narcos':            'pablo_escobar',
        'frank lucas':       'frank_lucas',
        'american gangster': 'frank_lucas',
        'charles manson':    'charles_manson',
        'manson':            'charles_manson',
        'ed kemper':         'ed_kemper',
        'kemper':            'ed_kemper',
        'dahmer':            'dahmer',
        'jeffrey dahmer':    'dahmer',
        'ted bundy':         'ted_bundy',
        'bundy':             'ted_bundy',
        'griselda':          'griselda',
        'scarface':          'scarface',
        'godfather':         'godfather',
        'goodfellas':        'goodfellas',
    }
    for keyword, folder in folder_map.items():
        if keyword in topic_lower:
            return f'content/{folder}'
    first_word = topic_lower.split()[0] if topic_lower.split() else ''
    if first_word and os.path.exists(f'content/{first_word}'):
        return f'content/{first_word}'
    return None


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
        if size < 10000:
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

    topic_folder  = find_content_folder(topic)
    shared_folder = 'content/_shared'

    images: list[str]       = []
    videos: list[dict]      = []
    music_long: str | None  = None
    music_short: str | None = None

    # Topic-specific
    if topic_folder and os.path.exists(topic_folder):
        images += _scan_paths(f'{topic_folder}/images', _img_exts)
        videos += _scan_and_validate_videos(f'{topic_folder}/videos')
        long_p  = f'{topic_folder}/music/documentary_long.mp3'
        short_p = f'{topic_folder}/music/documentary_short.mp3'
        if os.path.exists(long_p):
            music_long = long_p
        if os.path.exists(short_p):
            music_short = short_p

    # Shared supplement
    if os.path.exists(shared_folder):
        images += _scan_paths(f'{shared_folder}/images', _img_exts)
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
            "[orig][narr]amix=inputs=2:duration=shortest[aout]",
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
    norm_w, norm_h = ("1080:1920" if is_short else "1920:1080")
    filters = [f"scale={norm_w}:{norm_h},format=yuv420p"]

    if is_short:
        # Shorts: persistent top bar (channel name) + bottom bar (CTA)
        bar_h = 80
        filters += [
            # Top bar
            f"drawbox=x=0:y=0:w={w}:h={bar_h}:color=black@0.75:t=fill",
            f"drawtext=fontfile='{font_esc}':text='{channel_name}':fontsize=32:fontcolor=white"
            f":x=(w-text_w)/2:y={bar_h//2 - 16}",
            # Bottom bar
            f"drawbox=x=0:y=ih-{bar_h}:w={w}:h={bar_h}:color=black@0.75:t=fill",
            f"drawtext=fontfile='{font_esc}':text='{cta_text}':fontsize=28:fontcolor=white"
            f":x=(w-text_w)/2:y=ih-{bar_h//2 + 14}",
        ]
    else:
        hook_end    = 2.5
        outro_start = 9999.0
        crimson     = "0xDC143C"

        _hook_raw = hook_text or title or "Dark Crime Decoded"
        _hook_raw = (_hook_raw[:42] + "…") if len(_hook_raw) > 42 else _hook_raw
        hook_esc  = _escape_drawtext(_hook_raw)

        # Cold open: soft glitch pulse at t=0
        filters += [
            f"drawbox=x=0:y=0:w={w}:h={h}:color=white@0.20:t=fill:enable='lt(t,0.08)'",
        ]

        # Hook: slim scrim + text at bottom (0–2.5 s)
        filters += [
            # Thin scrim — non-blocking, just enough for legibility
            f"drawbox=x=0:y=ih-110:w={w}:h=110:color=black@0.42:t=fill"
            f":enable='between(t,0,{hook_end})'",
            # Crimson accent line above scrim
            f"drawbox=x=0:y=ih-113:w={w}:h=3:color={crimson}@1.0:t=fill"
            f":enable='between(t,0,{hook_end})'",
            # Hook text with shadow for depth
            f"drawtext=fontfile='{font_esc}':text='{hook_esc}':fontsize=44:fontcolor=white"
            f":shadowcolor=black@0.80:shadowx=2:shadowy=2"
            f":x=(w-text_w)/2:y=ih-78"
            f":alpha='if(lt(t,0.25),t/0.25,1)'"
            f":enable='between(t,0,{hook_end})'",
            # Brand watermark — small, crimson, bottom edge
            f"drawtext=fontfile='{font_esc}':text='{channel_name}':fontsize=18:fontcolor={crimson}"
            f":shadowcolor=black@0.80:shadowx=1:shadowy=1"
            f":x=(w-text_w)/2:y=ih-24"
            f":alpha='if(lt(t,0.25),t/0.25,1)'"
            f":enable='between(t,0,{hook_end})'",
        ]

        # Chapter transition flashes (0.5s white flash text overlay)
        chapters = _parse_chapter_timestamps(chapters_str)
        for ch_time, ch_label in chapters:
            if ch_time < 6.0:
                continue
            ch_esc = _escape_drawtext(ch_label[:40])
            flash_end = ch_time + 0.5
            filters += [
                f"drawbox=x=0:y=0:w={w}:h={h}:color=white@0.25:t=fill"
                f":enable='between(t,{ch_time},{flash_end})'",
                f"drawtext=fontfile='{font_esc}':text='{ch_esc}':fontsize=52:fontcolor=white"
                f":x=(w-text_w)/2:y=(h/2 - 30)"
                f":enable='between(t,{ch_time},{flash_end})'",
            ]

        # Outro: fade to black at last 4.5s + CTA text
        # Use ffprobe to get duration, fall back to expression
        try:
            _dur = _ffprobe_duration(video_path) or 0.0
            if _dur > 10:
                outro_start = _dur - 4.5
            else:
                outro_start = 9999.0
        except Exception:
            outro_start = 9999.0

        if outro_start < 9999.0:
            filters += [
                f"fade=t=out:st={outro_start}:d=4.5",
                f"drawbox=x=0:y=0:w={w}:h={h}:color=black@1.0:t=fill"
                f":enable='gte(t,{outro_start + 3.5})'",
                f"drawtext=fontfile='{font_esc}':text='{channel_name}':fontsize=56:fontcolor=white"
                f":x=(w-text_w)/2:y=(h/2 - 60)"
                f":enable='gte(t,{outro_start + 3.5})'",
                f"drawtext=fontfile='{font_esc}':text='{cta_text}':fontsize=36:fontcolor=yellow"
                f":x=(w-text_w)/2:y=(h/2 + 20)"
                f":enable='gte(t,{outro_start + 3.5})'",
            ]

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


def _fetch_gap_images(
    script_text: str,
    needed: int,
    video_id: str,
    topic: str,
    coverage_ratio: float,
) -> list[str]:
    """Fill a visual gap with priority: Wikimedia → OpenAI search → Pollinations AI.

    Archive and YouTube CC are never used for gap-fill — only clean image sources.
    """
    if needed <= 0:
        return []

    results: list[str] = []

    # Priority 1: Wikimedia person photos + Commons
    wiki_imgs = fetch_real_images(script_text, min(needed, 8), video_id, topic=topic)
    results.extend(wiki_imgs)
    if len(results) >= needed:
        return results[:needed]

    # Priority 2: OpenAI web search for real photos
    remaining = needed - len(results)
    if remaining > 0:
        ai_imgs = _fetch_openai_images_for_gap(topic, remaining, video_id)
        results.extend(ai_imgs)
    if len(results) >= needed:
        return results[:needed]

    # Priority 3: Pollinations AI generation (last resort)
    remaining = needed - len(results)
    if remaining > 0:
        print(f"[Video] Gap-fill last resort: generating {remaining} Pollinations AI images")
        for i in range(remaining):
            prompt = f"{topic} cinematic documentary dark dramatic portrait"
            out = os.path.join(IMAGES_DIR, f"{video_id}_gap_{i}.png")
            result = generate_ai_image(prompt, out)
            if result and os.path.exists(result):
                results.append(result)

    print(f"[Video] Gap-fill complete: {len(results)}/{needed} images (Wikimedia + OpenAI + Pollinations)")
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


def _download_video_url(url: str, output_path: str,
                        max_bytes: int | None = None) -> str | None:
    """Download one stock video URL with Content-Type + size validation."""
    limit = max_bytes or _VIDEO_MAX_BYTES
    try:
        # Check Content-Type via HEAD before downloading the full file
        ct = ""
        try:
            head = requests.head(url, timeout=8, headers={"User-Agent": "DarkCrimeDecoded/1.0"}, allow_redirects=True)
            ct = head.headers.get("Content-Type", "").lower()
            content_length = int(head.headers.get("Content-Length", 0) or 0)
            if content_length > limit:
                print(f"[Stock] Skipping oversized video ({content_length // 1_000_000} MB): {url[:60]}")
                return None
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
                        print(f"[Stock] Aborted oversized download (>{limit // 1_000_000} MB): {url[:60]}")
                        break

        size = os.path.getsize(output_path)
        if size < _VIDEO_MIN_BYTES:
            try:
                os.remove(output_path)
            except OSError:
                pass
            return None
        return output_path
    except Exception:
        return None


_SOURCE_BLACKLIST = {"agc", "chronicle", "reaction", "review", "compilation"}


def _is_blacklisted_source(url_or_title: str) -> bool:
    """Return True if the URL or title belongs to a channel/type we want to skip."""
    text = (url_or_title or "").lower()
    return any(kw in text for kw in _SOURCE_BLACKLIST)


def _validate_clip(path: str) -> bool:
    """Return True if path is a valid video file with duration 3-60 s."""
    if not path or not os.path.exists(path):
        return False
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout:
            import json as _json
            data = _json.loads(result.stdout)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    dur = float(stream.get("duration", 0) or 0)
                    return 3.0 <= dur <= 60.0
    except Exception:
        pass
    # MoviePy fallback
    try:
        try:
            from moviepy.editor import VideoFileClip as _VFC
        except ImportError:
            from moviepy import VideoFileClip as _VFC
        with _VFC(path) as c:
            return 3.0 <= c.duration <= 60.0
    except Exception:
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
        # ffprobe duration check
        dur = _ffprobe_duration(saved)
        if dur < 2.0:
            print(f"[Stock] Rejected invalid video (ffprobe duration={dur:.1f}s): {url[:60]}")
            try:
                os.remove(saved)
            except OSError:
                pass
            continue
        if not _validate_clip(saved):
            print(f"[Stock] Clip failed validation (duration out of 3-60s range): {url[:60]}")
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
    """Generate stock-video-friendly B-roll query from script chunk. Groq primary → OpenAI fallback."""
    # Groq first (free tier)
    result = _groq_query_for_chunk(chunk_text, topic=topic, for_video=True)
    if result:
        return result

    # OpenAI fallback
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
    if api_key:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 20, "temperature": 0.2},
                timeout=20,
            )
            if r.status_code == 200:
                q = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if 2 <= len(q.split()) <= 8:
                    return q
        except Exception as e:
            print(f"[Stock] OpenAI video query failed: {e}")
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
            ("YouTube CC",       _search_youtube_cc,       True,  None),
            ("Pexels",           _search_pexels_videos,    False, _VIDEO_MAX_BYTES),
            ("Pixabay",          _search_pixabay_videos,   False, _VIDEO_MAX_BYTES),
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
    """DuckDuckGo then Google image search. Returns saved path or None."""
    urls = _ddgs_image_results(query)
    if not urls:
        urls = _google_image_results(query)
    if not urls:
        print(f"[Image] No real photo found for '{query}'")
        return None
    saved = _download_first_valid(urls, output_path)
    if saved:
        print(f"[Image] Real photo: '{query}'")
        return saved
    print(f"[Image] No real photo found for '{query}'")
    return None


def _get_search_query_for_chunk(chunk_text: str) -> str | None:
    """
    Get a specific English image search query for a script chunk.
    Always English even if chunk is Arabic. Groq primary → OpenAI fallback.
    """
    # Groq first (free tier)
    result = _groq_query_for_chunk(chunk_text, for_video=False)
    if result:
        return result

    # OpenAI fallback
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
    if api_key:
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini",
                      "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 20, "temperature": 0.3},
                timeout=20,
            )
            if r.status_code == 200:
                q = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
                if len(q.split()) <= 8 and len(q) > 3:
                    return q
        except Exception as e:
            print(f"[Image] OpenAI search query failed: {e}")
    return None


def fetch_real_images(script_text: str, count: int, video_id: str,
                      topic: str = "") -> list[str]:
    """
    Universal image builder — works for any script topic.

    Priority order:
      1. User images from Telegram (always first)
      2. Wikimedia person photo (if person detected in chunk)
      3. Wikimedia Commons general search
      4. Pollinations AI generation with topic-specific prompt (last resort)

    Pexels is never used (returns irrelevant content for specific queries).

    Logs each image as real photo or AI generated.
    Returns list of image paths.
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

    if not words:
        paths = []
        for i in range(count):
            p = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.png")
            r = generate_ai_image(fallback_base, p, seed=seed + i)
            if r:
                paths.append(r)
        return paths

    # Priority 0: user-provided images from standard asset folders
    user_folder_images = _load_user_images_from_folders(topic)
    preloaded_paths: list[str] = []
    for uimg in user_folder_images:
        dest = os.path.join(IMAGES_DIR, f"{video_id}_user_{len(preloaded_paths)}.png")
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
    ai_prompts = generate_image_prompts(script_text, remaining)

    # Split script into equal word-chunks for remaining images
    chunk_size = max(1, len(words) // remaining)
    chunks = [
        " ".join(words[i * chunk_size: (i + 1) * chunk_size if i < remaining - 1 else len(words)])
        for i in range(remaining)
    ]

    image_paths:  list[str]      = list(preloaded_paths)
    query_cache:  dict[str, str] = {}
    real_count    = len(preloaded_paths)
    ai_count      = 0

    for i, chunk in enumerate(chunks):
        img_path = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.png")
        saved    = None

        # Step 1: person photo — runs on raw chunk, highest priority, before query gate
        person = _detect_person_in_chunk(chunk)
        if person:
            photo_url = _search_wikimedia_person_photo(person)
            if photo_url:
                saved = _download_first_valid([photo_url], img_path)
                if saved:
                    print(f"[Image] Wikimedia person photo: '{person}'")
                    real_count += 1

        # Step 2: Wikimedia Commons general search + OpenAI web search
        if not saved:
            query = _get_search_query_for_chunk(chunk)
            if query:
                if query in query_cache:
                    shutil.copy2(query_cache[query], img_path)
                    saved = img_path
                    print(f"[Image] Reused '{query}' for chunk {i}")
                else:
                    # Step 2a: Wikimedia Commons (mime-filtered, broader results)
                    wiki_urls = _search_wikimedia_commons(query)
                    if not wiki_urls:
                        wiki_urls = _wikimedia_image_results(query)
                    if wiki_urls:
                        saved = _download_first_valid(wiki_urls, img_path)
                        if saved:
                            print(f"[Image] Real photo (Wikimedia): '{query}'")
                            query_cache[query] = saved
                            real_count += 1

                    # Step 2b: OpenAI web search
                    if not saved:
                        oai_urls = _search_images_openai(query)
                        if oai_urls:
                            saved = _download_first_valid(oai_urls, img_path)
                            if saved:
                                print(f"[Image] Real photo (OpenAI search): '{query}'")
                                query_cache[query] = saved
                                real_count += 1

        # Step 3: AI fallback — Pollinations with topic-specific prompt
        if not saved:
            print(f"[Image] chunk {i}: no real image found, using AI generation")
            ai_prompt = ai_prompts[i] if i < len(ai_prompts) else fallback_base
            saved = generate_ai_image(ai_prompt, img_path, seed=seed + i)
            if saved:
                ai_count += 1

        if saved:
            image_paths.append(saved)

        if i < remaining - 1:
            time.sleep(2)

    print(f"[Image] Images: {real_count}/{count} real/user | {ai_count}/{count} AI generated")
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
    # Removed -profile:v baseline and -level 3.0: these can conflict with
    # libx264 on Ubuntu (GitHub Actions runner) and cause encoder init failures.
    try:
        final.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            ffmpeg_params=[
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
            ],
            temp_audiofile=temp_audio,
            logger=None,
        )
    except Exception as e:
        print(f"[Video] CRASH at write_videofile: {e}")
        traceback.print_exc()
        return ""
    finally:
        for _ in range(5):
            try:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                break
            except OSError:
                time.sleep(0.5)

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

        # Escape text for ffmpeg drawtext
        clean_title = _re.sub(r'[^\w\s\-]', '', chapter_title)[:50]
        clean_title = clean_title.replace("'", "\\'")

        cmd = [
            ffmpeg_bin, "-y",
            "-i", long_video_path,
            "-ss", str(int(cut_start)),
            "-t",  str(int(cut_dur)),
            "-vf",
            (
                f"drawtext=text='{clean_title}':"
                "fontsize=40:fontcolor=white:"
                "x=(w-text_w)/2:y=50:"
                "box=1:boxcolor=black@0.5:boxborderw=10"
            ),
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

    out_path    = os.path.join(output_dir, f"{safe_id}_best_{lang}.mp4")
    clean_title = _re.sub(r'[^\w\s\-]', '', chapter_title)[:50].replace("'", "\'")

    cmd = [
        ffmpeg_bin, "-y",
        "-i", long_video_path,
        "-ss", str(int(cut_start)),
        "-t",  str(int(cut_dur)),
        "-vf",
        (
            f"drawtext=text='{clean_title}':"
            "fontsize=40:fontcolor=white:"
            "x=(w-text_w)/2:y=50:"
            "box=1:boxcolor=black@0.5:boxborderw=10"
        ),
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
    Contextually assign an image to each script moment.

    Rules:
    - User images matched to their best-fit moment by tag overlap
    - FORBIDDEN: law-enforcement-tagged image on a pure crime/killer moment
    - Image rotation window: no repeat within last 5 placements
    - Alternates user/stock where possible
    - Logs every decision with reason
    """
    if not moments:
        all_paths = [img.get("path", "") if isinstance(img, dict) else img
                     for img in (user_images or []) + (ai_image_paths or [])]
        return [p for p in all_paths if p and os.path.exists(p)]

    user_pool = [img for img in (user_images or [])
                 if isinstance(img, dict) and img.get("path") and os.path.exists(img["path"])]
    ai_pool   = [p for p in (ai_image_paths or []) if p and os.path.exists(p)]

    print(f"[Visual] match_images_to_moments: {len(user_pool)} user images, {len(ai_pool)} stock/AI images, {len(moments)} moments")

    def _score(img_tags: list[str], m_tags: list[str]) -> int:
        img_lower = {t.lower() for t in img_tags}
        return sum(1 for t in m_tags if t.lower() in img_lower)

    def _is_forbidden(img_tags: list[str], moment: dict) -> bool:
        img_lower = {t.lower() for t in img_tags}
        has_law = any(t in img_lower for t in {"fbi", "police", "detective", "law_enforcement", "dea", "cop", "officer"})
        cats = moment.get("categories", [])
        return has_law and "crime" in cats and "law_enforcement" not in cats

    result: list[str] = []
    ai_idx = 0

    # PHASE 1: Fill ALL user images into their best-matching moments first.
    # User images are NEVER interleaved with stock — they fill first N slots.
    remaining_user = list(user_pool)
    user_slots = min(len(remaining_user), len(moments))

    for m_idx in range(user_slots):
        moment = moments[m_idx]
        best_score = -1
        best_img = None
        for img in remaining_user:
            img_tags = img.get("tags", [])
            if _is_forbidden(img_tags, moment):
                continue
            score = _score(img_tags, moment.get("tags", []))
            if score > best_score:
                best_score = score
                best_img = img
        if not best_img and remaining_user:
            best_img = remaining_user[0]
            best_score = 0
        if best_img:
            remaining_user.remove(best_img)
            preview = moment["text"][:50].replace("\n", " ")
            print(f"[Visual] Slot {m_idx}: '{preview}...' → {os.path.basename(best_img['path'])} [user_image score={best_score}]")
            result.append(best_img["path"])

    # PHASE 2: Fill remaining moment slots with stock/AI images
    for m_idx in range(user_slots, len(moments)):
        if ai_pool:
            chosen = ai_pool[ai_idx % len(ai_pool)]
            ai_idx += 1
            preview = moments[m_idx]["text"][:50].replace("\n", " ")
            print(f"[Visual] Slot {m_idx}: '{preview}...' → {os.path.basename(chosen)} [stock/AI]")
            result.append(chosen)

    # Pad if still short
    if ai_pool:
        while len(result) < len(moments):
            result.append(ai_pool[ai_idx % len(ai_pool)])
            ai_idx += 1

    print(f"[Visual] Final slot assignment: {len([r for r in result if 'user_' in r or 'transformed' in r])} user, {len(result)} total")
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


# â"€â"€ Hook-aware assembly (long videos only) â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def assemble_video_with_hook(
    audio_path: str,
    image_paths: list[str],
    output_path: str,
    video_id: str,
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

    def _media_clip(src_path: str, dur: float, zoom_in: bool = True, first_clip: bool = False):
        fi = 0.0 if first_clip else 0.2   # no fade-in on opening shot
        if _is_video_file(src_path):
            v = VideoFileClip(src_path)
            if v.duration <= 0:
                v.close()
                frame = _load_frame(src_path)
                return _zoom_clip(frame, dur, 1.00, 1.06 if zoom_in else 1.00, fade_in=fi)
            max_start = max(0.0, v.duration - dur)
            start = random.uniform(0, max_start) if max_start > 0 else 0.0
            c = v.subclip(start, min(v.duration, start + dur))
            c = _fit_vertical(c)
            if c.duration < dur:
                c = c.set_duration(dur)
            return c
        frame = _load_frame(src_path)
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

    # â"€â"€ MAIN CONTENT (1:30 to end): slow cuts every 8-12 s â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # Each image gets a zoom-in clip + zoom-out clip; then shuffled
    main_clips = []
    for img_path in image_paths:
        try:
            dur1  = random.uniform(6, 8)
            main_clips.append(_media_clip(img_path, dur1, zoom_in=True))
            dur2  = random.uniform(6, 8)
            main_clips.append(_media_clip(img_path, dur2, zoom_in=False))
        except Exception as e:
            print(f"[Video] Main clip error: {e}")

    random.shuffle(main_clips)

    # Loop main clips until they cover main_duration + buffer
    while sum(c.duration for c in main_clips) < main_duration + 20:
        src = image_paths[random.randint(0, len(image_paths) - 1)]
        dur = random.uniform(6, 8)
        try:
            main_clips.append(_media_clip(src, dur, zoom_in=True))
        except Exception:
            pass

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
        final = concatenate_videoclips(all_clips, method="chain")
        if final.duration > total_duration:
            final = final.subclip(0, total_duration)
        final = final.set_audio(audio)

        final.write_videofile(
            output_path,
            fps=24,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            ffmpeg_params=[
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
            ],
            temp_audiofile=temp_audio,
            remove_temp=True,
            logger=None,
        )
    except Exception as e:
        print(f"[Video] CRASH assembling hook video: {e}")
        traceback.print_exc()
        return ""
    finally:
        for _ in range(5):
            try:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                break
            except OSError:
                time.sleep(0.5)

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


def calculate_total_images(user_images=None) -> int:
    """Return 12 AI + however many user images were sent."""
    ai_images  = 12
    user_count = len(user_images) if user_images else 0
    total      = ai_images + user_count
    print(f"[Video] Images: {ai_images} AI + {user_count} user = {total} total")
    return total


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

    +3  video clip          +2  dark/cinematic tones (brightness < 100)
    +1  image file          -2  overexposed (brightness > 200)
    +3  face detected       +2  query/topic keyword in filename
    -5  irrelevant category (animals, nature, fashion…)
    """
    score = 0.0
    is_video = _is_video_file(path)
    score += 3.0 if is_video else 1.0

    base = os.path.basename(path).lower()

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

def assemble_short_video(audio_path: str, image_paths: list[str], output_path: str) -> str:
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
            v = VideoFileClip(src_path)
            if v.duration <= 0:
                v.close()
                return _zoom_clip(_load_frame(src_path), 1.00, 1.08 if zoom_in else 1.00, dur)
            actual_dur = min(dur, v.duration)  # don't pad beyond natural duration
            max_start  = max(0.0, v.duration - actual_dur)
            start      = random.uniform(0, max_start) if max_start > 0 else 0.0
            c = v.subclip(start, min(v.duration, start + actual_dur))
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
        final = concatenate_videoclips(final_clips, method="chain")
        # Trim video to EXACT audio duration — prevents silence at end
        exact_duration = audio.duration
        if final.duration > exact_duration:
            final = final.subclip(0, exact_duration)
        final = final.set_audio(audio)
        print(f"[Video] Final duration: {final.duration:.1f}s  Audio: {audio.duration:.1f}s")
        final.write_videofile(
            output_path,
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="ultrafast",
            ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            temp_audiofile=temp_audio,
            remove_temp=True,
            logger=None,
        )
    except Exception as e:
        print(f"[Video] CRASH assembling short video: {e}")
        traceback.print_exc()
        return ""
    finally:
        for _ in range(5):
            try:
                if os.path.exists(temp_audio):
                    os.remove(temp_audio)
                break
            except OSError:
                time.sleep(0.5)

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
        fallback_seconds = 90 if "short" in os.path.basename(path).lower() else 660
        print(f"[Music] Generating {fallback_seconds}s brown-noise ambient track: {path}")
        if not _create_ambient_music_fallback(path, fallback_seconds):
            print(f"[Music] Could not generate ambient track for {path} -- voice-only mode")
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
             "-filter_complex", "[1]volume=0.06[bg];[0][bg]amix=inputs=2:duration=first",
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

def process_audio_netflix(input_path: str) -> str:
    """
    Apply a 5-step ffmpeg chain for cinematic audio quality.
    Returns the processed file path (replaces input in-place).
    Skips silently if ffmpeg is unavailable.
    """
    import subprocess
    import shutil

    ffmpeg_bin = _get_ffmpeg()
    if not ffmpeg_bin:
        print("[Audio] ffmpeg not found — skipping Netflix processing")
        return input_path

    base   = input_path.replace(".mp3", "")
    steps  = [
        # 1. Bass boost — warmth
        ([ffmpeg_bin, "-y", "-i", input_path,
          "-af", "equalizer=f=120:width_type=o:width=2:g=3",
          f"{base}_s1.mp3"], "bass boost"),
        # 3. Light compression — consistent volume
        ([ffmpeg_bin, "-y", "-i", f"{base}_s1.mp3",
          "-af", "acompressor=threshold=0.5:ratio=4:attack=5:release=50",
          f"{base}_s3.mp3"], "compression"),
        # 4. Subtle reverb — space and depth
        ([ffmpeg_bin, "-y", "-i", f"{base}_s3.mp3",
          "-af", "aecho=0.8:0.9:40:0.3",
          f"{base}_s4.mp3"], "reverb"),
        # 5. Loudness normalisation
        ([ffmpeg_bin, "-y", "-i", f"{base}_s4.mp3",
          "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
          f"{base}_processed.mp3"], "loudnorm"),
    ]

    prev = input_path
    step_files: list[str] = []
    for cmd, label in steps:
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            step_files.append(cmd[-1])
            prev = cmd[-1]
        except Exception as e:
            print(f"[Audio] Netflix step '{label}' failed: {e} — stopping chain")
            break

    if not step_files:
        return input_path

    final_processed = step_files[-1]

    # Mix background music via dedicated function
    _is_short = "short" in os.path.basename(input_path).lower()
    mixed = mix_background_music(final_processed, is_short=_is_short)
    if mixed != final_processed:
        final_processed = mixed

    # Replace original with processed
    try:
        shutil.move(final_processed, input_path)
    except Exception as e:
        print(f"[Audio] Could not replace original with processed: {e}")
        return final_processed

    # Clean up intermediate step files
    for f in step_files:
        if f != final_processed and os.path.exists(f):
            try: os.remove(f)
            except OSError: pass

    print("[Audio] Audio post-processed: bass boost + compression + reverb + music mixed")
    return input_path


# â"€â"€ Section-aware TTS + accurate chapter builder â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

_SECTION_DISPLAY = {
    "Introduction":   "ðŸŽ¬ Introduction",
    "Background":     "🔺 Background & Context",
    "Main Story":     "🔍 Main Story",
    "Shocking Facts": "ðŸ'€ Shocking Facts",
    "Conclusion":     "ðŸŽ¯ Conclusion",
    "Ù…Ù‚Ø¯Ù…Ø©":          "ðŸŽ¬ Ù…Ù‚Ø¯Ù…Ø©",
    "Ø§Ù„Ø®Ù„ÙÙŠØ©":         "🔺 Ø§Ù„Ø®Ù„ÙÙŠØ© ÙˆØ§Ù„Ø³ÙŠØ§Ù‚",
    "Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©":  "🔍 Ø§Ù„Ù‚ØµØ© Ø§Ù„Ø±Ø¦ÙŠØ³ÙŠØ©",
    "Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©":    "ðŸ'€ Ø­Ù‚Ø§Ø¦Ù‚ ØµØ§Ø¯Ù…Ø©",
    "Ø§Ù„Ø®Ø§ØªÙ…Ø©":         "ðŸŽ¯ Ø§Ù„Ø®Ø§ØªÙ…Ø©",
}


def _canonical_section_name(name: str) -> str:
    """Normalize section names to stable display keys."""
    n = (name or "").strip().strip("-: ").lower()
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
        "shocking facts": "Shocking Facts",
        "revelations": "Shocking Facts",
        "conclusion": "Conclusion",
        "ending": "Conclusion",
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
    return aliases.get(n, name.strip())


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

    # Parse section markers robustly (English + Arabic + braces).
    sections = _parse_script_sections(script_text)

    if not sections:
        print("[Video] No section markers — using single-call TTS")
        audio_path = generate_voiceover(script_text, video_id, language)
        return audio_path, ""

    print(f"[Video] Generating TTS for {len(sections)} sections")

    section_paths: list[str]   = []
    section_durations: list[float] = []

    for i, (name, content) in enumerate(sections):
        sec_id   = f"{video_id}_sec{i}"
        sec_path = generate_voiceover(content, sec_id, language)
        if not sec_path or not os.path.exists(sec_path):
            print(f"[Video] Section {i + 1} TTS failed — falling back to full-script TTS")
            audio_path = generate_voiceover(script_text, video_id, language)
            return audio_path, ""
        dur = get_audio_duration(sec_path)
        section_durations.append(dur)
        section_paths.append(sec_path)
        print(f"[Video] Section {i + 1} '{name}': {dur:.1f}s ({format_time(dur)})")

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
        display = _SECTION_DISPLAY.get(name, f"🔌 {name}")
        chapter_lines.append(f"{format_time(cumulative)} {display}")
        cumulative += section_durations[i]

    chapters = "\n".join(chapter_lines)
    total_dur = sum(section_durations)
    print(f"[Video] Chapters built (total {format_time(total_dur)}):\n{chapters}")
    return final_audio, chapters


# â"€â"€ Main entry point â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€

def create_video(script_data: dict, video_id: str, custom_audio_path: str = "", user_images: list | None = None, user_videos: list | None = None) -> str:
    import traceback
    title    = script_data.get("title", "")
    niche    = script_data.get("niche", "")
    language = script_data.get("language", "english")
    print(f"[Video] Starting: {title} ({language})")

    # â"€â"€ Voiceover â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    is_short = "short" in video_id
    try:
        if custom_audio_path and Path(custom_audio_path).exists():
            enhanced_path = os.path.join(AUDIO_DIR, f"{video_id}_enhanced.mp3")
            audio_path = clean_voice(custom_audio_path, enhanced_path)
            print(f"[Video] Using custom audio: {audio_path}")
        elif not is_short:
            # Long video: section-by-section TTS for accurate chapter timestamps
            audio_path, dynamic_chapters = generate_tts_sections(
                script_data["script"], video_id, language
            )
            if dynamic_chapters:
                script_data["chapters"] = dynamic_chapters
                print("[Video] Dynamic chapters saved to script_data")
            # Netflix-quality audio post-processing (long videos only)
            if audio_path and os.path.exists(audio_path):
                audio_path = process_audio_netflix(audio_path)
        else:
            audio_path = generate_voiceover(script_data["script"], video_id, language)
            # Mix background music for shorts
            if audio_path and os.path.exists(audio_path):
                audio_path = mix_background_music(audio_path, is_short=True)
        print(f"[Video] Audio ready: {audio_path}")
        # Duration check
        try:
            try:
                from moviepy.editor import AudioFileClip as _AC
            except ImportError:
                from moviepy import AudioFileClip as _AC
            _dur = _AC(audio_path).duration
            _min = _dur / 60
            _is_short_check = "short" in video_id
            if _is_short_check:
                if _dur < 60:
                    print(f"[Video] WARNING: Short audio too short: {_dur:.1f}s (need 60-90s)")
                elif _dur > 90:
                    print(f"[Video] WARNING: Short audio too long: {_dur:.1f}s (need 60-90s)")
                else:
                    print(f"[Video] Short duration OK: {_dur:.1f}s")
            else:
                if _dur < 600:
                    print(f"[Video] WARNING: Long audio too short: {_min:.1f} min (need 10-14 min)")
                elif _dur > 840:
                    print(f"[Video] WARNING: Long audio too long: {_min:.1f} min (need 10-14 min)")
                else:
                    print(f"[Video] Long duration OK: {_min:.1f} min")
        except Exception:
            pass
    except Exception as e:
        print(f"[Video] CRASH at voiceover: {e}")
        traceback.print_exc()
        return ""

    # â"€â"€ Image / clip counts â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€â"€
    # Whisper subtitles (word-level, for burning + timestamp sync)
    whisper_segments: list[dict] = []
    if ENABLE_SUBTITLES:
        try:
            whisper_segments = generate_subtitles(audio_path, language)
        except Exception as _ws_e:
            print(f"[Subtitle] Skipping Whisper (non-fatal): {_ws_e}")

    n_images = calculate_unique_images(is_short=is_short)
    calculate_total_images(user_images)
    print(f"[Video] Building {n_images} visuals ({'short' if is_short else 'long'})")

    script_text = script_data.get("script", "")
    topic_str   = script_data.get("topic", "")

    # Minimal wait for late-arriving Telegram downloads before scanning
    import time as _t; _t.sleep(1)

    # Auto-load user content from disk (Telegram images → output/user_images, videos → output/user_videos)
    folder_videos = _load_user_videos_from_folder()
    folder_images = _load_user_images_from_folders(topic_str)  # includes output/user_images

    # Deduplicate: merge passed-in lists with folder-loaded lists by path
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
            # Normalise path to absolute in the dict so all downstream code gets abs paths
            _ui_norm = dict(_ui); _ui_norm["path"] = _p_abs
            all_user_images.append(_ui_norm)

    print(f"[Video] User images available on disk at assembly start: {len(all_user_images)}")
    if not all_user_images and not all_user_videos:
        print("[Video] INFO: No user images provided — will use stock/AI visuals only")
    print(f"[DEBUG] User content: {len(all_user_images)} unique images, {len(all_user_videos)} unique videos")
    if all_user_images:
        print(f"[DEBUG] User image paths: {[img['path'] for img in all_user_images]}")

    # Detect assembly mode
    mode = _detect_assembly_mode(all_user_images, all_user_videos)

    try:
        if mode == "user_content":
            # MODE 1: User-provided content
            # Step A: copy user videos to clip pool
            image_paths: list[str] = []
            for uv in all_user_videos:
                path = uv.get("path", "")
                if path and os.path.exists(path):
                    dest = os.path.abspath(os.path.join(IMAGES_DIR, f"{video_id}_uv_{len(image_paths)}.mp4"))
                    try:
                        import shutil as _shutil
                        _shutil.copy2(path, dest)
                        if os.path.exists(dest):
                            image_paths.append(dest)
                            print(f"[Video] User video added: {uv.get('caption','')[:60]}")
                        else:
                            print(f"[Video] WARNING: Copy appeared to succeed but {dest} not found")
                    except Exception as _e:
                        print(f"[Video] Could not copy user video {path}: {_e}")

            # Step A2: no user videos — auto-search archive and YouTube CC
            if not all_user_videos:
                print("[Video] No user videos — searching archive and YouTube CC automatically")
                auto_vids = fetch_stock_videos(
                    script_text, min(4, max(2, n_images // 3)), video_id, topic=topic_str
                )
                for vpath in auto_vids:
                    if vpath not in image_paths:
                        image_paths.append(vpath)

            # Step B: copy user images directly into clip pool (BEFORE stock)
            for i, ui in enumerate(all_user_images):
                path = ui.get("path", "")
                if path and os.path.exists(path):
                    ext = os.path.splitext(path)[1] or ".jpg"
                    dest = os.path.abspath(os.path.join(IMAGES_DIR, f"{video_id}_ui_{i}{ext}"))
                    try:
                        import shutil as _shutil
                        _shutil.copy2(path, dest)
                        if os.path.exists(dest):
                            image_paths.append(dest)
                            print(f"[Video] User image added: {ui.get('caption','')[:60]} → {dest}")
                        else:
                            print(f"[Video] WARNING: Copy appeared to succeed but {dest} not found")
                    except Exception as _e:
                        print(f"[Video] Could not copy user image {path}: {_e}")

            # Step C: smart gap-fill based on content sufficiency
            audio_duration = _ffprobe_duration(audio_path) or (n_images * 8)
            is_sufficient, coverage_ratio = check_content_sufficiency(
                all_user_images, all_user_videos, audio_duration
            )
            if is_sufficient:
                print(f"[Video] ✅ User content sufficient — skipping all AI/stock generation")
            elif len(image_paths) < n_images:
                missing = n_images - len(image_paths)
                print(f"[Video] ⚠️ Gap: {missing} visuals needed (coverage {coverage_ratio*100:.0f}%)")
                gap_imgs = _fetch_gap_images(
                    script_text, missing, video_id, topic_str, coverage_ratio
                )
                if gap_imgs:
                    gap_imgs = _rank_visual_pool(gap_imgs, topic=topic_str)
                image_paths.extend(gap_imgs)
        else:
            # MODE 2: Auto (no user content)
            image_paths = fetch_stock_videos(script_text, n_images, video_id, topic=topic_str)
            if len(image_paths) < max(6, n_images // 2):
                missing = max(0, n_images - len(image_paths))
                if missing:
                    print(f"[Stock] Fallback: generating {missing} image visuals")
                    image_paths.extend(fetch_real_images(script_text, missing, video_id, topic=topic_str))
            if image_paths:
                image_paths = _rank_visual_pool(image_paths, topic=topic_str)
    except Exception as e:
        print(f"[Video] CRASH at visual generation: {e}")
        traceback.print_exc()
        return ""

    if not image_paths:
        print("[Video] No visuals generated, aborting")
        return ""

    # Wikipedia real photo + processed user images (for moment matching)
    person_name = _extract_person_name_from_topic(title, topic_str)
    priority_images = get_person_images(
        person_name, video_id,
        # In MODE 1 user images are already in image_paths; only pass to get_person_images
        # for Wikipedia portrait + AI-transform expansion
        all_user_images if all_user_images else None,
        script_text=script_text,
    )

    # Sort priority images by keyword timestamp from Whisper segments
    if whisper_segments and priority_images:
        def _img_ts(img):
            tags = img.get("tags", []) or img.get("caption", "").split()
            ts = find_keyword_timestamp(whisper_segments, tags)
            return ts if ts is not None else float("inf")
        priority_images.sort(key=_img_ts)
        print("[Visual] User images sorted by audio keyword timestamp")

    # BUG 2 fix: In MODE 1, image_paths already has user images first.
    # build_image_list puts priority_images before stock; match_images_to_moments
    # now exhausts user images before stock (fixed above).
    # For MODE 1, skip moment matching — user images are already ordered correctly.
    if mode == "user_content":
        # User images are first in image_paths; just append any extra priority images
        # (Wikipedia photo, AI-transformed versions) that aren't already present
        extra_paths = [img["path"] for img in priority_images
                       if isinstance(img, dict) and img.get("path")
                       and img["path"] not in image_paths
                       and os.path.exists(img["path"])]
        all_image_paths = image_paths + extra_paths
        print(f"[DEBUG] MODE 1 final pool: {len(image_paths)} direct + {len(extra_paths)} extra priority = {len(all_image_paths)} total")
    else:
        # MODE 2: use moment matching (user images exhausted first per fix above)
        try:
            moments = parse_script_moments(script_text, topic=topic_str)
            if moments and (priority_images or image_paths):
                matched = match_images_to_moments(moments, priority_images, image_paths)
                all_image_paths = matched if matched else build_image_list(priority_images, image_paths)
            else:
                all_image_paths = build_image_list(priority_images, image_paths)
        except Exception as e:
            print(f"[Visual] Moment matching failed ({e}), using default image order")
            all_image_paths = build_image_list(priority_images, image_paths)

    print(f"[DEBUG] First 5 images selected for video: {[os.path.basename(p) for p in all_image_paths[:5]]}")

    # ── Image enhancement ────────────────────────────────────────────────────────
    try:
        from agents.enhancer import enhance_image as _enhance_image
        _img_exts = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".bmp"}
        _to_enhance = [
            p for p in all_image_paths
            if p and Path(p).suffix.lower() in _img_exts and os.path.exists(p)
        ]
        if _to_enhance:
            print(f"[Video] Enhancing {len(_to_enhance)} image(s) before rendering...")
            from concurrent.futures import ThreadPoolExecutor as _TPE
            with _TPE(max_workers=min(4, len(_to_enhance))) as _pool:
                _enh_results = list(_pool.map(_enhance_image, _to_enhance))
            _enh_map = dict(zip(_to_enhance, _enh_results))
            # Use `or p` so that a None result from enhance_image falls back to original
            all_image_paths = [_enh_map.get(p) or p for p in all_image_paths]
            print(f"[Video] Enhancement complete")
    except Exception as _enh_err:
        print(f"[Video] Enhancement skipped (non-fatal): {_enh_err}")

    # ── Pre-assembly validation ───────────────────────────────────────────────
    # Filter any None or missing paths that could crash MoviePy
    _missing = [p for p in all_image_paths if not p or not os.path.exists(p)]
    if _missing:
        print(f"[Video] WARNING: {len(_missing)} image path(s) missing before assembly — filtering out")
        all_image_paths = [p for p in all_image_paths if p and os.path.exists(p)]
    print(f"[DEBUG] Final image list count: {len(all_image_paths)}")
    print(f"[DEBUG] First 3 image paths: {all_image_paths[:3]}")

    # ── Assembly ──────────────────────────────────────────────────────────────
    output_path = os.path.join(FINAL_DIR, f"{video_id}.mp4")

    if is_short:
        video_path = assemble_short_video(
            audio_path=audio_path,
            image_paths=all_image_paths,
            output_path=output_path,
        )
    else:
        video_path = assemble_video_with_hook(
            audio_path=audio_path,
            image_paths=all_image_paths,
            output_path=output_path,
            video_id=video_id,
        )

    if video_path:
        # Burn subtitles onto final video
        if whisper_segments:
            try:
                subbed_path = video_path.replace(".mp4", "_subbed.mp4")
                burned = burn_subtitles_ffmpeg(video_path, whisper_segments, subbed_path, language)
                if burned and os.path.exists(burned):
                    os.replace(burned, video_path)
                    print("[Subtitle] Subtitles burned into final video")
            except Exception as _sub_e:
                print(f"[Subtitle] Burn failed (non-fatal): {_sub_e}")

        # Mix original audio from pure/clean/scene user videos at 25%
        pure_paths = [
            uv["path"] for uv in all_user_videos
            if _is_pure_video(uv) and os.path.exists(uv.get("path", ""))
        ]
        if pure_paths:
            video_path = _mix_pure_video_audio(video_path, pure_paths)

        # Apply cold open + overlays
        _overlay_title    = script_data.get("title", "")
        _overlay_chapters = script_data.get("chapters", "")
        _overlay_hook     = script_data.get("angle_title", "") or script_data.get("topic", "")
        video_path = _apply_intro_outro_overlay(
            video_path,
            title=_overlay_title,
            language=language,
            video_id=video_id,
            is_short=is_short,
            chapters_str=_overlay_chapters,
            hook_text=_overlay_hook,
        )

        # Intro disabled — black screen confirmed in production benchmarks
        # To re-enable: change "FORCE_ENABLE" back to ("1", "true", "yes") after fixing
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
        script_data["short_clip_path"] = cut_short_clip(video_path, short_out, script_data=script_data)

        # Generate styled thumbnail; fall back to raw frame extraction on failure
        thumb_path = os.path.join(FINAL_DIR, f"{video_id}_thumb.jpg")
        _thumb_candidates = [p for p in all_image_paths[:5] if p and os.path.exists(p)]
        _thumb = None
        if _thumb_candidates:
            try:
                from agents.thumbnail_generator import create_thumbnail as _mk_thumb, select_best_image as _sbi
                _thumb_src = _sbi(_thumb_candidates)
                _thumb = _mk_thumb(
                    image_path  = _thumb_src,
                    title       = script_data.get("title", ""),
                    output_path = thumb_path,
                    language    = language,
                )
            except Exception as _te:
                print(f"[Thumb] Non-fatal: {_te}")
        if not _thumb:
            _thumb = extract_first_frame(video_path, thumb_path)
        if _thumb:
            script_data["thumbnail_path"] = _thumb
    return video_path
