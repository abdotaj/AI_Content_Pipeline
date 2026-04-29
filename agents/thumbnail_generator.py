"""
thumbnail_generator.py — Dark Crime Decoded thumbnail generator.

Produces a 1280×720 YouTube-optimised thumbnail:
  • Dark image background with cinematic vignette
  • Crimson gradient accent at bottom
  • Bold white title (auto-wrapped, mobile-readable)
  • Small channel branding at top

Usage:
    from agents.thumbnail_generator import create_thumbnail
    path = create_thumbnail(
        image_path  = "output/images/img_001_enh.png",
        title       = "Pablo Escobar: The Real Story",
        output_path = "output/dark_crime/final/video_001_thumb.jpg",
        language    = "english",   # or "arabic"
    )
"""

import os
import textwrap
from functools import lru_cache
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as np
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

# ── Canvas dimensions ─────────────────────────────────────────────────────────
_TW, _TH = 1280, 720          # YouTube standard thumbnail

# ── Palette ───────────────────────────────────────────────────────────────────
_CRIMSON      = (180,  10,  30)
_CRIMSON_MID  = (120,   8,  20)
_WHITE        = (255, 255, 255)
_BLACK        = (0,     0,   0)
_SHADOW       = (15,   15,  18)
_GRAY_LIGHT   = (200, 200, 205)

# ── Stop words for title distillation ────────────────────────────────────────
_STOP_WORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
    "but", "with", "by", "from", "as", "is", "was", "are", "were",
    "that", "this", "it", "its", "be", "been", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should",
    "very", "also", "just", "then", "than", "so", "yet", "not", "no",
})

# ── Font lookup (cached) ──────────────────────────────────────────────────────
_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-Bold.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/seguibl.ttf",
]

_FONT_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
]


@lru_cache(maxsize=16)
def _font(size: int, bold: bool = True) -> "ImageFont.FreeTypeFont":
    candidates = _FONT_BOLD if bold else _FONT_REGULAR
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── Internal helpers ──────────────────────────────────────────────────────────
def _fit_cover(img: "Image.Image", w: int, h: int) -> "Image.Image":
    """Scale + center-crop image to exactly (w, h), preserving aspect ratio."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img   = img.resize((new_w, new_h), Image.LANCZOS)
    left  = (new_w - w) // 2
    top   = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _dark_overlay(canvas: "Image.Image", opacity: float = 0.50) -> "Image.Image":
    """Blend a black overlay at `opacity` over canvas (RGB)."""
    overlay = Image.new("RGB", canvas.size, _BLACK)
    return Image.blend(canvas, overlay, alpha=opacity)


def _crimson_gradient(canvas: "Image.Image",
                      start_y: int, strength: float = 0.85) -> "Image.Image":
    """
    Add a vertical crimson→black gradient from start_y to bottom.
    Used to darken/brand the text zone.
    """
    if not _HAVE_PIL:
        return canvas
    try:
        import numpy as _np
        arr   = _np.array(canvas, dtype=_np.float32)
        h, w  = arr.shape[:2]
        zone  = h - start_y  # gradient height in pixels
        for row in range(start_y, h):
            t = (row - start_y) / zone              # 0 at top of zone, 1 at bottom
            t = t ** 0.7                            # slight ease (stronger at bottom)
            alpha = strength * t
            target = _np.array(_SHADOW, dtype=_np.float32)
            arr[row] = arr[row] * (1 - alpha) + target * alpha
        return Image.fromarray(_np.clip(arr, 0, 255).astype(_np.uint8))
    except Exception:
        # NumPy unavailable — plain dark box fallback
        draw  = ImageDraw.Draw(canvas)
        draw.rectangle([0, start_y, canvas.width, canvas.height],
                       fill=(*_SHADOW, 200))
        return canvas


def _vignette(canvas: "Image.Image", strength: float = 0.55) -> "Image.Image":
    """Darken corners using an elliptical vignette mask."""
    try:
        import numpy as _np
        arr  = _np.array(canvas, dtype=_np.float32)
        h, w = arr.shape[:2]
        Y, X = _np.ogrid[:h, :w]
        cx, cy = w / 2.0, h / 2.0
        dist = _np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
        mask = _np.clip(1.0 - strength * (dist ** 1.8), 0.0, 1.0)
        arr  = arr * mask[..., _np.newaxis]
        return Image.fromarray(_np.clip(arr, 0, 255).astype(_np.uint8))
    except Exception:
        return canvas


def _extract_thumb_text(title: str, max_words: int = 4) -> str:
    """
    Distil a full episode title to 2-5 high-impact words for thumbnail CTR.

    Logic:
      1. Strip brand prefix ("Dark Crime Decoded:", episode markers).
      2. Prefer the segment after em-dash / colon — it usually contains the hook.
      3. Drop stop words; keep proper nouns, numbers, power words.
      4. Return UPPERCASE for display impact.
    """
    import re

    text = title.strip()
    text = re.sub(r"^dark crime decoded[\s:–—-]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bepisode\s*\d+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\(\[{][^\)\]}]*[\)\]}]", "", text)

    # Prefer the hook half (after — : |)
    for sep in ("—", "–", ":", "|"):
        if sep in text:
            parts = [p.strip() for p in text.split(sep, 1) if p.strip()]
            if len(parts) == 2 and len(parts[1].split()) >= 2:
                text = parts[1]
                break

    words = [w.strip("\"',.!?") for w in text.split()]
    sig   = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 1]
    result = sig[:max_words] if len(sig) >= 2 else [w for w in words if len(w) > 1][:max_words]
    return " ".join(result).upper() if result else title[:40].upper()


def _score_image(path: str) -> float:
    """
    Score a candidate image for thumbnail suitability (0–100).

    Criteria (evaluated on a 320×320 downsample for speed):
      Sharpness        25 pts  — Laplacian energy via PIL FIND_EDGES
      Contrast         20 pts  — luminance std-dev
      Skin / face      30 pts  — skin-heuristic pixels in upper-centre region
      Dramatic compo.  15 pts  — bright centre vs. dark edges
      Subject coverage 10 pts  — centre luminance variance
    """
    try:
        import numpy as _np
        from PIL import Image as _Img, ImageFilter as _IF

        img = _Img.open(path).convert("RGB").resize((320, 320), _Img.LANCZOS)
        arr = _np.array(img, dtype=_np.float32)
        R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]
        lum  = 0.299 * R + 0.587 * G + 0.114 * B
        h, w = arr.shape[:2]
        q    = w // 4
        cx, cy = w // 2, h // 2

        # Sharpness — mean squared edge response
        edges  = _np.array(img.convert("L").filter(_IF.FIND_EDGES), dtype=_np.float32)
        s_sharp = min(float(_np.mean(edges ** 2)) / 400.0, 1.0) * 25

        # Contrast — luminance std-dev
        s_contrast = min(float(_np.std(lum)) / 80.0, 1.0) * 20

        # Skin tone heuristic (face presence)
        Ri = R.astype(_np.int32); Gi = G.astype(_np.int32); Bi = B.astype(_np.int32)
        skin = (
            (R > 60) & (G > 30) & (B > 15) & (R > G) & (R > B) &
            ((Ri - Bi) > 15) &
            ((_np.maximum(_np.maximum(Ri, Gi), Bi) -
              _np.minimum(_np.minimum(Ri, Gi), Bi)) > 15)
        )
        region  = skin[:int(h * 0.75), int(w * 0.15):int(w * 0.85)]
        s_face  = min(float(region.mean()) / 0.12, 1.0) * 30

        # Dramatic composition — bright centre, dark edges
        c_lum = float(lum[cy - q:cy + q, cx - q:cx + q].mean())
        e_lum = float(_np.concatenate([
            lum[:q, :].ravel(), lum[-q:, :].ravel(),
            lum[:, :q].ravel(), lum[:, -q:].ravel(),
        ]).mean())
        s_drama = min(max(0.0, c_lum - e_lum) / 55.0, 1.0) * 15

        # Subject coverage — centre variance
        s_cover = min(float(_np.var(lum[cy - q:cy + q, cx - q:cx + q])) / 1200.0, 1.0) * 10

        return round(s_sharp + s_contrast + s_face + s_drama + s_cover, 2)

    except Exception:
        return 0.0


def select_best_image(candidates: list[str]) -> str:
    """
    Score up to 5 candidate images and return the path of the highest scorer.
    Falls back to candidates[0] if every score is 0 or scoring fails.
    """
    valid = [p for p in candidates if p and os.path.exists(p)]
    if not valid:
        return candidates[0] if candidates else ""
    if len(valid) == 1:
        return valid[0]

    scores: list[tuple[float, str]] = []
    for path in valid[:5]:
        sc = _score_image(path)
        print(f"[Thumb] Score {sc:5.1f} — {os.path.basename(path)}")
        scores.append((sc, path))

    best_sc, best_path = max(scores, key=lambda x: x[0])
    print(f"[Thumb] Best: {os.path.basename(best_path)} (score={best_sc})")
    return best_path


def _wrap_title(title: str, font, max_width: int,
                draw: "ImageDraw.Draw") -> list[str]:
    """Word-wrap title to fit within max_width pixels. Returns list of lines."""
    words = title.split()
    lines: list[str] = []
    line  = ""
    for word in words:
        test = f"{line} {word}".strip()
        w    = draw.textbbox((0, 0), test, font=font)[2]
        if w > max_width and line:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    return lines[:3]   # max 3 lines to stay readable


def _draw_text_shadowed(draw: "ImageDraw.Draw", pos: tuple,
                        text: str, font,
                        fill: tuple = _WHITE,
                        shadow_offset: int = 3,
                        shadow_color: tuple = _BLACK) -> None:
    """Draw text with a drop shadow for contrast against any background."""
    x, y = pos
    draw.text((x + shadow_offset, y + shadow_offset), text,
              font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_text_outline(draw: "ImageDraw.Draw", pos: tuple,
                       text: str, font,
                       fill: tuple = _WHITE,
                       outline: tuple = _BLACK,
                       thickness: int = 2) -> None:
    """Draw text with a pixel-perfect outline for maximum readability."""
    x, y = pos
    for dx in range(-thickness, thickness + 1):
        for dy in range(-thickness, thickness + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


# ── Public API ────────────────────────────────────────────────────────────────
def create_thumbnail(image_path: str,
                     title: str,
                     output_path: str,
                     language: str = "english",
                     channel: str = "Dark Crime Decoded") -> str | None:
    """
    Generate a 1280×720 YouTube thumbnail.

    Args:
        image_path:  Background image (any size/format — auto-fit).
        title:       Episode title (auto-wrapped to 2-3 lines).
        output_path: Destination .jpg path.
        language:    "english" or "arabic" (controls text alignment).
        channel:     Channel brand tag displayed at top.

    Returns:
        output_path on success, None on failure.
    """
    if not _HAVE_PIL:
        print("[Thumb] Pillow not installed — skipping thumbnail")
        return None

    if not os.path.exists(image_path):
        print(f"[Thumb] Source image not found: {image_path}")
        return None

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    arabic = language.lower().startswith("ar")

    try:
        # ── 1. Background image ───────────────────────────────────────────────
        bg = Image.open(image_path).convert("RGB")
        bg = _fit_cover(bg, _TW, _TH)

        # ── 2. Cinematic grade ────────────────────────────────────────────────
        bg = _dark_overlay(bg, opacity=0.42)
        bg = _vignette(bg, strength=0.55)

        # ── 3. Crimson gradient zone (bottom 45%) ─────────────────────────────
        gradient_top = int(_TH * 0.55)
        bg = _crimson_gradient(bg, start_y=gradient_top, strength=0.82)

        draw = ImageDraw.Draw(bg)

        # ── 4. Crimson accent line (left edge) ────────────────────────────────
        draw.rectangle([0, 0, 6, _TH], fill=_CRIMSON)

        # ── 5. Channel branding (top) ─────────────────────────────────────────
        brand_font = _font(28, bold=False)
        brand_text = channel.upper()
        brand_bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
        brand_w    = brand_bbox[2] - brand_bbox[0]
        if arabic:
            brand_x = _TW - brand_w - 30
        else:
            brand_x = 30
        # Pill background for brand readability
        pad = 8
        draw.rectangle(
            [brand_x - pad, 18 - pad,
             brand_x + brand_w + pad, 18 + brand_bbox[3] + pad],
            fill=(*_CRIMSON_MID, 200),
        )
        draw.text((brand_x, 18), brand_text, font=brand_font, fill=_WHITE)

        # ── 6. Main title (distilled to 2-5 high-impact words for CTR) ──────────
        title_clean = _extract_thumb_text(title)
        for font_size in (88, 74, 62):
            tf   = _font(font_size)
            pad  = 60              # horizontal padding from edges
            lines = _wrap_title(title_clean, tf, _TW - pad * 2 - 10, draw)
            if len(lines) <= 3:
                break

        # Measure total text block height
        sample_bbox  = draw.textbbox((0, 0), "Ag", font=tf)
        line_h       = sample_bbox[3] - sample_bbox[1]
        line_spacing = int(line_h * 0.25)
        block_h      = len(lines) * line_h + (len(lines) - 1) * line_spacing

        # Vertically center text block in the bottom 45% zone
        zone_top = gradient_top + 20
        block_y  = zone_top + (_TH - zone_top - block_h) // 2

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=tf)
            lw   = bbox[2] - bbox[0]
            if arabic:
                lx = _TW - pad - lw
            else:
                lx = pad
            ly = block_y + i * (line_h + line_spacing)
            _draw_text_outline(draw, (lx, ly), line, tf,
                               fill=_WHITE, outline=_BLACK, thickness=2)

        # ── 7. Crimson underline below title ──────────────────────────────────
        underline_y = block_y + block_h + 14
        draw.rectangle([pad, underline_y, pad + 120, underline_y + 4],
                       fill=_CRIMSON)

        # ── 8. Save ───────────────────────────────────────────────────────────
        bg.save(output_path, "JPEG", quality=95, optimize=True)
        print(f"[Thumb] Created: {output_path}")
        return output_path

    except Exception as e:
        print(f"[Thumb] Failed: {e}")
        return None
