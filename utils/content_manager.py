import os
import json
import hashlib

BASE_CONTENT_DIR = "content"


def topic_to_slug(topic: str) -> str:
    """Convert a topic name to a filesystem-safe folder slug.

    Single source of truth for topic → folder mapping across the pipeline.
    'Pablo Escobar' → 'pablo_escobar', 'tokyo-vice' → 'tokyo_vice'
    """
    return topic.lower().strip().replace(" ", "_").replace("-", "_")


def ensure_topic_content(topic: str) -> dict:
    """
    Ensure content/{topic}/ subtree exists with all required subdirs.
    Returns a dict with all paths and media counts — safe to call repeatedly.

    Structure created:
        content/<topic>/images/      — AI + real images (persistent, reusable)
        content/<topic>/videos/      — final assembled videos (en/ar long/short)
        content/<topic>/animations/  — motion clips and character photos
        content/<topic>/characters/  — real photos, cast.json identity lock
        content/<topic>/cache/       — prompt/image/clip hash index
        content/<topic>/metadata/    — scripts, research, metadata JSON
    """
    slug            = topic_to_slug(topic)
    topic_path      = os.path.join(BASE_CONTENT_DIR, slug)
    images_path     = os.path.join(topic_path, "images")
    videos_path     = os.path.join(topic_path, "videos")
    animations_path = os.path.join(topic_path, "animations")
    characters_path = os.path.join(topic_path, "characters")
    cache_path      = os.path.join(topic_path, "cache")
    metadata_path   = os.path.join(topic_path, "metadata")

    for p in [images_path, videos_path, animations_path, characters_path,
              cache_path, metadata_path]:
        os.makedirs(p, exist_ok=True)

    images = [f for f in os.listdir(images_path) if f.lower().endswith((".jpg", ".png"))]
    videos = [f for f in os.listdir(videos_path) if f.lower().endswith(".mp4")]

    return {
        "topic":            slug,
        "path":             topic_path,
        "images_path":      images_path,
        "videos_path":      videos_path,
        "animations_path":  animations_path,
        "characters_path":  characters_path,
        "cache_path":       cache_path,
        "metadata_path":    metadata_path,
        "images_count":     len(images),
        "videos_count":     len(videos),
    }


def save_topic_metadata(
    topic: str,
    *,
    en_script: str = "",
    ar_script: str = "",
    research: dict | None = None,
    metadata: dict | None = None,
) -> None:
    """Persist scripts, research, and metadata into content/<topic>/metadata/."""
    paths = ensure_topic_content(topic)
    md    = paths["metadata_path"]

    if en_script:
        try:
            with open(os.path.join(md, "english_script.txt"), "w", encoding="utf-8") as f:
                f.write(en_script)
        except Exception as e:
            print(f"[CONTENT] english_script.txt save failed: {e}")

    if ar_script:
        try:
            with open(os.path.join(md, "arabic_script.txt"), "w", encoding="utf-8") as f:
                f.write(ar_script)
        except Exception as e:
            print(f"[CONTENT] arabic_script.txt save failed: {e}")

    if research is not None:
        try:
            with open(os.path.join(md, "research.json"), "w", encoding="utf-8") as f:
                json.dump(research, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CONTENT] research.json save failed: {e}")

    if metadata is not None:
        try:
            with open(os.path.join(md, "metadata.json"), "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CONTENT] metadata.json save failed: {e}")


def prompt_hash(text: str) -> str:
    """SHA-256 short hash of a prompt string — used for dedup caching."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]


def read_image_cache(cache_path: str) -> dict:
    """Load images_cache.json → {prompt_hash: absolute_image_path}."""
    cache_file = os.path.join(cache_path, "images_cache.json")
    if not os.path.exists(cache_file):
        return {}
    try:
        with open(cache_file, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_image_cache(cache_path: str, cache: dict) -> None:
    """Atomically persist images_cache.json."""
    cache_file = os.path.join(cache_path, "images_cache.json")
    tmp = cache_file + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp, cache_file)
    except Exception as e:
        print(f"[CONTENT] image cache write failed: {e}")
