# ============================================================
#  agents/publish_agent.py  —  Posts to YouTube & TikTok
#  X/Twitter removed (requires paid plan)
# ============================================================
import os
from config import TIKTOK_SESSION_ID, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET


# ── YOUTUBE ─────────────────────────────────────────────────

def upload_to_youtube(video_path: str, script_data: dict) -> str:
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("[Publish] Install: pip install google-api-python-client")
        return ""

    TOKEN_FILE = "youtube_token.json"
    if not os.path.exists(TOKEN_FILE):
        print("[Publish] YouTube token not found. Skipping.")
        return ""

    try:
        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            ["https://www.googleapis.com/auth/youtube.upload"]
        )
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": script_data["title"],
                "description": f"{script_data['caption']}\n\n{script_data['hashtags']}",
                "tags": script_data["keywords"],
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            video_path, chunksize=-1,
            resumable=True, mimetype="video/mp4"
        )
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"[Publish] YouTube {int(status.progress() * 100)}%")

        url = f"https://youtube.com/shorts/{response['id']}"
        print(f"[Publish] YouTube: {url}")
        return url

    except Exception as e:
        print(f"[Publish] YouTube failed: {e}")
        return ""


# ── TIKTOK ──────────────────────────────────────────────────

def upload_to_tiktok(video_path: str, script_data: dict) -> str:
    """Upload to TikTok using official API."""
    try:
        import requests
        
        # Initialize upload
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

        with open(video_path, "rb") as f:
            video_data = f.read()

        upload_headers = {
            "Content-Range": f"bytes 0-{video_size-1}/{video_size}",
            "Content-Type": "video/mp4"
        }
        requests.put(upload_url, data=video_data, headers=upload_headers)
        print(f"[Publish] TikTok posted: {publish_id}")
        return f"TikTok: {publish_id}"

    except Exception as e:
        print(f"[Publish] TikTok failed: {e}")
        return ""


# ── COMBINED ────────────────────────────────────────────────

def publish_video(video_path: str, script_data: dict) -> dict:
    """Publish to YouTube and TikTok based on language."""
    results = {}
    language = script_data.get("language", "english")

    if language == "english":
        # English videos → YouTube only
        results["youtube"] = upload_to_youtube(video_path, script_data)
    else:
        # Arabic videos → TikTok only
        results["tiktok"] = upload_to_tiktok(video_path, script_data)

    return results


if __name__ == "__main__":
    import sys
    if "--auth-youtube" in sys.argv:
        from google_auth_oauthlib.flow import InstalledAppFlow
        client_config = {
            "installed": {
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
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
        print("[Auth] YouTube token saved!")
