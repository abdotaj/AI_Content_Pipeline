import os

BASE_CONTENT_DIR = "content"


def ensure_topic_content(topic: str) -> dict:
    """
    Ensure content/{topic}/images/ and content/{topic}/videos/ exist.
    Returns a dict with paths and media counts — safe to call even when
    the topic folder doesn't exist yet (folders are created on first call).
    """
    topic = topic.lower().strip().replace(" ", "_")
    topic_path   = os.path.join(BASE_CONTENT_DIR, topic)
    images_path  = os.path.join(topic_path, "images")
    videos_path  = os.path.join(topic_path, "videos")

    os.makedirs(images_path, exist_ok=True)
    os.makedirs(videos_path, exist_ok=True)

    images = [
        f for f in os.listdir(images_path)
        if f.lower().endswith((".jpg", ".png"))
    ]
    videos = [
        f for f in os.listdir(videos_path)
        if f.lower().endswith(".mp4")
    ]

    return {
        "topic":        topic,
        "path":         topic_path,
        "images_path":  images_path,
        "videos_path":  videos_path,
        "images_count": len(images),
        "videos_count": len(videos),
    }
