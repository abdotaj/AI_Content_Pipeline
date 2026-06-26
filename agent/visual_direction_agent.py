# ============================================================
#  agents/visual_direction_agent.py
#  Documentary Visual Direction Engine
#
#  Converts a narration script into a scene-by-scene visual
#  direction JSON that drives image generation, animation, and
#  video assembly.
#
#  Documentary styles:
#    NETFLIX   — moody, slow-burn true crime / investigative
#    ALJAZEERA — journalistic, map-heavy, historical authority
#    BBC       — neutral academic, archive-forward
#
#  Public API:
#    analyze_script(script_text, topic="", lang="en") -> list[dict]
#    build_pollinations_prompt(scene, identity=None)  -> str
#    detect_doc_style(topic, research="")             -> str
# ============================================================

import re
import json
import time
from typing import Optional

try:
    from config import GROQ_API_KEY
    from groq import Groq
    _groq = Groq(api_key=GROQ_API_KEY)
except Exception:
    _groq = None

# ── Documentary style definitions ─────────────────────────────────────────────

STYLES = {
    "NETFLIX": {
        "color_grade":   "desaturated teal-orange, high contrast, deep shadows, cinematic grain",
        "pacing":        "deliberate slow-burn, shock cuts at reveals",
        "font_style":    "minimal white sans-serif lower thirds, bold red accent titles",
        "music_tone":    "tense ambient drones, sparse piano, sudden silence",
        "logo_watermark": "DARK CRIME DECODED",
    },
    "ALJAZEERA": {
        "color_grade":   "neutral journalistic, warm golden highlights, crisp whites",
        "pacing":        "measured and authoritative, map reveals paced to narration",
        "font_style":    "bold Arabic + English bilingual lower thirds, channel blue-white palette",
        "music_tone":    "understated orchestral, serious investigative tension",
        "logo_watermark": "DARK CRIME DECODED — تحقيق",
    },
    "BBC": {
        "color_grade":   "natural warm, archival sepia for flashbacks, clean present-day",
        "pacing":        "academic, archive-forward, slow reveal zooms",
        "font_style":    "classic serif lower thirds, neutral white",
        "music_tone":    "minimal classical strings, period-appropriate score",
        "logo_watermark": "DARK CRIME DECODED",
    },
}

# ── Trigger keyword → style mapping ───────────────────────────────────────────

_STYLE_KEYWORDS: list[tuple[list[str], str]] = [
    (["cartel", "mafia", "serial killer", "heist", "narcos", "godfather",
      "scarface", "breaking bad", "crime lord", "murder", "kidnap"], "NETFLIX"),
    (["war", "coup", "genocide", "politician", "president", "minister",
      "conflict", "region", "al jazeera", "investigation", "corruption",
      "scandal", "middle east", "africa", "terrorism"], "ALJAZEERA"),
    (["archaeology", "ancient", "history", "empire", "dynasty", "roman",
      "biblical", "excavation", "discovery", "museum", "artifact"], "BBC"),
]

# ── Shot type library ─────────────────────────────────────────────────────────

SHOT_TYPES = {
    # format: shot_code -> (label, description)
    "ECU":  ("Extreme Close-Up", "Eyes, single object detail, evidence item"),
    "CU":   ("Close-Up", "Face, hands, document, weapon, key object"),
    "MCU":  ("Medium Close-Up", "Head and shoulders, person speaking"),
    "MS":   ("Medium Shot", "Waist up, person in environment"),
    "MLS":  ("Medium Long Shot", "Full body, person + immediate surroundings"),
    "LS":   ("Long Shot", "Person dwarfed by environment"),
    "ELS":  ("Extreme Long Shot", "Landscape, city aerial, establishing geography"),
    "OTS":  ("Over-the-Shoulder", "Two-person confrontation, interrogation"),
    "POV":  ("Point of View", "First-person subjective — victim, detective"),
    "INS":  ("Insert", "Close detail cutaway — clock, map, document, photo"),
    "AER":  ("Aerial / Drone", "Wide geography, urban sprawl, escape route"),
    "TILT": ("Tilt", "Power dynamic — low angle authority / high angle victim"),
}

# ── Visual source hierarchy ───────────────────────────────────────────────────

VISUAL_SOURCES = [
    "archive_photo",       # real historical photographs
    "documentary_footage", # existing documentary video clips
    "news_footage",        # broadcast news archival
    "cctv_footage",        # surveillance / security camera
    "court_footage",       # trial / hearing video
    "satellite_image",     # Google Earth / overhead
    "map_animation",       # animated geographic map
    "newspaper_headline",  # scanned front pages
    "magazine_cover",      # period publications
    "motion_graphic",      # animated infographic / timeline
    "timeline_visual",     # chronological event display
    "text_overlay",        # on-screen text / title card
    "ai_generated",        # Pollinations AI image
    "reenactment",         # dramatic reconstruction
    "talking_portrait",    # animated real portrait
    "book_cover",          # published accounts
    "social_media",        # tweet / post screenshot
    "phone_record",        # call log / message visual
]

# ── Mood → motion effect mapping ─────────────────────────────────────────────

MOOD_EFFECTS: dict[str, list[str]] = {
    "tense":      ["slow_zoom_in", "handheld_shake", "flash_cut"],
    "shocking":   ["smash_cut", "freeze_frame", "white_flash", "speed_ramp"],
    "sad":        ["slow_zoom_out", "cross_dissolve", "vignette_fade"],
    "mysterious": ["slow_zoom_in", "rack_focus", "shadow_wipe"],
    "triumphant": ["crane_up", "wide_reveal", "music_swell"],
    "dark":       ["dolly_in", "low_angle_tilt", "shadow_push"],
    "urgent":     ["quick_cut", "whip_pan", "jump_cut"],
    "nostalgic":  ["slow_dissolve", "sepia_fade", "film_grain_overlay"],
    "hopeful":    ["slow_crane_up", "golden_hour_glow", "soft_dissolve"],
    "neutral":    ["standard_cut", "gentle_zoom"],
}

# ── Edit speed mapping ────────────────────────────────────────────────────────

EDIT_SPEED_MAP: dict[str, str] = {
    "tense":      "fast (1.5–3s per cut)",
    "shocking":   "very fast (0.5–1.5s), freeze at peak",
    "sad":        "slow (5–8s per cut)",
    "mysterious": "medium-slow (3–5s per cut)",
    "triumphant": "medium (2–4s per cut)",
    "dark":       "slow (4–7s per cut)",
    "urgent":     "very fast (0.5–2s per cut)",
    "nostalgic":  "slow (6–10s per cut)",
    "hopeful":    "medium (3–5s per cut)",
    "neutral":    "medium (2–4s per cut)",
}

# ── System prompt ─────────────────────────────────────────────────────────────

_VD_SYSTEM_PROMPT = """You are a senior documentary director and editor — your credits include Netflix true crime series, Al Jazeera investigative documentaries, and BBC historical films.

Your task: analyze the narration script provided and return a scene-by-scene visual direction plan as a strict JSON array.

CORE PHILOSOPHY:
- Every visual MUST complement and extend the narration — never contradict or ignore it.
- Visuals must be historically accurate and emotionally appropriate.
- No generic stock imagery. Every scene has a specific, purposeful visual.
- Pacing is as important as content — slow reveals for mystery, hard cuts for shock.

DOCUMENTARY STYLE MODES:
NETFLIX — true crime, dark cinematography, teal-orange grade, slow burn revelations, shock cuts at peak moments
ALJAZEERA — journalistic authority, map-heavy, bilingual lower thirds, measured pacing, source attribution
BBC — archive-forward, academic, warm natural light for history, clean contemporary for present

SCENE DETECTION RULES:
- Split at every [SECTION:], paragraph break, or major narrative shift
- A "scene" = one coherent visual moment (5–20 seconds of screen time)
- Minimum 1 scene per 50 words of narration

VISUAL SOURCE PRIORITY (use first available):
1. archive_photo — real photographs from the era/event
2. documentary_footage — existing real footage
3. news_footage — broadcast archival
4. cctv_footage — when surveillance is mentioned
5. court_footage — when trial/verdict is mentioned
6. satellite_image + map_animation — when location/geography matters
7. newspaper_headline — when media coverage is narrated
8. reenactment — when no real footage exists
9. ai_generated — atmosphere shots between real assets
10. motion_graphic / timeline_visual — for facts, dates, sequences

MANDATORY TRIGGER RULES:

IF narration mentions WAR / COUP / BATTLE / BOMBING / ATTACK:
→ Shot type: AER (aerial) or ELS
→ Edit speed: FAST
→ Visual: archive_photo + news_footage + map_animation
→ Transition: smash_cut
→ Motion: speed_ramp into freeze_frame

IF narration mentions DEATH / MURDER / EXECUTION:
→ Edit speed: VERY SLOW
→ Camera: slow_zoom_in to ECU
→ Visual: archive_photo → newspaper_headline → talking_portrait
→ Transition: cross_dissolve with vignette
→ Motion: slow_zoom_in + shadow_push

IF narration mentions SCANDAL / CORRUPTION / EVIDENCE / ARREST:
→ Shot: INSERT of document / photo / weapon
→ Edit speed: medium then FAST at reveal
→ Visual: newspaper_headline + court_footage + archive_photo
→ Motion: rack_focus → smash_cut

IF narration mentions a PERSON (real or fictional):
→ Always include: talking_portrait or archive_photo
→ Shot: MCU or MS — never cut off body awkwardly
→ Maintain consistent face/identity throughout episode

IF narration mentions a LOCATION / COUNTRY / CITY:
→ Always add: map_animation + satellite_image + archive_photo of location
→ Shot: ELS → AER → MS (wide to close)
→ Lower third text overlay: location name + date

IF content is MYSTERIOUS / PSYCHOLOGICAL:
→ Edit speed: slow (4–6s per cut)
→ Motion: slow_zoom_in + rack_focus
→ Color hint: desaturated, vignette
→ Sound design note: ambient drones, silence

IF content is SHOCKING / SURPRISING / REVEAL:
→ Edit speed: fast cut then freeze
→ Motion: speed_ramp → freeze_frame → slow_zoom_in
→ White flash or smash_cut transition

IF content is SAD / TRAGIC / MOURNING:
→ Edit speed: very slow (6–10s)
→ Motion: slow_zoom_out + vignette_fade
→ Transition: cross_dissolve
→ Color hint: desaturated, cool blue tones

OUTPUT FORMAT — return ONLY a valid JSON array with this schema for every scene:

[
  {
    "Scene_Number": 1,
    "Timestamp": "00:00:00",
    "Duration_Seconds": 8,
    "Narration_Excerpt": "exact words from script for this scene",
    "Mood": "tense|shocking|sad|mysterious|dark|urgent|nostalgic|hopeful|neutral|triumphant",
    "Emotional_Intensity": 7,
    "Documentary_Style": "NETFLIX|ALJAZEERA|BBC",
    "Shot_Type": "ECU|CU|MCU|MS|MLS|LS|ELS|OTS|POV|INS|AER|TILT",
    "Camera_Angle": "eye_level|low_angle|high_angle|dutch_tilt|overhead|pov",
    "Camera_Mode": "static|dolly_in|dolly_out|pan_left|pan_right|tilt_up|tilt_down|handheld|crane_up|rack_focus|slow_zoom_in|slow_zoom_out",
    "Visual_Description": "Precise, specific visual — who/what/where/when. No generics.",
    "Visual_Sources": ["archive_photo", "map_animation"],
    "Edit_Speed": "very_fast (0.5–1.5s)|fast (1.5–3s)|medium (2–4s)|medium_slow (3–5s)|slow (5–8s)|very_slow (6–10s)",
    "Transition": "hard_cut|smash_cut|cross_dissolve|whip_pan|speed_ramp|freeze_frame|white_flash|fade_black|wipe_left|shadow_wipe|vignette_fade",
    "Motion_Effects": ["slow_zoom_in", "vignette"],
    "Color_Grade_Hint": "teal-orange desaturated|warm sepia|cool blue|natural warm|black and white|golden hour",
    "Text_Overlays": [
      {"type": "lower_third", "content": "Name — Title, Year"},
      {"type": "location_tag", "content": "City, Country — Year"},
      {"type": "fact_card", "content": "Key statistic or quote"}
    ],
    "Sound_Design": "Brief description of audio texture — ambient, music tone, silence use",
    "Search_Keywords": ["specific search terms for archival asset retrieval"],
    "Pollinations_Prompt": "Detailed AI image generation prompt for this scene if ai_generated is in Visual_Sources"
  }
]

CRITICAL RULES:
- Return ONLY the JSON array — no prose, no markdown fences, no explanation.
- Every scene must have at least 2 Visual_Sources.
- Search_Keywords must be specific enough to find real archival assets.
- Pollinations_Prompt must include: subject + location + era + mood + lighting + style + "9:16 vertical cinematic documentary".
- Duration_Seconds must match audio pacing (slow mood = longer duration).
- Do NOT invent facts. If you are uncertain about a visual, note it in Search_Keywords.
- LANGUAGE RULE (non-negotiable): Search_Keywords and Pollinations_Prompt MUST always be written in ENGLISH, even when the narration is in Arabic. Image generation APIs (Pollinations, Pexels, Pixabay) only understand English. Translate all Arabic names, places, and events to their English equivalents in these two fields.
"""


# ── Style detector ─────────────────────────────────────────────────────────────

def detect_doc_style(topic: str, research: str = "") -> str:
    """Return the best-fit documentary style for this topic."""
    combined = (topic + " " + research).lower()
    for keywords, style in _STYLE_KEYWORDS:
        if any(kw in combined for kw in keywords):
            return style
    return "NETFLIX"


# ── Timestamp utilities ────────────────────────────────────────────────────────

def _estimate_duration(text: str, wpm: int = 130) -> int:
    """Estimate voiceover duration in seconds from word count."""
    words = len(text.split())
    return max(4, round((words / wpm) * 60))


def _seconds_to_ts(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ── Script splitter ───────────────────────────────────────────────────────────

def _split_into_chunks(script_text: str) -> list[str]:
    """
    Split a script into narration chunks at [SECTION:] markers,
    paragraph breaks, or every ~80 words if no breaks exist.
    """
    # Normalize line endings
    text = script_text.replace("\r\n", "\n").strip()

    # Split at [SECTION:...] markers first
    section_parts = re.split(r"\[SECTION:[^\]]*\]", text, flags=re.IGNORECASE)

    chunks = []
    for part in section_parts:
        part = part.strip()
        if not part:
            continue
        # Split long parts further at paragraph breaks
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", part) if p.strip()]
        for para in paragraphs:
            words = para.split()
            if len(words) <= 100:
                chunks.append(para)
            else:
                # Break into ~80-word segments
                for i in range(0, len(words), 80):
                    seg = " ".join(words[i:i+80])
                    if seg:
                        chunks.append(seg)

    return chunks or [text]


# ── Groq call with retry ───────────────────────────────────────────────────────

def _groq_call(messages: list[dict], max_tokens: int = 4000) -> str:
    """Call Groq with retry on rate limit. Returns raw response text."""
    if _groq is None:
        raise RuntimeError("Groq client not initialized — check GROQ_API_KEY")

    models = ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"]
    last_err = None
    for model in models:
        for attempt in range(2):
            try:
                time.sleep(2)
                resp = _groq.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=0.3,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                if "rate" in err_str or "429" in err_str:
                    wait = 45 if attempt == 0 else 0
                    if wait:
                        print(f"[VD] Rate limit — waiting {wait}s...")
                        time.sleep(wait)
                    continue
                break  # non-rate error → try next model
    raise last_err


# ── JSON extractor ────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> list[dict]:
    """Extract the first JSON array from a raw LLM response."""
    # Strip markdown fences
    raw = re.sub(r"```(?:json)?", "", raw).strip()

    # Find array boundaries
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("No JSON array found in response")

    return json.loads(raw[start:end+1])


# ── Pollinations prompt builder ───────────────────────────────────────────────

def build_pollinations_prompt(scene: dict, identity: Optional[dict] = None) -> str:
    """
    Build a Pollinations AI image generation prompt from a scene dict.
    Falls back gracefully if Pollinations_Prompt is missing.
    """
    # Use LLM-generated prompt if available and non-empty
    existing = (scene.get("Pollinations_Prompt") or "").strip()
    if existing and len(existing) > 20:
        return existing

    # Build from components
    parts = []

    desc = scene.get("Visual_Description", "")
    mood = scene.get("Mood", "dark")
    style = scene.get("Documentary_Style", "NETFLIX")
    shot = scene.get("Shot_Type", "MS")
    grade = scene.get("Color_Grade_Hint", "desaturated cinematic")

    if identity and identity.get("name"):
        parts.append(f"{identity['name']},")

    if desc:
        parts.append(desc)

    # Shot type → natural language
    shot_labels = {
        "ECU": "extreme close-up", "CU": "close-up shot", "MCU": "medium close-up",
        "MS": "medium shot", "MLS": "medium long shot", "LS": "long establishing shot",
        "ELS": "extreme wide establishing shot", "AER": "aerial drone shot",
        "OTS": "over-the-shoulder shot", "POV": "first-person POV shot",
        "INS": "insert detail close-up", "TILT": "low-angle tilt shot",
    }
    parts.append(shot_labels.get(shot, "cinematic shot"))

    # Style → atmosphere
    style_atm = {
        "NETFLIX": "dark cinematic, teal-orange color grade, high contrast, film grain",
        "ALJAZEERA": "journalistic documentary, neutral warm tones, sharp clarity",
        "BBC": "natural warm light, archival quality, period accurate",
    }
    parts.append(style_atm.get(style, "cinematic documentary"))

    # Mood → lighting
    mood_light = {
        "tense": "dramatic side lighting, deep shadows",
        "shocking": "harsh overhead light, stark contrast",
        "sad": "soft blue-grey diffused light, cool tones",
        "mysterious": "dim atmospheric light, vignette, mystery",
        "dark": "low-key noir lighting, deep black shadows",
        "urgent": "harsh flash-like lighting, high contrast",
        "nostalgic": "warm sepia golden tones, soft focus",
        "neutral": "balanced documentary lighting",
    }
    parts.append(mood_light.get(mood, "cinematic documentary lighting"))
    parts.append(grade)
    parts.append("9:16 vertical format, ultra-realistic documentary photography")

    return ", ".join(p for p in parts if p)


# ── Main analysis function ─────────────────────────────────────────────────────

def analyze_script(
    script_text: str,
    topic: str = "",
    lang: str = "en",
    research: str = "",
    doc_style: Optional[str] = None,
    batch_size: int = 8,
) -> list[dict]:
    """
    Analyze a narration script and return a list of visual direction scenes.

    Args:
        script_text: The full narration (English or Arabic).
        topic:       Episode topic (used for style detection).
        lang:        "en" or "ar" (affects prompt language note).
        research:    Research text (helps style detection).
        doc_style:   Override detected style ("NETFLIX"|"ALJAZEERA"|"BBC").
        batch_size:  Number of chunks per Groq call (keep ≤ 8 to stay in token limit).

    Returns:
        List of scene dicts matching the enhanced visual direction schema.
    """
    style = doc_style or detect_doc_style(topic, research)
    style_info = STYLES.get(style, STYLES["NETFLIX"])

    print(f"[VD] Documentary style: {style}")
    print(f"[VD] Topic: {topic}")

    chunks = _split_into_chunks(script_text)
    print(f"[VD] Script split into {len(chunks)} chunks")

    all_scenes: list[dict] = []
    scene_counter = 1
    running_seconds = 0

    # Process in batches to stay within Groq token limits
    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]
        batch_text = "\n\n---\n\n".join(batch)

        user_message = f"""DOCUMENTARY STYLE: {style}
COLOR GRADE: {style_info['color_grade']}
PACING: {style_info['pacing']}
FONT STYLE: {style_info['font_style']}
WATERMARK: {style_info['logo_watermark']}
TOPIC: {topic}
LANGUAGE NOTE: {"Arabic narration — transliterate locations to English for Search_Keywords" if lang == "ar" else "English narration"}

SCRIPT TO ANALYZE:
{batch_text}

Return the visual direction JSON array for ALL narration above.
Scene numbering starts at {scene_counter}.
First scene timestamp starts at {_seconds_to_ts(running_seconds)}."""

        try:
            raw = _groq_call(
                messages=[
                    {"role": "system", "content": _VD_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
                max_tokens=4000,
            )
            batch_scenes = _extract_json(raw)
        except Exception as e:
            print(f"[VD] Batch {batch_start//batch_size + 1} failed: {e}")
            # Fallback: generate minimal scenes from chunks
            batch_scenes = _fallback_scenes(batch, scene_counter, running_seconds, style, topic)

        # Re-number and re-timestamp scenes sequentially
        for scene in batch_scenes:
            scene["Scene_Number"] = scene_counter
            scene["Timestamp"] = _seconds_to_ts(running_seconds)

            # Ensure duration is estimated if missing
            if not scene.get("Duration_Seconds"):
                narr = scene.get("Narration_Excerpt", "")
                scene["Duration_Seconds"] = _estimate_duration(narr) if narr else 6

            # Ensure Pollinations_Prompt exists
            if "ai_generated" in scene.get("Visual_Sources", []) and not scene.get("Pollinations_Prompt"):
                scene["Pollinations_Prompt"] = build_pollinations_prompt(scene)

            # Safety: strip any Arabic that leaked into API-facing fields
            scene = _sanitize_api_fields(scene, topic or "documentary")

            running_seconds += scene["Duration_Seconds"]
            scene_counter += 1

        all_scenes.extend(batch_scenes)
        print(f"[VD] Processed batch → {len(all_scenes)} scenes total")

    print(f"[VD] Complete: {len(all_scenes)} scenes, ~{running_seconds}s total")
    return all_scenes


# ── Fallback scene generator ──────────────────────────────────────────────────

def _fallback_scenes(
    chunks: list[str],
    start_number: int,
    start_seconds: int,
    style: str,
    topic: str,
) -> list[dict]:
    """Generate minimal but valid scenes when the LLM call fails."""
    scenes = []
    sec = start_seconds
    for i, chunk in enumerate(chunks):
        mood = _detect_mood(chunk)
        duration = _estimate_duration(chunk)
        scenes.append({
            "Scene_Number":      start_number + i,
            "Timestamp":         _seconds_to_ts(sec),
            "Duration_Seconds":  duration,
            "Narration_Excerpt": chunk[:200],
            "Mood":              mood,
            "Emotional_Intensity": 5,
            "Documentary_Style": style,
            "Shot_Type":         "MS",
            "Camera_Angle":      "eye_level",
            "Camera_Mode":       "slow_zoom_in",
            "Visual_Description": f"Documentary scene for: {chunk[:100]}",
            "Visual_Sources":    ["ai_generated", "archive_photo"],
            "Edit_Speed":        EDIT_SPEED_MAP.get(mood, "medium (2–4s per cut)"),
            "Transition":        "cross_dissolve",
            "Motion_Effects":    MOOD_EFFECTS.get(mood, ["slow_zoom_in"]),
            "Color_Grade_Hint":  STYLES[style]["color_grade"],
            "Text_Overlays":     [],
            "Sound_Design":      "ambient documentary underscore",
            "Search_Keywords":   [topic] + chunk.split()[:5],
            "Pollinations_Prompt": (
                f"{chunk[:80]}, {topic}, documentary cinematic, "
                f"dark atmospheric, 9:16 vertical"
            ),
        })
        sec += duration
    return scenes


def _has_arabic(text: str) -> bool:
    """True if the string contains Arabic Unicode characters."""
    return any('؀' <= ch <= 'ۿ' for ch in text)


def _sanitize_api_fields(scene: dict, topic: str) -> dict:
    """
    Ensure Search_Keywords and Pollinations_Prompt are in English.
    If Arabic characters are detected in either field, replace with
    a safe English fallback derived from the topic and scene mood.
    """
    mood  = scene.get("Mood", "dark")
    style = scene.get("Documentary_Style", "NETFLIX")

    # Search_Keywords
    keywords = scene.get("Search_Keywords", [])
    if any(_has_arabic(k) for k in keywords):
        scene["Search_Keywords"] = [topic, mood, "documentary", "archive photo"]

    # Pollinations_Prompt
    prompt = scene.get("Pollinations_Prompt", "")
    if _has_arabic(prompt):
        style_atm = {
            "NETFLIX": "dark cinematic teal-orange grade film grain",
            "ALJAZEERA": "journalistic documentary warm tones",
            "BBC": "natural warm archival quality",
        }.get(style, "cinematic documentary")
        scene["Pollinations_Prompt"] = (
            f"{topic} documentary scene, {mood} atmosphere, {style_atm}, "
            "9:16 vertical cinematic documentary ultra-realistic"
        )
    return scene


def _detect_mood(text: str) -> str:
    """Simple keyword-based mood detector for fallback scenes."""
    t = text.lower()
    if any(w in t for w in [
        "killed", "dead", "murder", "death", "shot", "stabbed",
        "bullet", "executed", "assassinated", "slain", "massacre",
        "found dead", "body found", "ended on",
    ]):
        return "dark"
    if any(w in t for w in [
        "shocking", "revealed", "discovered", "suddenly", "secret",
        "no one knew", "nobody knew", "surprised", "confessed",
    ]):
        return "shocking"
    if any(w in t for w in ["sad", "grief", "mourning", "loss", "tragedy", "tears"]):
        return "sad"
    if any(w in t for w in [
        "mystery", "unknown", "disappeared", "strange", "clue",
        "vanished", "unsolved", "suspected", "alleged",
    ]):
        return "mysterious"
    if any(w in t for w in ["escape", "chase", "urgent", "immediately", "ran", "fled"]):
        return "urgent"
    return "tense"


# ── Save helper ───────────────────────────────────────────────────────────────

def save_visual_direction(scenes: list[dict], output_path: str) -> None:
    """Save scene list to a JSON file."""
    import os
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scenes, f, ensure_ascii=False, indent=2)
    print(f"[VD] Saved {len(scenes)} scenes → {output_path}")


# ── CLI entry point for standalone testing ─────────────────────────────────────

if __name__ == "__main__":
    import sys, pathlib

    if len(sys.argv) < 2:
        print("Usage: python -m agents.visual_direction_agent <script_file.txt> [topic] [en|ar]")
        sys.exit(1)

    script_path = sys.argv[1]
    topic_arg   = sys.argv[2] if len(sys.argv) > 2 else "true crime documentary"
    lang_arg    = sys.argv[3] if len(sys.argv) > 3 else "en"

    script_text = pathlib.Path(script_path).read_text(encoding="utf-8")
    scenes = analyze_script(script_text, topic=topic_arg, lang=lang_arg)

    out_path = script_path.replace(".txt", "_visual_direction.json")
    save_visual_direction(scenes, out_path)
    print(f"\nDone — {len(scenes)} scenes written to {out_path}")
