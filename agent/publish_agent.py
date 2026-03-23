# ============================================================
#  agents/publish_agent.py  —  Posts to TikTok & YouTube
# ============================================================
import os
import json
import requests
from pathlib import Path
from config import TIKTOK_SESSION_ID, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET


# ── YOUTUBE ─────────────────────────────────────────────────

def upload_to_youtube(video_path: str, script_data: dict) -> str:
    """
    Upload video to YouTube as an unlisted Short.
    Returns video URL or empty string on failure.

    Setup required:
    1. Go to Google Cloud Console
    2. Enable YouTube Data API v3
    3. Create OAuth 2.0 credentials
    4. Run first-time auth: python agents/publish_agent.py --auth-youtube
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("[Publish] Install: pip install google-api-python-client google-auth-oauthlib")
        return ""

    TOKEN_FILE = "youtube_token.json"
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        print("[Publish] YouTube auth required. Run: python agents/publish_agent.py --auth-youtube")
        return ""

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": script_data["title"],
            "description": f"{script_data['caption']}\n\n{script_data['hashtags']}",
            "tags": script_data["keywords"],
            "categoryId": "22"  # People & Blogs
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[Publish] YouTube upload {int(status.progress() * 100)}%")

    video_id = response["id"]
    url = f"https://youtube.com/shorts/{video_id}"
    print(f"[Publish] YouTube: {url}")
    return url


def auth_youtube():
    """One-time YouTube OAuth setup. Run this once before first use."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        import json
    except ImportError:
        print("Install: pip install google-auth-oauthlib")
        return

    client_config = {
        "installed": {
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )
    creds = flow.run_local_server(port=0)

    with open("youtube_token.json", "w") as f:
        f.write(creds.to_json())
    print("[Auth] YouTube token saved to youtube_token.json")


# ── TIKTOK ──────────────────────────────────────────────────

def upload_to_tiktok(video_path: str, script_data: dict) -> str:
    """
    Upload to TikTok using TikTok Content Posting API.
    Requires approved developer access at developers.tiktok.com

    Alternative: use TikTok Creator Marketplace API or
    unofficial uploader (tiktok-uploader PyPI package).
    Returns video URL or status message.
    """
    try:
        # Try official API first
        return _tiktok_official_api(video_path, script_data)
    except Exception as e:
        print(f"[Publish] TikTok official API failed: {e}")
        # Fallback to unofficial uploader
        return _tiktok_unofficial(video_path, script_data)


def _tiktok_official_api(video_path: str, script_data: dict) -> str:
    """TikTok Content Posting API (requires approval)."""
    # Step 1: Initialize upload
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {TIKTOK_SESSION_ID}",
        "Content-Type": "application/json"
    }

    video_size = os.path.getsize(video_path)
    caption = f"{script_data['caption']} {script_data['hashtags']}"[:2200]

    init_payload = {
        "post_info": {
            "title": caption,
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1
        }
    }

    init_r = requests.post(init_url, headers=headers, json=init_payload)
    init_r.raise_for_status()
    init_data = init_r.json()["data"]
    publish_id = init_data["publish_id"]
    upload_url = init_data["upload_url"]

    # Step 2: Upload video
    with open(video_path, "rb") as f:
        video_data = f.read()

    upload_headers = {
        "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        "Content-Type": "video/mp4"
    }
    requests.put(upload_url, data=video_data, headers=upload_headers)

    print(f"[Publish] TikTok publish_id: {publish_id}")
    return f"TikTok posted (publish_id: {publish_id})"


def _tiktok_unofficial(video_path: str, script_data: dict) -> str:
    """Fallback using tiktok-uploader package."""
    try:
        from tiktok_uploader.upload import upload_video
        upload_video(
            filename=video_path,
            description=f"{script_data['caption']} {script_data['hashtags']}"[:2200],
            cookies="tiktok_cookies.txt"  # export from browser
        )
        print("[Publish] TikTok uploaded (unofficial)")
        return "TikTok posted (unofficial uploader)"
    except ImportError:
        print("[Publish] Install: pip install tiktok-uploader")
        return ""
    except Exception as e:
        print(f"[Publish] TikTok upload failed: {e}")
        return ""


# ── COMBINED ────────────────────────────────────────────────

def publish_video(video_path: str, script_data: dict) -> dict:
    """Publish to both platforms. Returns dict of results."""
    results = {}
    results["youtube"] = upload_to_youtube(video_path, script_data)
    results["tiktok"]  = upload_to_tiktok(video_path, script_data)
    return results


if __name__ == "__main__":
    import sys
    if "--auth-youtube" in sys.argv:
        auth_youtube()
