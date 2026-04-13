# ============================================================
#  agents/video_agent.py  —  AI-generated images + voiceover
# ============================================================
import os
import time
import random
import asyncio
import requests
from pathlib import Path
from config import (
    AUDIO_DIR, VIDEO_DIR, FINAL_DIR,
    VIDEO_WIDTH, VIDEO_HEIGHT
)

IMAGES_DIR = "output/images"
for d in [AUDIO_DIR, VIDEO_DIR, FINAL_DIR, IMAGES_DIR]:
    Path(d).mkdir(parents=True, exist_ok=True)


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

    voice = get_voice(language)
    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")

    async def _generate():
        communicate = edge_tts.Communicate(script_text, voice)
        await communicate.save(audio_path)

    asyncio.run(_generate())
    print(f"[Video] Voiceover saved (edge-tts): {audio_path}")
    return audio_path


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
            "stability": 0.35,
            "similarity_boost": 0.90,
            "style": 0.45,
            "use_speaker_boost": True,
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
    """Generate voiceover — ElevenLabs (chunked) if configured, edge-tts as fallback."""
    from config import ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID_EN, ELEVENLABS_VOICE_ID_AR, ELEVENLABS_VOICE_ID

    api_key = ELEVENLABS_API_KEY
    print(f"[Voice] ElevenLabs key: {'set' if api_key and api_key != 'YOUR_ELEVENLABS_KEY' else 'MISSING'}")
    if not api_key or api_key == "YOUR_ELEVENLABS_KEY":
        return generate_voiceover_edgetts(script_text, filename, language)

    voice_ids = {
        "english": ELEVENLABS_VOICE_ID_EN or ELEVENLABS_VOICE_ID,
        "arabic":  ELEVENLABS_VOICE_ID_AR or ELEVENLABS_VOICE_ID,
    }
    voice_id = voice_ids.get(language.lower(), ELEVENLABS_VOICE_ID)
    print(f"[Voice] Voice ID ({language}): {'set' if voice_id else 'MISSING'}")
    if not voice_id:
        return generate_voiceover_edgetts(script_text, filename, language)

    audio_path = os.path.join(AUDIO_DIR, f"{filename}.mp3")
    chunks = _split_text(script_text, max_chars=2000)
    print(f"[Voice] ElevenLabs: {len(chunks)} chunk(s) for {language}")

    chunk_files: list[str] = []
    for i, chunk in enumerate(chunks):
        chunk_path = os.path.join(AUDIO_DIR, f"{filename}_chunk_{i}.mp3")
        result = _elevenlabs_chunk(chunk, voice_id, api_key, chunk_path)
        if result == "401":
            # 401 will never succeed — skip retries, go straight to edge-tts
            for f in chunk_files:
                try: os.remove(f)
                except OSError: pass
            print(f"[Voice] 401 Unauthorized — falling back to edge-tts immediately")
            return generate_voiceover_edgetts(script_text, filename, language)
        elif result:
            chunk_files.append(chunk_path)
            print(f"[Voice] Chunk {i + 1}/{len(chunks)} done")
            if i < len(chunks) - 1:
                time.sleep(2)
        else:
            # Clean up and fall back to edge-tts for the whole script
            for f in chunk_files:
                try: os.remove(f)
                except OSError: pass
            print(f"[Voice] Chunk {i + 1} failed — falling back to edge-tts")
            return generate_voiceover_edgetts(script_text, filename, language)

    if len(chunk_files) == 1:
        import shutil
        shutil.move(chunk_files[0], audio_path)
    else:
        merged = False

        # Attempt 1 — ffmpeg concat
        import subprocess
        list_path = os.path.join(AUDIO_DIR, f"{filename}_list.txt")
        with open(list_path, "w", encoding="utf-8") as lf:
            for cf in chunk_files:
                lf.write(f"file '{os.path.abspath(cf)}'\n")
        ffmpeg_bin = _get_ffmpeg()
        if ffmpeg_bin:
            try:
                subprocess.run(
                    [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", audio_path],
                    check=True, capture_output=True,
                )
                merged = True
                print("[Voice] Chunks merged with ffmpeg")
            except Exception as e:
                print(f"[Voice] ffmpeg concat failed: {e}")

        # Attempt 2 — pydub (with imageio_ffmpeg path injected)
        if not merged:
            if _merge_chunks_pydub(chunk_files, audio_path):
                merged = True
                print("[Voice] Chunks merged with pydub")

        # Attempt 3 — use first chunk only (better than silence)
        if not merged:
            import shutil
            shutil.copy(chunk_files[0], audio_path)
            print("[Voice] Using first chunk only (merge failed)")

        # Cleanup
        for f in chunk_files:
            try: os.remove(f)
            except OSError: pass
        try: os.remove(list_path)
        except OSError: pass

    print(f"[Voice] ElevenLabs complete: {len(chunks)} chunk(s) -> {audio_path}")
    return audio_path


# ── AI Image generation (Pollinations — free, no key) ─────────────────────────

# Real criminal portraits — used for Image 1
_SUBJECTS_REAL = {
    "escobar":        "Pablo Escobar Colombian drug lord portrait cinematic dramatic",
    "pablo":          "Pablo Escobar Colombian drug lord portrait cinematic dramatic",
    "al capone":      "Al Capone Chicago gangster portrait 1920s cinematic",
    "capone":         "Al Capone Chicago gangster portrait 1920s cinematic",
    "lucky luciano":  "Lucky Luciano New York mafia portrait cinematic",
    "luciano":        "Lucky Luciano New York mafia portrait cinematic",
    "frank lucas":    "Frank Lucas Harlem drug lord portrait cinematic",
    "griselda blanco":"Griselda Blanco cocaine godmother portrait cinematic",
    "btk":            "BTK killer Dennis Rader portrait dark cinematic",
    "dennis rader":   "BTK killer Dennis Rader portrait dark cinematic",
    "dahmer":         "Jeffrey Dahmer serial killer portrait cinematic",
    "jeffrey dahmer": "Jeffrey Dahmer serial killer portrait cinematic",
    "ed gein":        "Ed Gein Wisconsin criminal portrait dark cinematic",
    "ted bundy":      "Ted Bundy serial killer portrait cinematic",
    "jordan belfort": "Jordan Belfort Wall Street trader portrait cinematic",
    "lindbergh":      "Charles Lindbergh baby kidnapping 1932 historical portrait",
    "el chapo":       "El Chapo Sinaloa cartel leader portrait cinematic",
    "guzman":         "El Chapo Sinaloa cartel leader portrait cinematic",
    "charles manson": "Charles Manson cult leader portrait cinematic",
    "manson":         "Charles Manson cult leader portrait cinematic",
    "john gotti":     "John Gotti New York mafia boss portrait cinematic",
    "gotti":          "John Gotti New York mafia boss portrait cinematic",
    "leopold":        "Leopold and Loeb 1924 Chicago murder case portrait cinematic",
    "loeb":           "Leopold and Loeb 1924 Chicago murder case portrait cinematic",
    "ada":            "1999 Ada Oklahoma murder case portrait dark cinematic",
}

# Actor / character portraits — used for Image 2 (distinct from real person)
_SUBJECTS_ACTOR = {
    # Scarface
    "scarface":            "Al Pacino Tony Montana Scarface portrait cinematic",
    "tony montana":        "Al Pacino Tony Montana Scarface portrait cinematic",
    # Godfather
    "godfather":           "Marlon Brando Don Corleone Godfather portrait cinematic",
    "corleone":            "Marlon Brando Don Corleone Godfather portrait cinematic",
    # Breaking Bad
    "breaking bad":        "Bryan Cranston Walter White Breaking Bad portrait",
    "walter white":        "Bryan Cranston Walter White Breaking Bad portrait",
    # Dexter
    "dexter":              "Michael C Hall Dexter Morgan portrait dark cinematic",
    # Peaky Blinders
    "peaky blinders":      "Cillian Murphy Tommy Shelby portrait cinematic",
    "tommy shelby":        "Cillian Murphy Tommy Shelby portrait cinematic",
    # Money Heist
    "money heist":         "Alvaro Morte Professor Money Heist portrait",
    "professor":           "Alvaro Morte Professor Money Heist portrait",
    # Ozark
    "ozark":               "Jason Bateman Laura Linney Ozark portrait cinematic",
    "marty byrde":         "Jason Bateman Marty Byrde Ozark portrait cinematic",
    # Goodfellas
    "goodfellas":          "Ray Liotta Henry Hill Goodfellas portrait cinematic",
    "henry hill":          "Ray Liotta Henry Hill Goodfellas portrait cinematic",
    # Casino
    "casino":              "Robert De Niro Sam Rothstein Casino portrait cinematic",
    "sam rothstein":       "Robert De Niro Sam Rothstein Casino portrait cinematic",
    # Wolf of Wall Street
    "wolf of wall street": "Leonardo DiCaprio Jordan Belfort Wolf of Wall Street portrait cinematic",
    # American Gangster
    "american gangster":   "Denzel Washington Frank Lucas American Gangster portrait cinematic",
    # Narcos — actor version of Escobar
    "narcos":              "Wagner Moura Pablo Escobar Narcos portrait cinematic",
    "escobar":             "Wagner Moura Pablo Escobar Narcos portrait cinematic",
    "pablo":               "Wagner Moura Pablo Escobar Narcos portrait cinematic",
    # City of God
    "city of god":         "Alexandre Rodrigues Rocket City of God portrait",
    # Sicario
    "sicario":             "Emily Blunt Kate Macer Sicario portrait cinematic",
    # Griselda series
    "griselda":            "Sofia Vergara Griselda Blanco portrait cinematic",
    # El Chapo series
    "el chapo":            "Marco de la O El Chapo series portrait cinematic",
    "guzman":              "Marco de la O El Chapo series portrait cinematic",
    # Cartel/Rise
    "cartel":              "cartel leader Mexico dangerous man portrait cinematic",
    "popo":                "cartel leader Mexico dangerous man portrait cinematic",
    "rise":                "cartel leader Mexico dangerous man portrait cinematic",
    # BTK series
    "btk":                 "Rainn Wilson BTK killer portrait dark cinematic",
    "dennis rader":        "Rainn Wilson BTK killer portrait dark cinematic",
    # Dahmer series
    "dahmer":              "Evan Peters Jeffrey Dahmer portrait dark cinematic",
    "jeffrey dahmer":      "Evan Peters Jeffrey Dahmer portrait dark cinematic",
    # Wentworth
    "wentworth":           "Danielle Cormack Bea Smith Wentworth portrait",
    # Adolescence
    "adolescence":         "Stephen Graham Adolescence Netflix portrait cinematic",
    # NYPD / misc
    "nypd":                "NYPD detective 1990s New York police portrait cinematic",
}

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


def generate_image_prompts(title: str, niche: str, script: str = "", language: str = "english") -> tuple[list[str], int]:
    """Return (list_of_6_prompts, seed) for Pollinations.

    Fixed 6-slot structure — every slot is semantically distinct so images
    never duplicate subjects across the same video or across different stories
    featuring the same actor/actress:
      1. Portrait of real person / character (from _SUBJECTS_REAL)
      2. Portrait of main actor / actress   (from _SUBJECTS_ACTOR — always
         different from slot 1; falls back to a shadow/closeup variant)
      3. Real location from the story
      4. Era / time-period cinematic scene
      5. Crime / theme specific scene
      6. Justice / conclusion scene

    Each image also receives a unique seed (seed + index) so even when
    prompts cycle across a longer video, Pollinations renders a fresh image.
    """
    t = (title + " " + niche).lower()
    s = script.lower()[:500]
    suffix = "vertical 9:16 cinematic portrait dramatic lighting dark background professional 4k photography style"

    # Image 1 — Real person / character portrait
    portrait_real = next(
        (v for k, v in _SUBJECTS_REAL.items() if k in t or k in s),
        "true crime documentary mysterious person dark portrait cinematic",
    )

    # Image 2 — Actor / actress portrait (must differ from slot 1)
    portrait_actor = next(
        (v for k, v in _SUBJECTS_ACTOR.items() if k in t or k in s),
        None,
    )
    if portrait_actor is None or portrait_actor == portrait_real:
        # Variation: dramatic shadows closeup so it visually differs
        portrait_actor = portrait_real.replace("portrait", "closeup dramatic shadows intense").strip()

    # Image 3 — Real location
    location = next(
        (v for k, v in _LOCATIONS.items() if k in t or k in s),
        "dark city night street dramatic cinematic",
    )

    # Image 4 — Era / time period
    era = next(
        (v for k, v in _ERAS.items() if k in s),
        "modern dark cinematic atmospheric",
    )

    # Image 5 — Crime / theme scene
    theme = next(
        (v for k, v in _THEMES.items() if k in t or k in s),
        "crime investigation evidence board detective cinematic",
    )

    # Image 6 — Justice / conclusion
    justice = "courtroom trial verdict judge gavel dramatic justice cinematic"

    seed = random.randint(1, 99999)
    prompts = [
        f"{portrait_real}, {suffix}",
        f"{portrait_actor}, {suffix}",
        f"{location}, {suffix}",
        f"{era}, {suffix}",
        f"{theme}, {suffix}",
        f"{justice}, {suffix}",
    ]
    return prompts, seed


def generate_ai_image(prompt: str, output_path: str, seed: int = None) -> str:
    """Fetch an AI-generated image from Pollinations with retry + dark fallback."""
    import io
    from PIL import Image as PILImage

    output_path = output_path.replace(".jpg", ".png")
    encoded = requests.utils.quote(prompt)
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

    return VideoClip(frame_function=make_frame, duration=duration)


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
        clips.append(VideoClip(frame_function=fn, duration=dur))

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
                clip = clip.subclipped(0, remaining)
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
        final = final.with_audio(audio)
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


def cut_short_clip(video_path: str, video_id: str, duration: int = 55) -> str:
    """Cut the first `duration` seconds of a video and save to output/shorts/."""
    try:
        from moviepy import VideoFileClip
    except ImportError:
        return ""

    short_path = os.path.join(SHORTS_DIR, f"{video_id}_short.mp4")
    temp_audio  = os.path.join(SHORTS_DIR, f"{video_id}_short_tmp_audio.m4a")
    clip = None
    short = None
    try:
        clip = VideoFileClip(video_path)
        end = min(duration, clip.duration)
        short = clip.subclipped(0, end)
        short.write_videofile(
            short_path,
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
        # Verify output is a real video file
        size_kb = os.path.getsize(short_path) // 1024 if os.path.exists(short_path) else 0
        print(f"[Video] Short clip saved: {short_path} ({size_kb}KB)")
        if size_kb < 10:
            print(f"[Video] WARNING: short clip too small ({size_kb}KB) — may be corrupt")
        return short_path
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


# ── Main entry point ───────────────────────────────────────────────────────────

def create_video(script_data: dict, video_id: str, custom_audio_path: str = "") -> str:
    import traceback
    title    = script_data.get("title", "")
    niche    = script_data.get("niche", "")
    language = script_data.get("language", "english")
    print(f"[Video] Starting: {title} ({language})")

    # ── Voiceover ─────────────────────────────────────────────────────────────
    try:
        if custom_audio_path and Path(custom_audio_path).exists():
            enhanced_path = os.path.join(AUDIO_DIR, f"{video_id}_enhanced.mp3")
            audio_path = clean_voice(custom_audio_path, enhanced_path)
            print(f"[Video] Using custom audio: {audio_path}")
        else:
            audio_path = generate_voiceover(script_data["script"], video_id, language)
        print(f"[Video] Audio ready: {audio_path}")
    except Exception as e:
        print(f"[Video] CRASH at voiceover: {e}")
        traceback.print_exc()
        return ""

    # ── Image / clip counts ───────────────────────────────────────────────────
    is_short     = "short" in video_id
    n_variations = 2 if is_short else 4

    if is_short:
        n_images = 6
    else:
        try:
            from moviepy import AudioFileClip as _AFC
            _tmp = _AFC(audio_path)
            _dur = _tmp.duration
            _tmp.close()
            n_images = max(10, int(_dur / 60 * 6))
        except Exception:
            n_images = 10

    print(f"[Video] Generating {n_images} images x {n_variations} variations ({'short' if is_short else 'long'})")

    # ── Prompt generation ─────────────────────────────────────────────────────
    try:
        prompts, seed = generate_image_prompts(title, niche, script_data.get("script", ""), language)
        extended_prompts = [prompts[i % len(prompts)] for i in range(n_images)]
    except Exception as e:
        print(f"[Video] CRASH at prompt generation: {e}")
        traceback.print_exc()
        return ""

    # ── Image download + clip creation ────────────────────────────────────────
    all_clips: list = []
    for i, prompt in enumerate(extended_prompts):
        img_path = os.path.join(IMAGES_DIR, f"{video_id}_img_{i}.png")
        try:
            result = generate_ai_image(prompt, img_path, seed=seed + i)
        except Exception as e:
            print(f"[Video] CRASH generating image {i}: {e}")
            traceback.print_exc()
            result = None
        if result:
            try:
                variations = image_to_clips(result, n_variations=n_variations)
                all_clips.extend(variations)
                print(f"[Video] Image {i + 1}/{n_images}: {len(variations)} clips added")
            except Exception as e:
                print(f"[Video] CRASH creating clips for image {i}: {e}")
                traceback.print_exc()
        if i < len(extended_prompts) - 1:
            time.sleep(5)

    if not all_clips:
        print("[Video] No clips generated, aborting")
        return ""

    random.shuffle(all_clips)
    print(f"[Video] Total clip pool: {len(all_clips)} clips (shuffled)")

    # ── Title cards (long-form only) ──────────────────────────────────────────
    before_clips: list = []
    after_clips:  list = []
    if not is_short:
        series_label = _extract_series_from_title(title)
        if series_label:
            try:
                before_clips = [create_title_card(series_label, "The Real Story", duration=7.0)]
                after_clips  = [create_title_card("Follow for More", "Dark Crime Decoded", duration=3.0)]
                print(f"[Video] Title cards created: '{series_label}'")
            except Exception as e:
                print(f"[Video] Title card skipped: {e}")

    # ── Assembly ──────────────────────────────────────────────────────────────
    video_path = assemble_video(
        audio_path=audio_path,
        image_clips=all_clips,
        output_filename=video_id,
        before_clips=before_clips or None,
        after_clips=after_clips or None,
    )
    if video_path:
        script_data["short_clip_path"] = cut_short_clip(video_path, video_id)
    return video_path
