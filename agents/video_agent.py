# ============================================================
#  agents/video_agent.py  —  AI-generated images + voiceover
# ============================================================
import os
import json
import time
import random
import asyncio
import requests
from pathlib import Path

import moviepy
print(f"[Video] MoviePy version: {moviepy.__version__}")
if moviepy.__version__.startswith('2'):
    print("[Video] WARNING: MoviePy 2.x detected!")
    print("[Video] Using compatibility mode")
    MOVIEPY_V2 = True
else:
    print("[Video] MoviePy 1.x confirmed ✅")
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
    EDGETTS_RATE,
)

IMAGES_DIR = "output/images"
for d in [AUDIO_DIR, VIDEO_DIR, FINAL_DIR, IMAGES_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)


# ── Chapter / timestamp helpers ───────────────────────────────────────────────

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
    """Remove [SECTION: ...] markers so they are never spoken in TTS."""
    import re
    return re.sub(r'\[SECTION:[^\]]+\]\s*', '', text).strip()


# ── Voiceover ─────────────────────────────────────────────────────────────────

def get_voice(language: str) -> str:
    voices = {
        "arabic": "ar-SA-HamedNeural",
        "english": "en-US-GuyNeural"
    }
    return voices.get(language.lower(), "en-US-GuyNeural")


def generate_voiceover_edgetts(script_text: str, filename: str, language: str = "english") -> str:
    """Generate voiceover using edge-tts."""
    try:
        import edge_tts
    except ImportError:
        os.system("pip install edge-tts -q")
        import edge_tts

    if language.lower() == "arabic":
        voice = "ar-SA-ZariyahNeural"
        rate  = "+15%"
    else:
        voice = "en-US-ChristopherNeural"
        rate  = "+20%"

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
    import openai
    import httpx

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("[Voice] OpenAI API key not set — skipping")
        return ""

    client = openai.OpenAI(
        api_key=api_key,
        timeout=httpx.Timeout(60.0, connect=10.0),
    )

    if is_short:
        model = "tts-1"
        voice = "alloy" if language == "arabic" else "echo"
        speed = 1.10
        label = f"{'Arabic' if language == 'arabic' else 'English'} short"
    elif language == "arabic":
        model = "tts-1"
        voice = "alloy"
        speed = 1.06
        label = "Arabic long"
    else:
        model = "gpt-4o-mini-tts"
        voice = "onyx"
        speed = 1.03
        label = "English long"

    # Voice instructions (only gpt-4o-mini-tts supports this parameter)
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
    }
    tts_instructions = _INSTRUCTIONS.get(voice) if model == "gpt-4o-mini-tts" else None

    print(f"[Voice] TTS speed: {speed} ({label}) | model={model} voice={voice}")

    try:
        chunks = _split_text(text, max_chars=4000)
        print(f"[Voice] OpenAI TTS: {len(chunks)} chunk(s)")

        audio_files: list[str] = []
        base = output_path.replace(".mp3", "")
        for i, chunk in enumerate(chunks):
            chunk_path = f"{base}_oai_chunk{i}.mp3"

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
                    print(f"[Voice] OpenAI chunk attempt {attempt + 1} failed: {e}")
                    time.sleep(5)
            else:
                print(f"[Voice] OpenAI chunk {i + 1} failed all attempts")
                for f in audio_files:
                    try: os.remove(f)
                    except OSError: pass
                return ""

        # Merge chunks
        if len(audio_files) == 1:
            import shutil
            shutil.move(audio_files[0], output_path)
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
                import shutil
                shutil.copy(audio_files[0], output_path)
                print("[Voice] OpenAI using first chunk only")
            for f in audio_files:
                if os.path.exists(f) and f != output_path:
                    try: os.remove(f)
                    except OSError: pass
            try: os.remove(list_path)
            except OSError: pass

        print(f"[Voice] OpenAI TTS complete: {output_path}")
        return output_path

    except Exception as e:
        print(f"[Voice] OpenAI TTS failed: {e}")
        return ""


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


def _split_text(text: str, max_chars: int = 1500) -> list[str]:
    """Split text on word boundaries into chunks no larger than max_chars."""
    chunks: list[str] = []
    words = text.split()
    current: list[str] = []
    current_len = 0
    for word in words:
        if current_len + len(word) > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + 1
    if current:
        chunks.append(" ".join(current))
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
            "speed": 1.0,
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
    """Generate voiceover — ElevenLabs → OpenAI TTS → edge-tts priority chain."""
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
    from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID_EN, ELEVENLABS_VOICE_ID_AR, ELEVENLABS_VOICE_ID

    # ── Priority 1: ElevenLabs ────────────────────────────────────────────────
    api_key = ELEVENLABS_API_KEY
    print(f"[Voice] ElevenLabs key: {'set' if api_key and api_key != 'YOUR_ELEVENLABS_KEY' else 'MISSING'}")
    if api_key and api_key != "YOUR_ELEVENLABS_KEY":
        voice_ids = {
            "english": ELEVENLABS_VOICE_ID_EN or ELEVENLABS_VOICE_ID,
            "arabic":  ELEVENLABS_VOICE_ID_AR or ELEVENLABS_VOICE_ID,
        }
        voice_id = voice_ids.get(language.lower(), ELEVENLABS_VOICE_ID)
        print(f"[Voice] Voice ID ({language}): {'set' if voice_id else 'MISSING'}")

        if voice_id:
            audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
            chunks = _split_text(script_text, max_chars=2000)
            print(f"[Voice] ElevenLabs: {len(chunks)} chunk(s) for {language}")

            chunk_files: list[str] = []
            el_failed = False
            for i, chunk in enumerate(chunks):
                chunk_path = os.path.join(AUDIO_DIR, f"{filename}_chunk_{i}.mp3")
                result = _elevenlabs_chunk(chunk, voice_id, api_key, chunk_path)
                if result == "401":
                    for f in chunk_files:
                        try: os.remove(f)
                        except OSError: pass
                    print("[Voice] ElevenLabs 401 — trying OpenAI TTS")
                    el_failed = True
                    break
                elif result:
                    chunk_files.append(chunk_path)
                    print(f"[Voice] ElevenLabs chunk {i + 1}/{len(chunks)} done")
                    if i < len(chunks) - 1:
                        time.sleep(2)
                else:
                    for f in chunk_files:
                        try: os.remove(f)
                        except OSError: pass
                    print(f"[Voice] ElevenLabs chunk {i + 1} failed — trying OpenAI TTS")
                    el_failed = True
                    break

            if not el_failed:
                if len(chunk_files) == 1:
                    import shutil
                    shutil.move(chunk_files[0], audio_path)
                else:
                    merged = False
                    import subprocess
                    list_path = os.path.join(AUDIO_DIR, f"{filename}_list.txt")
                    with open(list_path, "w", encoding="utf-8") as lf:
                        for cf in chunk_files:
                            lf.write(f"file '{os.path.abspath(cf)}'\n")
                    ffmpeg_bin = _get_ffmpeg()
                    if ffmpeg_bin:
                        try:
                            subprocess.run(
                                [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0",
                                 "-i", list_path, "-c", "copy", audio_path],
                                check=True, capture_output=True,
                            )
                            merged = True
                            print("[Voice] Chunks merged with ffmpeg")
                        except Exception as e:
                            print(f"[Voice] ffmpeg concat failed: {e}")
                    if not merged:
                        if _merge_chunks_pydub(chunk_files, audio_path):
                            merged = True
                            print("[Voice] Chunks merged with pydub")
                    if not merged:
                        import shutil
                        shutil.copy(chunk_files[0], audio_path)
                        print("[Voice] Using first chunk only (merge failed)")
                    for f in chunk_files:
                        try: os.remove(f)
                        except OSError: pass
                    try: os.remove(list_path)
                    except OSError: pass

                print(f"[Voice] ElevenLabs complete: {len(chunks)} chunk(s) -> {audio_path}")
                return audio_path

    # ── Priority 2: OpenAI TTS ────────────────────────────────────────────────
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        print("[Voice] Trying OpenAI TTS...")
        _oai_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
        _is_short = "short" in filename.lower()
        result = generate_voiceover_openai(script_text, language, _oai_path, is_short=_is_short)
        if result:
            return result
        print("[Voice] OpenAI TTS failed — falling back to edge-tts")

    # ── Priority 3: edge-tts (always available) ───────────────────────────────
    print("[Voice] Using edge-tts fallback")
    return generate_voiceover_edgetts(script_text, filename, language)


# ── AI Image generation (Pollinations — free, no key) ─────────────────────────

# Combined subject lookup — real criminals AND actor/character portraits.
# extract_main_subject() returns up to 2 entries (longest key match first)
# so Image 1 = real criminal, Image 2 = actor who played them.
SUBJECTS = {
    # ── Real criminals ──────────────────────────────────────────────────────────
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

    # ── Series / movie actors ────────────────────────────────────────────────────
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
    "حميدتي":              "Sudanese military general RSF commander portrait dark cinematic dramatic",
    "dagalo":              "RSF Sudan military commander portrait cinematic dark dramatic",
    "محمد حمدان دقلو":     "Sudanese military general portrait dark cinematic dramatic",
    "omar bashir":         "Omar al-Bashir Sudan dictator president portrait cinematic",
    "البشير":              "Sudan president portrait dark cinematic dramatic",
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


# ── Wikipedia public-domain image fetcher ─────────────────────────────────────

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
) -> str | None:
    """
    Generate a cinematic AI version of a user image using its caption as the prompt.

    Pollinations is a text-to-image API so we use the caption as the seed text,
    with a hash-derived seed for reproducibility (same caption → same image).
    The result is 100% original AI art — no copyright concerns.
    Returns the saved output path or None on failure.
    """
    import hashlib

    caption_clean = (caption or "cinematic dark portrait").strip()
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


def process_user_images(user_images: list[dict], video_id: str) -> list[dict]:
    """
    For each user image: generate an AI-cinematic version from its caption,
    then include the original.

    Returns expanded list in this order per image:
      1. AI-transformed version (caption → Pollinations; tags include "portrait")
      2. Original user image               (tags include "real", "photo")

    The AI version is listed first so _build_clip_pool_with_user_images places
    it at the very opening of the video (portrait tag → position 0).
    """
    processed: list[dict] = []

    for i, img_info in enumerate(user_images):
        path    = img_info.get("path", "")
        caption = (img_info.get("caption") or "cinematic dark portrait").strip()
        tags    = img_info.get("tags", [])

        if not path or not os.path.exists(path):
            continue

        print(f"[Image] Processing user image {i + 1}: '{caption[:60]}'")

        # AI-transformed version (portrait tags → forces to opening position)
        transformed = transform_user_image(path, caption, video_id, i)
        if transformed:
            processed.append({
                "path":    transformed,
                "tags":    ["portrait", "cinematic"] + [t for t in tags if t not in {"portrait", "cinematic"}],
                "caption": f"cinematic {caption}",
                "type":    "ai_transformed",
            })

        # Original user image (real/photo tags → also at/near position 0)
        processed.append({
            "path":    path,
            "tags":    ["real", "photo"] + [t for t in tags if t not in {"real", "photo"}],
            "caption": caption,
            "type":    "user_original",
        })

        print(f"[Image] User image {i + 1}: AI transform + original queued")

    return processed


def check_image_relevance(
    image_path: str,
    topic: str,
    series_name: str | None,
    part_number: int | None = None,
) -> str:
    """Use OpenAI Vision to decide image relevance. Returns 'use_now', 'save_part2', or 'ignore'."""
    import base64

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
                print(f"[Image] ✅ Relevant: {image_path}")
                return "use_now"
            if "SAVE_PART2" in answer:
                print(f"[Image] 📦 Save for Part 2: {image_path}")
                return "save_part2"
            print(f"[Image] ❌ Not relevant: {image_path}")
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
    print(f"  ✅ Use now: {len(use_now)}")
    print(f"  📦 Save Part 2: {len(save_for_later)}")
    print(f"  ❌ Ignored: {len(ignored)}")

    if save_for_later:
        save_images_for_part2(save_for_later, topic)

    return use_now, save_for_later, ignored


def get_person_images(
    person_name: str,
    video_id: str,
    user_images: list[dict] | None = None,
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
        images.extend(process_user_images(raw_uploads, video_id))
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
    """Ask OpenAI for a specific ≤20-word image prompt from a script chunk.
    Falls back to a generic cinematic prompt if OpenAI is unavailable.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    first_200 = " ".join(chunk_text.split()[:200])

    if not api_key:
        return f"dark crime documentary scene, cinematic{_IMAGE_PROMPT_SUFFIX}"

    prompt = f"""Read this script excerpt and write a specific visual image generation prompt (max 20 words) that represents the exact subject being described.

Rules:
- Name real places, real objects, real events
- No human faces
- Dark cinematic documentary style
- Be specific not generic

Examples:
GOOD: 'Burned village Darfur Sudan desert, smoke ruins, golden hour, cinematic aerial view'
BAD: 'dark crime documentary background'

Script excerpt: {first_200}

Return only the image prompt, nothing else."""

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 60,
                "temperature": 0.7,
            },
            timeout=30,
        )
        if r.status_code == 200:
            result = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
            print(f"[Image] Chunk prompt: {result[:70]}")
            return f"{result}{_IMAGE_PROMPT_SUFFIX}"
        print(f"[Image] build_image_prompt error {r.status_code}")
    except Exception as e:
        print(f"[Image] build_image_prompt failed: {e}")

    return f"dark crime documentary scene, cinematic{_IMAGE_PROMPT_SUFFIX}"


def generate_image_prompts(script_text: str, count: int) -> list[str]:
    """Split script into [count] equal chunks, call OpenAI once per chunk.
    Returns list of [count] specific image prompts.
    Falls back gracefully per chunk if OpenAI call fails.
    """
    import re

    # Strip [SECTION: ...] markers so they don't pollute chunk text
    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()

    if not words:
        return [f"dark crime documentary cinematic{_IMAGE_PROMPT_SUFFIX}"] * count

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


# ── Real-photo fetching ────────────────────────────────────────────────────────

def download_real_image(url: str, output_path: str) -> str | None:
    """Download image from URL, smart-crop to 1080×1920 portrait. Returns path or None."""
    import io
    from PIL import Image as PILImage

    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
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


def _ddgs_image_results(query: str, max_results: int = 5) -> list[str]:
    """Return list of image URLs from DuckDuckGo. Returns empty list on any failure."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        os.system("pip install duckduckgo-search -q")
        try:
            from duckduckgo_search import DDGS
        except Exception:
            return []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=max_results, safesearch="moderate"))
        return [r["image"] for r in results if r.get("image")]
    except Exception as e:
        print(f"[Image] DDGS search failed '{query}': {e}")
        return []


def _download_first_valid(urls: list[str], output_path: str) -> str | None:
    """Try each URL in order, return path of the first that downloads successfully."""
    for url in urls:
        saved = download_real_image(url, output_path)
        if saved:
            return saved
    return None


def _translate_to_arabic_query(english_query: str) -> str | None:
    """Translate an English image search query to Arabic via OpenAI."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
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
                    "content": (
                        f"Translate this image search query to Arabic. "
                        f"Return only the Arabic translation, nothing else.\n\n"
                        f"Query: {english_query}"
                    ),
                }],
                "max_tokens": 30,
                "temperature": 0.1,
            },
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[Image] Arabic query translation failed: {e}")
    return None


def search_real_image(query: str, output_path: str) -> str | None:
    """DuckDuckGo image search. Tries up to 5 result URLs. Returns saved path or None."""
    urls = _ddgs_image_results(query)
    if not urls:
        print(f"[Image] No real photo found for '{query}'")
        return None
    saved = _download_first_valid(urls, output_path)
    if saved:
        print(f"[Image] ✅ Real photo: '{query}'")
        return saved
    print(f"[Image] No real photo found for '{query}'")
    return None


def _get_search_query_for_chunk(chunk_text: str) -> str | None:
    """Call OpenAI to get a specific 5-word English image search query for a script chunk.
    Always returns English — works even when chunk_text is Arabic or any other language.
    Works for any topic — crime, politics, war, science, business, sport, etc.
    Returns None if OpenAI is unavailable or the chunk is too generic.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    first_150 = " ".join(chunk_text.split()[:150])
    prompt = f"""What is the single most specific, searchable subject in this text?
Return only a short search query (max 5 words) suitable for image search.
Always write the query in English, even if the text is in Arabic or another language.

Examples:
GOOD: 'Mohamed Hamdan Dagalo RSF'
GOOD: 'Darfur burning village 2003'
GOOD: 'Elon Musk Tesla factory'
GOOD: 'Pablo Escobar mugshot'
BAD: 'crime story background'
BAD: 'dark documentary scene'

Text: {first_150}

Return only the English search query, nothing else."""

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 20,
                "temperature": 0.3,
            },
            timeout=20,
        )
        if r.status_code == 200:
            query = r.json()["choices"][0]["message"]["content"].strip().strip('"\'')
            if len(query.split()) <= 8 and len(query) > 3:
                return query
    except Exception as e:
        print(f"[Image] Search query generation failed: {e}")
    return None


def fetch_real_images(script_text: str, count: int, video_id: str) -> list[str]:
    """
    Universal image builder — works for any script topic.

    For each of [count] equal script chunks:
      1. Ask OpenAI for the most specific searchable subject (max 5 words).
      2. Search DuckDuckGo images for that query — try up to 3 URLs.
      3. Re-use a cached download when the same query appears again.
      4. Fall back to Pollinations AI generation if real search fails.

    Logs each image as ✅ real photo or 🤖 AI generated.
    Returns list of image paths.
    """
    import re
    import shutil

    clean = re.sub(r'\[SECTION:[^\]]+\]\s*', '', script_text).strip()
    words = clean.split()

    seed          = random.randint(1, 99999)
    fallback_base = f"dark cinematic documentary scene{_IMAGE_PROMPT_SUFFIX}"

    if not words:
        paths = []
        for i in range(count):
            p = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.png")
            r = generate_ai_image(fallback_base, p, seed=seed + i)
            if r:
                paths.append(r)
        return paths

    # AI fallback prompts (one OpenAI call per chunk via generate_image_prompts)
    ai_prompts = generate_image_prompts(script_text, count)

    # Split script into equal word-chunks
    chunk_size = max(1, len(words) // count)
    chunks = [
        " ".join(words[i * chunk_size: (i + 1) * chunk_size if i < count - 1 else len(words)])
        for i in range(count)
    ]

    image_paths:  list[str]      = []
    query_cache:  dict[str, str] = {}   # query → saved path (avoid re-downloading)
    real_count    = 0
    ai_count      = 0

    for i, chunk in enumerate(chunks):
        img_path = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.png")
        saved    = None

        # Step 1: get specific search query for this chunk
        query = _get_search_query_for_chunk(chunk)

        # Step 2: English DuckDuckGo search
        if query:
            if query in query_cache:
                shutil.copy2(query_cache[query], img_path)
                saved = img_path
                print(f"[Image] ♻️  Reused '{query}' for chunk {i}")
            else:
                en_urls = _ddgs_image_results(query)
                if len(en_urls) >= 2:
                    saved = _download_first_valid(en_urls, img_path)
                    if saved:
                        print(f"[Image] ✅ Real photo (EN): '{query}'")
                        query_cache[query] = saved
                        real_count += 1

                # Step 3: fewer than 2 English results → retry in Arabic
                if not saved:
                    ar_query = _translate_to_arabic_query(query)
                    if ar_query:
                        ar_urls = _ddgs_image_results(ar_query)
                        if ar_urls:
                            print(f"[Image] chunk {i}: retried in Arabic, found {len(ar_urls)} results")
                            saved = _download_first_valid(ar_urls, img_path)
                            if saved:
                                query_cache[query] = saved
                                real_count += 1

        # Step 4: AI fallback — Pollinations with script-matched prompt
        if not saved:
            print(f"[Image] chunk {i}: no real image found, using AI generation")
            ai_prompt = ai_prompts[i] if i < len(ai_prompts) else fallback_base
            saved = generate_ai_image(ai_prompt, img_path, seed=seed + i)
            if saved:
                ai_count += 1

        if saved:
            image_paths.append(saved)

        if i < count - 1:
            time.sleep(2)

    print(f"[Image] Images: {real_count}/{count} real photos | {ai_count}/{count} AI generated")
    return image_paths


# ── Title card helpers ────────────────────────────────────────────────────────

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


# ── MoviePy clip helpers ───────────────────────────────────────────────────────

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

    # ── Load audio ────────────────────────────────────────────────────────────
    try:
        audio = AudioFileClip(audio_path)
        total_duration = audio.duration
        print(f"[Video] Audio duration: {total_duration:.1f}s")
    except Exception as e:
        print(f"[Video] CRASH loading audio: {e}")
        traceback.print_exc()
        return ""

    # ── Build looped clip list (image portion only) ───────────────────────────
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

    # ── Concatenate ───────────────────────────────────────────────────────────
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

    # ── Write video ───────────────────────────────────────────────────────────
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

    # ── Verify output ─────────────────────────────────────────────────────────
    if not os.path.exists(output_path):
        print(f"[Video] ERROR: output file not created: {output_path}")
        return ""
    file_size = os.path.getsize(output_path)
    if file_size < 100_000:
        print(f"[Video] ERROR: output file too small ({file_size} bytes) — likely corrupt")
        return ""
    print(f"[Video] Success: {output_path} ({file_size // 1024 // 1024}MB)")
    return output_path


# ── Voice enhancement (for user-recorded audio) ───────────────────────────────

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


# ── Short clip cutter ──────────────────────────────────────────────────────────

SHORTS_DIR = "output/shorts"
Path(SHORTS_DIR).mkdir(parents=True, exist_ok=True)


def cut_short_clip(video_path: str, output_path: str, duration: int = 90) -> str:
    """Cut the first 60-90 seconds (random) of a video and save to output_path."""
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
        # Random duration between 60-90 seconds
        actual_duration = random.randint(60, 90)
        actual_duration = min(actual_duration, clip.duration)
        short = clip.subclip(0, actual_duration)
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


# ── User image helpers ─────────────────────────────────────────────────────────

def _find_keyword_position(script_text: str, tags: list[str]) -> float:
    """Return 0.0–1.0 relative position where the first tag appears in the script.
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


# ── Hook-aware assembly (long videos only) ────────────────────────────────────

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
        from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips
    except ImportError:
        from moviepy import AudioFileClip, VideoClip, concatenate_videoclips

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

    def _zoom_clip(
        frame, dur: float,
        start_scale: float, end_scale: float,
        fade_in: float = 0.0, fade_out: float = 0.0,
    ):
        """VideoClip with zoom + fade-in/out baked into make_frame.

        Uses VideoClip(make_frame) so output is always exactly TARGET_W×TARGET_H
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

    # ── HOOK SECTION (0:00 to 1:30): fast cuts every 3-5 s ───────────────────
    # Cycle through ALL images repeatedly — movie-trailer energy
    hook_clips = []
    hook_total = 0.0
    img_index  = 0

    while hook_total < hook_duration:
        img_path = image_paths[img_index % len(image_paths)]
        try:
            frame     = _load_frame(img_path)
            cut_dur   = random.uniform(3, 5)
            remaining = hook_duration - hook_total
            cut_dur   = min(cut_dur, remaining)
            if img_index % 2 == 0:
                clip = _zoom_clip(frame, cut_dur, 1.00, 1.08, fade_in=0.2, fade_out=0.2)
            else:
                clip = _zoom_clip(frame, cut_dur, 1.08, 1.00, fade_in=0.2, fade_out=0.2)
            hook_clips.append(clip)
            hook_total += cut_dur
        except Exception as e:
            print(f"[Video] Hook clip error: {e}")
        img_index += 1

    print(f"[Video] Hook: {len(hook_clips)} fast cuts in {hook_total:.1f}s")

    # ── MAIN CONTENT (1:30 to end): slow cuts every 8-12 s ───────────────────
    # Each image gets a zoom-in clip + zoom-out clip; then shuffled
    main_clips = []
    for img_path in image_paths:
        try:
            frame = _load_frame(img_path)
            dur1  = random.uniform(8, 12)
            main_clips.append(_zoom_clip(frame, dur1, 1.00, 1.06, fade_in=0.5, fade_out=0.5))
            dur2  = random.uniform(8, 12)
            main_clips.append(_zoom_clip(frame, dur2, 1.06, 1.00, fade_in=0.5, fade_out=0.5))
        except Exception as e:
            print(f"[Video] Main clip error: {e}")

    random.shuffle(main_clips)

    # Loop main clips until they cover main_duration + buffer
    while sum(c.duration for c in main_clips) < main_duration + 20:
        src = image_paths[random.randint(0, len(image_paths) - 1)]
        dur = random.uniform(8, 12)
        main_clips.append(_zoom_clip(_load_frame(src), dur, 1.00, 1.06, fade_in=0.5, fade_out=0.5))

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


# ── Image count helpers ───────────────────────────────────────────────────────

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


# ── Short video assembler ─────────────────────────────────────────────────────

def assemble_short_video(audio_path: str, image_paths: list[str], output_path: str) -> str:
    """Assemble short video: 2 zoom variations per image, loop to fill 60-90 s."""
    import traceback
    import numpy as np
    from PIL import Image as PILImage
    try:
        from moviepy.editor import AudioFileClip, VideoClip, concatenate_videoclips
    except ImportError:
        from moviepy import AudioFileClip, VideoClip, concatenate_videoclips

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

    # Pre-load frames
    frames = []
    for img_path in image_paths:
        try:
            frames.append(_load_frame(img_path))
        except Exception as e:
            print(f"[Video] Short image load error: {e}")

    if not frames:
        print("[Video] No frames for short video, aborting")
        return ""

    # 2 variations per image: zoom in (5-7 s) + zoom out (5-7 s)
    all_clips = []
    for frame in frames:
        all_clips.append(_zoom_clip(frame, 1.00, 1.08, random.uniform(5, 7)))
        all_clips.append(_zoom_clip(frame, 1.08, 1.00, random.uniform(5, 7)))

    random.shuffle(all_clips)

    # Loop by regenerating new clips from random frames until we have enough
    while sum(c.duration for c in all_clips) < total_duration + 5:
        frame = frames[random.randint(0, len(frames) - 1)]
        all_clips.append(_zoom_clip(frame, 1.00, 1.08, random.uniform(5, 7)))

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


# ── Section-aware TTS + accurate chapter builder ──────────────────────────────

_SECTION_DISPLAY = {
    "Introduction":   "🎬 Introduction",
    "Background":     "📺 Background & Context",
    "Main Story":     "🔍 Main Story",
    "Shocking Facts": "💀 Shocking Facts",
    "Conclusion":     "🎯 Conclusion",
}


def generate_tts_sections(script_text: str, video_id: str, language: str) -> tuple[str, str]:
    """
    Split script at [SECTION: ...] markers, generate TTS per section,
    measure each section duration with mutagen, build accurate chapter
    timestamps, concatenate all sections into one audio file.

    Returns (audio_path, chapters_text).
    Falls back to single full-script TTS when markers are absent or any
    section TTS call fails.
    """
    import re
    import subprocess

    final_audio = os.path.join(AUDIO_DIR, f"{video_id}.mp3")

    # Parse [SECTION: Name] markers
    raw = re.split(r'\[SECTION:\s*([^\]]+)\]', script_text.strip())
    sections: list[tuple[str, str]] = []
    for i in range(1, len(raw), 2):
        name    = raw[i].strip()
        content = raw[i + 1].strip() if i + 1 < len(raw) else ""
        if content:
            sections.append((name, content))

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
    chapter_lines = ["⏱️ CHAPTERS"]
    for i, (name, _) in enumerate(sections):
        display = _SECTION_DISPLAY.get(name, f"📌 {name}")
        chapter_lines.append(f"{format_time(cumulative)} {display}")
        cumulative += section_durations[i]

    chapters = "\n".join(chapter_lines)
    total_dur = sum(section_durations)
    print(f"[Video] Chapters built (total {format_time(total_dur)}):\n{chapters}")
    return final_audio, chapters


# ── Main entry point ───────────────────────────────────────────────────────────

def create_video(script_data: dict, video_id: str, custom_audio_path: str = "", user_images: list | None = None) -> str:
    import traceback
    title    = script_data.get("title", "")
    niche    = script_data.get("niche", "")
    language = script_data.get("language", "english")
    print(f"[Video] Starting: {title} ({language})")

    # ── Voiceover ─────────────────────────────────────────────────────────────
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
        else:
            audio_path = generate_voiceover(script_data["script"], video_id, language)
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
                if _dur < 900:
                    print(f"[Video] WARNING: Long audio too short: {_min:.1f} min (need 15-19 min)")
                elif _dur > 1140:
                    print(f"[Video] WARNING: Long audio too long: {_min:.1f} min (need 15-19 min)")
                else:
                    print(f"[Video] Long duration OK: {_min:.1f} min")
        except Exception:
            pass
    except Exception as e:
        print(f"[Video] CRASH at voiceover: {e}")
        traceback.print_exc()
        return ""

    # ── Image / clip counts ───────────────────────────────────────────────────
    n_images = calculate_unique_images(is_short=is_short)
    calculate_total_images(user_images)
    print(f"[Video] Generating {n_images} AI images ({'short' if is_short else 'long'})")

    # ── Image generation (real photos + AI fallback per script chunk) ────────
    try:
        script_text = script_data.get("script", "")
        image_paths = fetch_real_images(script_text, n_images, video_id)
    except Exception as e:
        print(f"[Video] CRASH at image generation: {e}")
        traceback.print_exc()
        return ""

    if not image_paths:
        print("[Video] No images generated, aborting")
        return ""

    # ── Wikipedia real photo + user uploads (priority images) ────────────────
    person_name = _extract_person_name_from_topic(title, script_data.get("topic", ""))
    priority_images = get_person_images(person_name, video_id, user_images)

    # ── Assembly ──────────────────────────────────────────────────────────────
    output_path = os.path.join(FINAL_DIR, f"{video_id}.mp4")
    all_image_paths = build_image_list(priority_images, image_paths)

    if is_short:
        # Short: 8 AI images × 2 variations = 16 clips, loop to 60-90s
        video_path = assemble_short_video(
            audio_path=audio_path,
            image_paths=all_image_paths,
            output_path=output_path,
        )
    else:
        # Long: 12 AI images, hook-aware assembly (fast cuts 0-90s, slow after)
        video_path = assemble_video_with_hook(
            audio_path=audio_path,
            image_paths=all_image_paths,
            output_path=output_path,
            video_id=video_id,
        )

    if video_path:
        short_out = os.path.join(SHORTS_DIR, f"{video_id}_short.mp4")
        script_data["short_clip_path"] = cut_short_clip(video_path, short_out)
    return video_path
