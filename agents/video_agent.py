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
    VIDEO_WIDTH, VIDEO_HEIGHT,
    SHORT_VIDEO_DURATION, LONG_VIDEO_DURATION,
    EDGETTS_RATE,
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
        communicate = edge_tts.Communicate(script_text, voice, rate=EDGETTS_RATE)
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
    "charles manson":   "Charles Manson cult leader portrait dark cinematic",
    "manson":           "Charles Manson cult leader portrait dark cinematic",
    "lucky luciano":    "Lucky Luciano New York mafia boss portrait cinematic",
    "luciano":          "Lucky Luciano New York mafia portrait cinematic",
    "frank lucas":      "Frank Lucas real Harlem drug lord portrait cinematic dramatic dark",
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
    "american gangster":   "Denzel Washington and Russell Crowe American Gangster portrait cinematic",

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

    # Special case: Godfather always returns both Brando + Pacino
    if "godfather" in title_lower:
        return [
            "Marlon Brando as Vito Corleone Godfather portrait cinematic",
            "Al Pacino as Michael Corleone portrait cinematic",
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


def generate_image_prompts(title: str, niche: str, script: str = "", language: str = "english") -> tuple[list[str], int]:
    """Return (list_of_12_prompts, seed) for Pollinations.

    12-slot structure:
      1.  Real criminal portrait (AI — Wikipedia real photo handled separately)
      2.  Actor / series portrait
      3.  Real location from the story
      4.  Era / time-period scene
      5.  Crime / theme scene
      6.  Justice / conclusion scene
      7.  Alternate portrait (extreme closeup, different angle)
      8.  Vintage newspaper / historical archive
      9.  Investigation board / evidence room
      10. Second atmospheric location
      11. Actor alternate (dramatic intensity)
      12. Final courtroom / verdict scene
    """
    t      = (title + " " + niche).lower()
    s      = script.lower()[:800]
    suffix = "vertical 9:16 cinematic portrait dramatic lighting dark background professional 4k photography style"

    # Slots 1 & 2 — portraits
    portraits  = extract_main_subject(title, script)
    portrait_1 = portraits[0]
    portrait_2 = portraits[1] if len(portraits) >= 2 else portrait_1.replace("portrait", "closeup dramatic shadows intense").strip()

    # Slot 3 — Real location
    location = next(
        (v for k, v in _LOCATIONS.items() if k in t or k in s),
        "dark city night street dramatic cinematic",
    )

    # Slot 4 — Era / time period
    era = next(
        (v for k, v in _ERAS.items() if k in s),
        "modern dark cinematic atmospheric",
    )

    # Slot 5 — Crime / theme scene
    theme = next(
        (v for k, v in _THEMES.items() if k in t or k in s),
        "crime investigation evidence board detective cinematic",
    )

    # Slot 6 — Justice
    justice = "courtroom trial verdict judge gavel dramatic justice cinematic"

    # Slot 7 — Alternate portrait (extreme closeup)
    portrait_alt = portrait_1.replace("portrait", "extreme closeup dramatic shadows intense gaze").strip()

    # Slot 8 — Historical archive / newspaper
    newspaper = "vintage newspaper front page crime headline archive 1920s sepia dramatic cinematic"

    # Slot 9 — Investigation / evidence board
    evidence = "crime investigation evidence board detective newspaper clippings red string dark cinematic"

    # Slot 10 — Second atmospheric location
    second_location = "dark alley night rain atmospheric crime city street cinematic"

    # Slot 11 — Actor alternate angle
    actor_alt = portrait_2.replace("portrait", "dramatic intensity extreme closeup").strip()

    # Slot 12 — Final verdict
    verdict = "court verdict guilty judge gavel justice served historic dramatic cinematic"

    seed = random.randint(1, 99999)
    prompts = [
        f"{portrait_1}, {suffix}",     # 1
        f"{portrait_2}, {suffix}",     # 2
        f"{location}, {suffix}",       # 3
        f"{era}, {suffix}",            # 4
        f"{theme}, {suffix}",          # 5
        f"{justice}, {suffix}",        # 6
        f"{portrait_alt}, {suffix}",   # 7
        f"{newspaper}, {suffix}",      # 8
        f"{evidence}, {suffix}",       # 9
        f"{second_location}, {suffix}",# 10
        f"{actor_alt}, {suffix}",      # 11
        f"{verdict}, {suffix}",        # 12
    ]
    return prompts, seed


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


def cut_short_clip(video_path: str, video_id: str, duration: int = SHORT_VIDEO_DURATION) -> str:
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


# ── Main entry point ───────────────────────────────────────────────────────────

def create_video(script_data: dict, video_id: str, custom_audio_path: str = "", user_images: list | None = None) -> str:
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

    # ── Wikipedia real photo + user uploads (priority images) ────────────────
    # Fetch Wikipedia photo for the real person; combine with user uploads.
    # get_person_images() returns dicts compatible with _build_clip_pool_with_user_images.
    person_name = _extract_person_name_from_topic(title, script_data.get("topic", ""))
    priority_images = get_person_images(person_name, video_id, user_images)

    # Merge priority images into clip pool at script-matched positions
    script_text = script_data.get("script", "")
    all_clips = _build_clip_pool_with_user_images(
        priority_images, all_clips, script_text, n_variations
    )
    print(f"[Video] Total clip pool: {len(all_clips)} clips")

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
