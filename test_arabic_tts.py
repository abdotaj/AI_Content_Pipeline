# ============================================================
#  test_arabic_tts.py  —  Local Arabic TTS chain test
#
#  Tests:
#    1. upgrade_arabic_script()  — 6-step Groq prompt
#    2. apply_mishkal_tashkeel() — selective diacritics
#    3. preprocess_arabic_tts()  — pronunciation fixes + number expansion
#    4. generate_voiceover()     — OpenAI TTS (nova) → edge-tts fallback
#
#  Output: output/test_arabic_tts.mp3
#  Run:    python test_arabic_tts.py
# ============================================================

import os
import sys
import time

# Force UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(__file__))

# Patch 'config' to darkcrimed settings before any agent import
import config_darkcrimed
sys.modules["config"] = config_darkcrimed

from dotenv import load_dotenv
load_dotenv()

# ── Short Arabic test script ──────────────────────────────────────────────────
# Deliberately includes:
#   - An ambiguous word (عِلم / عَلَم)
#   - A hard Arabic name (حُمَيْدتي)
#   - A foreign name/group (Wagner Group)
#   - A number that needs expanding (2019)
#   - A sentence with awkward spoken rhythm

TEST_SCRIPT = """
في عام 2019، كان السودان على شفا الانهيار.

رجل واحد كان يمسك بخيوط اللعبة من الخلف. اسمه محمد حمدان دقلو، المعروف بـ حميدتي.
قائد قوات الدعم السريع. رجل نشأ في البادية، وأصبح أقوى من الدولة نفسها.

في الشمال، كانت قوات الجيش تتقدم. في الجنوب، كانت ميليشيات Wagner Group تنتشر.
وفي الخرطوم، كانت المدينة تحترق.

الجميع كان يعرف ان الحرب قادمة. لكن احدا لم يتوقع ما جاء بعد ذلك.
"""

SEP = "─" * 60


def show(label: str, text: str):
    print(f"\n{SEP}")
    print(f"  {label}")
    print(SEP)
    print(text[:600])
    if len(text) > 600:
        print(f"  ... [{len(text)} chars total]")


def main():
    print("\n" + "═" * 60)
    print("  Arabic TTS Chain Test")
    print("═" * 60)
    show("ORIGINAL SCRIPT", TEST_SCRIPT)

    # ── Step 1: 6-step Arabic upgrade prompt ─────────────────────
    print(f"\n[1/4] Running upgrade_arabic_script() via Groq...")
    t0 = time.time()
    from agents.script_agent import upgrade_arabic_script
    upgraded = upgrade_arabic_script(TEST_SCRIPT)
    print(f"      Done in {time.time()-t0:.1f}s")
    show("AFTER upgrade_arabic_script()", upgraded)

    # ── Step 2: Mishkal selective tashkeel ───────────────────────
    print(f"\n[2/4] Running apply_mishkal_tashkeel()...")
    from agents.script_agent import apply_mishkal_tashkeel
    tashkeeled = apply_mishkal_tashkeel(upgraded)
    show("AFTER apply_mishkal_tashkeel()", tashkeeled)

    # ── Step 3: Arabic TTS preprocessing ─────────────────────────
    print(f"\n[3/4] Running preprocess_arabic_tts()...")
    from agents.video_agent import preprocess_arabic_tts, _apply_arabic_pronunciation
    pronounced = _apply_arabic_pronunciation(tashkeeled)
    final_text = preprocess_arabic_tts(pronounced)
    show("AFTER preprocess_arabic_tts()", final_text)

    # ── Step 4: TTS audio generation ─────────────────────────────
    print(f"\n[4/4] Generating TTS audio...")
    # generate_voiceover() writes to AUDIO_DIR/<stem>.mp3
    # AUDIO_DIR = output/dark_crime/audio  (from config_darkcrimed)
    audio_dir = "output/dark_crime/audio"
    os.makedirs(audio_dir, exist_ok=True)
    file_stem = "test_arabic_tts"  # no path, no extension — function adds both

    from agents.video_agent import generate_voiceover
    result = generate_voiceover(final_text, file_stem, language="arabic")

    print(f"\n{'═'*60}")
    if result and os.path.exists(result):
        size_kb = os.path.getsize(result) // 1024
        print(f"  SUCCESS — audio saved: {result} ({size_kb} KB)")
        print(f"  Play it to verify Arabic TTS quality.")
    else:
        # Try the expected path directly
        expected = os.path.join(audio_dir, f"{file_stem}.mp3")
        if os.path.exists(expected):
            size_kb = os.path.getsize(expected) // 1024
            print(f"  SUCCESS — audio saved: {expected} ({size_kb} KB)")
        else:
            print(f"  FAILED — no audio file generated.")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
