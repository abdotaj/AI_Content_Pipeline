"""
agents/enhancer.py — Image enhancement for Dark Crime Decoded videos.

Merges premium AI backends with the OpenCV/Pillow fallback chain.
Auto-detects capabilities at startup and logs which mode is active.
Never crashes the pipeline — every failure returns the original path.

Mode selection (auto, checked once at import)
─────────────────────────────────────────────
PREMIUM  — Real-ESRGAN x2 and/or GFPGAN detected (local only, skipped on CI)
STANDARD — OpenCV + Pillow only (always works on GitHub Actions)

Enhancement pipeline
────────────────────
Both modes run the same base chain. Premium adds steps 2a/2b.

  1. smart_resize     Aspect-ratio-aware center-crop to 1080×1920. No distortion.
  2a.[esrgan]         Real-ESRGAN x2 upscale at native res   ← PREMIUM only
  2b.[gfpgan]         GFPGAN face restoration                ← PREMIUM only
  3.  bilateral       Edge-preserving denoise  (cv2 | PIL fallback)
  4.  clahe           Adaptive contrast, LAB L-channel only  (cv2 | PIL fallback)
                      Dark scenes: clipLimit 3.0 — bright scenes: 2.0
  5.  sharpen         Threshold-gated UnsharpMask (PIL) — never sharpens flat skin
  6.  dcd_grade       Dark Crime Decoded color grade (NumPy, single pass):
                        a. cool shadow tint     skin-protected, full at black
                        b. warm highlight tint  golden split-tone complement
                        c. black crush          power-curve x^1.12
                        d. midtone desaturation Gaussian-weighted, skin-protected
                        e. crimson accent       soft-score on existing reds only
  7.  vignette        Elliptical luminance falloff (cached NumPy mask)
  8.  grain           Subtle film noise, per-image seed for unique pattern per cut

Image sources handled well
──────────────────────────
  Pollinations AI   — already 1080×1920, has API compression artifacts → bilateral
  Stock photos      — often washed out, landscape → smart_resize + clahe
  Telegram uploads  — JPEG compressed, mixed sizes → bilateral + smart_resize
  Portraits         — faces protected throughout by skin detection heuristic
  Dark scenes       — adaptive CLAHE boost + black crush

Public API
──────────
enhance_image(input_path: str) -> str
    Saves {stem}_enh.png beside the original.
    Cache-aware: skips if _enh.png is newer than source.
    Returns original path on any failure.

enhance_folder(folder_path: str) -> list[str]
    Parallel (≤4 workers). Returns enhanced paths, sorted filename order.
"""

import hashlib
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image as PILImage
from PIL import ImageEnhance, ImageFilter

# ── Logging ───────────────────────────────────────────────────────────────────

log = logging.getLogger("enhancer")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(_h)
log.setLevel(logging.INFO)

# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_W, TARGET_H = 1080, 1920
_TARGET_RATIO = TARGET_W / TARGET_H          # 0.5625  (9:16 portrait)
_SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".jfif", ".bmp"}
_ON_CI = bool(os.getenv("CI") or os.getenv("GITHUB_ACTIONS"))


# ── Capability detection (runs once at import, never per-call) ────────────────

def _check_cv2() -> bool:
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def _check_realesrgan() -> bool:
    if _ON_CI:
        return False
    if not Path("weights/RealESRGAN_x2plus.pth").exists():
        return False
    try:
        import basicsr    # noqa: F401
        import cv2        # noqa: F401
        import realesrgan # noqa: F401
        import torch      # noqa: F401
        return True
    except ImportError:
        return False


def _check_gfpgan() -> bool:
    if _ON_CI:
        return False
    if not Path("weights/GFPGANv1.4.pth").exists():
        return False
    try:
        import cv2    # noqa: F401
        import gfpgan # noqa: F401
        return True
    except ImportError:
        return False


_HAVE_CV2    = _check_cv2()
_HAVE_ESRGAN = _check_realesrgan()
_HAVE_GFPGAN = _check_gfpgan()
_MODE        = "PREMIUM" if (_HAVE_ESRGAN or _HAVE_GFPGAN) else "STANDARD"

log.info(
    "[Enhancer] Mode: %s | cv2=%s | esrgan=%s | gfpgan=%s",
    _MODE,
    "yes" if _HAVE_CV2    else "no (PIL fallback)",
    "yes" if _HAVE_ESRGAN else "no",
    "yes" if _HAVE_GFPGAN else "no",
)


# ── Step 1 — Smart resize ─────────────────────────────────────────────────────

def _smart_resize(img: PILImage.Image) -> PILImage.Image:
    """
    Resize to TARGET_W × TARGET_H without distortion.

    Scales so the image fills the target along one axis, then center-crops
    the overflow on the other axis. Preserves the subject (assumed centered).

    Landscape → scale to height=1920, center-crop width to 1080.
    Portrait  → scale to width=1080, center-crop height to 1920.
    Exact 9:16 → scale only, no crop.
    """
    src_w, src_h = img.size
    if (src_w, src_h) == (TARGET_W, TARGET_H):
        return img

    src_ratio = src_w / src_h

    if src_ratio > _TARGET_RATIO:
        # Wider than 9:16 → fit height, crop width
        new_h = TARGET_H
        new_w = round(src_w * TARGET_H / src_h)
        img = img.resize((new_w, new_h), PILImage.LANCZOS)
        left = (new_w - TARGET_W) // 2
        img = img.crop((left, 0, left + TARGET_W, TARGET_H))
    else:
        # Taller than (or equal to) 9:16 → fit width, crop height
        new_w = TARGET_W
        new_h = round(src_h * TARGET_W / src_w)
        img = img.resize((new_w, new_h), PILImage.LANCZOS)
        top = (new_h - TARGET_H) // 2
        img = img.crop((0, top, TARGET_W, top + TARGET_H))

    # Rounding edge-case guard
    if img.size != (TARGET_W, TARGET_H):
        img = img.resize((TARGET_W, TARGET_H), PILImage.LANCZOS)
    return img


# ── Steps 2a/2b — Premium backends (STANDARD mode: these are never called) ────

def _apply_realesrgan(img: PILImage.Image) -> "PILImage.Image | None":
    """
    Real-ESRGAN x2 upscale at the image's native resolution.
    Caller smart-resizes the result back to target.
    Returns None on any failure so the caller can skip gracefully.
    """
    try:
        import cv2
        import torch
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer

        model = RRDBNet(
            num_in_ch=3, num_out_ch=3,
            num_feat=64, num_block=23, num_grow_ch=32, scale=2,
        )
        upsampler = RealESRGANer(
            scale=2,
            model_path="weights/RealESRGAN_x2plus.pth",
            model=model,
            tile=512, tile_pad=16, pre_pad=0,
            half=torch.cuda.is_available(),
        )
        bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        out_bgr, _ = upsampler.enhance(bgr, outscale=2)
        return PILImage.fromarray(cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB))
    except Exception as exc:
        log.debug("[Enhancer] Real-ESRGAN failed: %s", exc)
        return None


def _apply_gfpgan(img: PILImage.Image) -> "PILImage.Image | None":
    """
    GFPGAN face restoration at whatever resolution it receives.
    Returns None on any failure so the caller can skip gracefully.
    """
    try:
        import cv2
        from gfpgan import GFPGANer

        restorer = GFPGANer(
            model_path="weights/GFPGANv1.4.pth",
            upscale=1, arch="clean", channel_multiplier=2,
        )
        bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        _, _, restored_bgr = restorer.enhance(
            bgr, has_aligned=False, only_center_face=False, paste_back=True,
        )
        return PILImage.fromarray(cv2.cvtColor(restored_bgr, cv2.COLOR_BGR2RGB))
    except Exception as exc:
        log.debug("[Enhancer] GFPGAN failed: %s", exc)
        return None


# ── Step 3 — Bilateral denoise ────────────────────────────────────────────────

def _bilateral_denoise(img: PILImage.Image) -> PILImage.Image:
    """
    Edge-preserving denoise via cv2.bilateralFilter.

    Removes JPEG block artifacts, Pollinations API compression ringing, and
    Telegram re-compression noise while keeping face edges, text, and
    architectural lines sharp. PIL SMOOTH is the fallback.
    """
    if not _HAVE_CV2:
        return img.filter(ImageFilter.SMOOTH)
    try:
        import cv2
        bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        bgr = cv2.bilateralFilter(bgr, d=7, sigmaColor=35, sigmaSpace=35)
        return PILImage.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    except Exception:
        return img.filter(ImageFilter.SMOOTH)


# ── Step 4 — Adaptive CLAHE contrast ─────────────────────────────────────────

def _clahe_contrast(img: PILImage.Image) -> PILImage.Image:
    """
    Adaptive histogram equalization on the LAB L-channel only.

    Lifts dark areas (crime scenes, shadowed faces, dimly-lit stock photos)
    without blowing out highlights. Hue and saturation channels are untouched.

    Adaptive clipLimit: dark images (avg L < 35 %) get clipLimit=3.0 for a
    stronger boost; brighter images get 2.0 to avoid amplifying noise.
    PIL Contrast fallback if cv2 unavailable.
    """
    if not _HAVE_CV2:
        arr_l = np.array(img.convert("L"), dtype=np.float32)
        avg_lum = arr_l.mean() / 255.0
        factor = 1.25 if avg_lum < 0.35 else 1.15
        return ImageEnhance.Contrast(img).enhance(factor)
    try:
        import cv2
        bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l_ch, a_ch, b_ch = cv2.split(lab)
        avg_lum = l_ch.mean() / 255.0
        clip = 3.0 if avg_lum < 0.35 else 2.0
        clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
        l_ch = clahe.apply(l_ch)
        bgr = cv2.cvtColor(cv2.merge([l_ch, a_ch, b_ch]), cv2.COLOR_LAB2BGR)
        return PILImage.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    except Exception:
        return ImageEnhance.Contrast(img).enhance(1.15)


# ── Step 5 — Sharpening ───────────────────────────────────────────────────────

def _sharpen(img: PILImage.Image) -> PILImage.Image:
    # threshold=2 suppresses sharpening on flat regions (skin, clear sky)
    # so only meaningful edges are enhanced.
    return img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=130, threshold=2))


# ── Step 6 — DCD color grade (single NumPy pass) ─────────────────────────────

def _apply_dcd_grade(img: PILImage.Image) -> PILImage.Image:
    """
    Dark Crime Decoded color grade. All work in float32 [0, 1].

    Skin tone heuristic
    ───────────────────
    Detects warm-biased pixels (R > G > B spread) across all skin tones.
    Skin pixels receive:
      • 15 % of the cool shadow shift  (face shadows stay warm)
      • 30 % of the midtone desaturation  (faces stay vivid)
    The crimson boost excludes skin entirely.

    Crimson accent
    ──────────────
    Smooth confidence score — peaks on saturated reds, fades on dark or
    muted reds, zero on skin. Only deepens colours that are already there.
    """
    arr = np.array(img, dtype=np.float32) / 255.0
    R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]

    # Rec. 601 luminance
    lum = 0.299 * R + 0.587 * G + 0.114 * B

    # Skin detection — tuned to work from dark (0.15) to near-specular (0.95)
    skin = (
        (R > 0.15) & (R < 0.95) &
        (R > G + 0.02) &   # red leads green
        (G > B + 0.01) &   # green leads blue (warm, not violet)
        (R - B > 0.08)     # meaningful warm-cool spread
    )
    skin_w  = skin.astype(np.float32)
    no_skin = 1.0 - skin_w

    # a. Cool shadow tint — teal/blue below lum ≈ 0.45, smooth rolloff
    shadow_w = np.clip(1.0 - lum / 0.45, 0.0, 1.0) ** 1.5
    shadow_w *= no_skin + skin_w * 0.15     # skin → 15 % of effect
    arr[..., 0] -= shadow_w * 0.030
    arr[..., 1] -= shadow_w * 0.010
    arr[..., 2] += shadow_w * 0.040

    # b. Warm highlight tint — golden above lum ≈ 0.65 (split-tone complement)
    hi_w = np.clip((lum - 0.65) / 0.35, 0.0, 1.0) ** 2.0
    arr[..., 0] += hi_w * 0.015
    arr[..., 1] += hi_w * 0.007
    arr[..., 2] -= hi_w * 0.010

    arr = np.clip(arr, 0.0, 1.0)

    # c. Black crush — power curve x^1.12
    #    lum 0.05 → 0.037 (−26 %)   deep shadow
    #    lum 0.50 → 0.463 (− 7 %)   midtone      barely perceptible
    #    lum 0.90 → 0.886 (− 2 %)   highlight    imperceptible
    #    lum 1.00 → 1.000             white stays white
    arr = np.power(arr, 1.12)

    # d. Midtone desaturation — Gaussian peak at lum ≈ 0.42
    R2, G2, B2 = arr[..., 0], arr[..., 1], arr[..., 2]
    lum2  = 0.299 * R2 + 0.587 * G2 + 0.114 * B2
    mid_w = np.exp(-((lum2 - 0.42) ** 2) / (2 * 0.22 ** 2))
    desat_w = mid_w * 0.18 * (1.0 - skin_w * 0.70)   # skin → 30 % of effect
    gray  = np.stack([lum2, lum2, lum2], axis=-1)
    arr   = arr * (1.0 - desat_w[..., np.newaxis]) + gray * desat_w[..., np.newaxis]

    # e. Crimson accent — smooth score, zero on skin
    R3, G3, B3 = arr[..., 0], arr[..., 1], arr[..., 2]
    crim_score = (
        np.clip((R3 - 0.38) / 0.42, 0.0, 1.0) *
        np.clip(1.0 - G3 / 0.38, 0.0, 1.0) *
        np.clip(1.0 - B3 / 0.38, 0.0, 1.0) *
        no_skin
    )
    crim_w = np.clip(crim_score * 1.4, 0.0, 1.0)
    arr[..., 0] = np.clip(R3 + crim_w * 0.055, 0.0, 1.0)
    arr[..., 2] = np.clip(B3 + crim_w * 0.030, 0.0, 1.0)
    arr[..., 1] = np.clip(G3 - crim_w * 0.018, 0.0, 1.0)

    return PILImage.fromarray((np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8))


# ── Step 7 — Vignette ─────────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def _vignette_mask(width: int, height: int, strength: float = 0.45) -> np.ndarray:
    """float32 (H, W, 1) mask: 1.0 at centre, ~0.55 at corners. Cached."""
    Y, X = np.ogrid[:height, :width]
    cx, cy = width / 2.0, height / 2.0
    dist = np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
    return np.clip(1.0 - strength * (dist ** 1.6), 0.0, 1.0).astype(np.float32)[:, :, np.newaxis]


def _apply_vignette(img: PILImage.Image) -> PILImage.Image:
    arr = np.array(img, dtype=np.float32)
    arr *= _vignette_mask(img.width, img.height)
    return PILImage.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ── Step 8 — Film grain ───────────────────────────────────────────────────────

def _add_grain(img: PILImage.Image, path_seed: int = 42, strength: float = 3.5) -> PILImage.Image:
    # Unique seed per image → different grain pattern per cut → not "stuck" in video
    rng = np.random.default_rng(seed=path_seed)
    arr = np.array(img, dtype=np.float32)
    noise = rng.normal(0.0, strength, arr.shape).astype(np.float32)
    return PILImage.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))


# ── Public API ────────────────────────────────────────────────────────────────

def enhance_image(input_path: str) -> str:
    """
    Enhance one image through the full DCD pipeline and return its output path.

    Output: {stem}_enh.png saved beside the original.
    Cache:  if _enh.png already exists and is newer than the source, it is
            returned immediately without reprocessing.
    Safety: returns the original path unchanged on any failure.
    """
    in_path = Path(input_path)

    if not in_path.exists():
        log.warning("[Enhancer] File not found: %s", input_path)
        return input_path
    if in_path.suffix.lower() not in _SUPPORTED_EXTS:
        return input_path
    if in_path.stem.endswith("_enh"):
        return input_path

    out_path = in_path.with_name(in_path.stem + "_enh.png")
    if out_path.exists() and out_path.stat().st_mtime >= in_path.stat().st_mtime:
        log.info("[Enhancer] Cache hit: %s", out_path.name)
        return str(out_path)

    path_seed = int(hashlib.md5(in_path.name.encode()).hexdigest()[:8], 16) & 0xFFFFFF
    steps: list[str] = []
    t0 = time.monotonic()

    try:
        img = PILImage.open(input_path).convert("RGB")

        # ── Step 1: smart resize ─────────────────────────────────────────────
        img = _smart_resize(img)
        steps.append("resize")

        # ── Steps 2a/2b: premium backends (STANDARD mode: skipped entirely) ──
        if _HAVE_ESRGAN:
            result = _apply_realesrgan(img)
            if result is not None:
                img = _smart_resize(result)   # ESRGAN may 2× the resolution
                steps.append("esrgan")

        if _HAVE_GFPGAN:
            result = _apply_gfpgan(img)
            if result is not None:
                img = _smart_resize(result)
                steps.append("gfpgan")

        # ── Steps 3–8: standard chain (always runs) ──────────────────────────
        img = _bilateral_denoise(img);  steps.append("bilateral")
        img = _clahe_contrast(img);     steps.append("clahe")
        img = _sharpen(img);            steps.append("sharpen")
        img = _apply_dcd_grade(img);    steps.append("dcd-grade")
        img = _apply_vignette(img);     steps.append("vignette")
        img = _add_grain(img, path_seed=path_seed); steps.append("grain")

        # Final size guard (rounding edge-cases from ESRGAN or GFPGAN)
        if img.size != (TARGET_W, TARGET_H):
            img = img.resize((TARGET_W, TARGET_H), PILImage.LANCZOS)

        img.save(str(out_path), "PNG", optimize=False)

        elapsed = time.monotonic() - t0
        log.info(
            "[Enhancer] %s → %s  [%s | %s | %.1fs]",
            in_path.name, out_path.name,
            _MODE, "+".join(steps), elapsed,
        )
        return str(out_path)

    except Exception as exc:
        log.warning("[Enhancer] Failed on %s (%s) — using original", in_path.name, exc)
        return input_path


def enhance_folder(folder_path: str) -> list[str]:
    """
    Enhance all supported images in folder_path with up to 4 parallel workers.

    Already-enhanced files (stem ending '_enh') are always skipped.
    Returns enhanced paths in sorted filename order.
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        log.warning("[Enhancer] Not a directory: %s", folder_path)
        return []

    files = sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() in _SUPPORTED_EXTS
        and not f.stem.endswith("_enh")
    )
    if not files:
        log.info("[Enhancer] No images to enhance in %s", folder_path)
        return []

    log.info("[Enhancer] Enhancing %d image(s) in %s", len(files), folder_path)

    results: list[str] = [""] * len(files)
    with ThreadPoolExecutor(max_workers=min(4, len(files))) as pool:
        future_to_idx = {
            pool.submit(enhance_image, str(f)): i
            for i, f in enumerate(files)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = str(files[idx])
                log.warning("[Enhancer] Worker error on %s: %s", files[idx].name, exc)

    log.info("[Enhancer] Done — %d image(s) enhanced", len(results))
    return results
