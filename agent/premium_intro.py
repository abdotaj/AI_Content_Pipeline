"""
premium_intro.py — 3-second cinematic intro for Dark Crime Decoded.

Renders 1080×1920 frames with Pillow, encodes via ffmpeg pipe.

Usage:
    from agents.premium_intro import create_intro, prepend_intro
    intro = create_intro("output/dark_crime/final/intro.mp4")
    video  = prepend_intro(intro, video_path)   # replaces video_path in-place
"""

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

# ── Visual constants ──────────────────────────────────────────────────────────
_W, _H   = 1080, 1920
_FPS     = 30
_DUR     = 3.0
_FRAMES  = int(_FPS * _DUR)          # 90 frames

_BG      = (8,   8,  10)             # near-black
_CRIMSON = (180,  10,  30)           # brand crimson
_CRIMSON_DIM = (90, 5, 15)          # glow / shadow crimson
_WHITE   = (255, 255, 255)
_GRAY    = (155, 155, 160)           # subtitle gray

# ── Font lookup (cached) ──────────────────────────────────────────────────────
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/seguibl.ttf",
]


@lru_cache(maxsize=16)
def _font(size: int) -> "ImageFont.FreeTypeFont":
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    print("[Intro] No TTF font found — using PIL default (reduced quality)")
    return ImageFont.load_default()


# ── Animation helpers ─────────────────────────────────────────────────────────
def _ease(t: float) -> float:
    """Smooth ease-in-out (cubic Hermite)."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _anim(t: float, start: float, dur: float) -> float:
    """Normalised [0,1] progress for an element that starts at `start` and takes `dur`."""
    return _ease((t - start) / dur) if dur > 0 else (1.0 if t >= start else 0.0)


def _text_center(draw: "ImageDraw.Draw", text: str, font, cy: int,
                 fill: tuple, offset_y: int = 0) -> None:
    """Draw text horizontally centered at vertical position cy + offset_y."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw   = bbox[2] - bbox[0]
    tx   = (_W - tw) // 2
    ty   = cy - (bbox[3] - bbox[1]) // 2 + offset_y
    draw.text((tx, ty), text, font=font, fill=fill)


# ── Frame renderer ────────────────────────────────────────────────────────────
def _render_frame(fi: int) -> bytes:
    """
    Return raw RGB bytes for frame `fi`.

    Timeline (t = fi / _FRAMES, range 0-1 maps to 0-3 s):
      0.00–0.08  : fade in from black
      0.10–0.35  : crimson line sweeps center→ edges
      0.28–0.55  : "DARK CRIME" slides up, fades in
      0.45–0.70  : "DECODED" slides up, fades in (crimson)
      0.62–0.80  : "True Crime Documentary" fades in (gray)
      0.90–1.00  : fade to black
    """
    t   = fi / _FRAMES
    cy  = _H // 2             # vertical midpoint
    line_y = cy + 60          # accent line sits just below center

    # Global fade (applies to entire canvas)
    if t < 0.08:
        gf = _ease(t / 0.08)
    elif t > 0.90:
        gf = _ease((1.0 - t) / 0.10)
    else:
        gf = 1.0

    canvas = Image.new("RGBA", (_W, _H), (*_BG, 255))
    draw   = ImageDraw.Draw(canvas)

    # ── Crimson accent line ───────────────────────────────────────────────────
    la   = _anim(t, 0.10, 0.25)
    half = int((_W // 2) * la)
    if half > 0:
        a_line = int(255 * gf)
        cx = _W // 2
        draw.rectangle([cx - half, line_y,     cx + half, line_y + 4],
                       fill=(*_CRIMSON, a_line))
        draw.rectangle([cx - half, line_y + 5, cx + half, line_y + 6],
                       fill=(*_CRIMSON_DIM, a_line // 2))

    # ── "DARK CRIME" ──────────────────────────────────────────────────────────
    ta = _anim(t, 0.28, 0.27)
    if ta > 0:
        a  = int(255 * ta * gf)
        oy = int(28 * (1.0 - ta))
        f  = _font(128)

        # Crimson glow (offset copies behind main text)
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            _text_center(draw, "DARK CRIME", f,
                         line_y - 165, (*_CRIMSON_DIM, a // 3), oy + dy)

        _text_center(draw, "DARK CRIME", f,
                     line_y - 165, (*_WHITE, a), oy)

    # ── "DECODED" ─────────────────────────────────────────────────────────────
    da = _anim(t, 0.45, 0.25)
    if da > 0:
        a  = int(255 * da * gf)
        oy = int(38 * (1.0 - da))
        f  = _font(168)

        # White shadow
        _text_center(draw, "DECODED", f,
                     line_y + 175, (0, 0, 0, a // 2), oy + 3)
        # Crimson fill
        _text_center(draw, "DECODED", f,
                     line_y + 175, (*_CRIMSON, a), oy)

    # ── "TRUE CRIME DOCUMENTARY" ──────────────────────────────────────────────
    sa = _anim(t, 0.62, 0.20)
    if sa > 0:
        a = int(200 * sa * gf)
        _text_center(draw, "TRUE CRIME DOCUMENTARY", _font(44),
                     line_y + 330, (*_GRAY, a))

    # ── Apply global fade via alpha composite with black background ───────────
    if gf < 1.0:
        black = Image.new("RGBA", (_W, _H), (*_BG, 255))
        canvas = Image.blend(black.convert("RGBA"),
                             canvas.convert("RGBA"),
                             alpha=gf)

    return canvas.convert("RGB").tobytes()


# ── Public API ────────────────────────────────────────────────────────────────
def create_intro(output_path: str,
                 channel_name: str = "Dark Crime Decoded") -> str | None:
    """
    Generate the 3-second vertical intro clip.
    Returns output_path on success, None on failure (non-fatal).
    """
    if not _HAVE_PIL:
        print("[Intro] Pillow not installed — skipping")
        return None

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[Intro] ffmpeg not found — skipping intro")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg, "-y",
        "-f",       "rawvideo",
        "-vcodec",  "rawvideo",
        "-s",       f"{_W}x{_H}",
        "-pix_fmt", "rgb24",
        "-r",       str(_FPS),
        "-i",       "pipe:0",
        "-vcodec",  "libx264",
        "-preset",  "fast",
        "-crf",     "20",
        "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for fi in range(_FRAMES):
            proc.stdin.write(_render_frame(fi))
        proc.stdin.close()
        proc.wait(timeout=60)

        if proc.returncode == 0 and os.path.exists(output_path):
            print(f"[Intro] Created: {output_path}")
            return output_path
        print(f"[Intro] ffmpeg exited {proc.returncode}")
        return None

    except Exception as e:
        print(f"[Intro] Failed: {e}")
        if proc:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.kill()
        return None


def prepend_intro(intro_path: str, video_path: str) -> str:
    """
    Prepend intro_path to video_path via ffmpeg concat.
    Replaces video_path in-place on success; returns original on any failure.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not os.path.exists(intro_path):
        return video_path

    tmp = video_path.replace(".mp4", "_intro_tmp.mp4")
    list_file = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8") as lf:
            lf.write(f"file '{os.path.abspath(intro_path)}'\n")
            lf.write(f"file '{os.path.abspath(video_path)}'\n")
            list_file = lf.name

        result = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0",
             "-i", list_file, "-c", "copy", tmp],
            capture_output=True, timeout=180,
        )
        if list_file:
            os.unlink(list_file)

        if result.returncode == 0 and os.path.exists(tmp):
            os.replace(tmp, video_path)
            print(f"[Intro] Prepended to {os.path.basename(video_path)}")
            return video_path

        print(f"[Intro] Concat failed (returncode={result.returncode}) — keeping original")

    except Exception as e:
        print(f"[Intro] Prepend failed: {e} — keeping original")
        if list_file and os.path.exists(list_file):
            try:
                os.unlink(list_file)
            except OSError:
                pass

    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return video_path
