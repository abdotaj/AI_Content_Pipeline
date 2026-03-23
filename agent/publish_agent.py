# ============================================================
#  agents/publish_agent.py  —  Posts to TikTok, YouTube & X
# ============================================================
import os
import json
import requests
from pathlib import Path
from config import (
    TIKTOK_SESSION_ID, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
    X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
)


# ── X / TWITTER ─────────────────────────────────────────────

def post_to_x(script_data: dict) -> str:
    """Post a text tweet to X with title, caption and hashtags."""
    try:
        import tweepy
    except ImportError:
        print("[Publish] Install: pip install tweepy")
        return ""

    try:
        client = tweepy.Client(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET
        )

        # Build tweet text (max 280 chars)
        tweet = f"{script_data['title']}\n\n{script_data['caption']}\n\n{script_data['hashtags']}"
        if len(tweet) > 280:
            tweet = f"{script_data['title']}\n\n{script_data['hashtags']}"
        if len(tweet) > 280:
            tweet = script_data['title'][:277] + "..."

        response = client.create_tweet(text=tweet)
        tweet_id = response.data["id"]
        url = f"https://x.com/i/web/status/{tweet_id}"
        print(f"[Publish] X posted: {url}")
        return url

    except Exception as e:
        print(f"[Publish] X failed: {e}")
        return ""


# ── YOUTUBE ─────────────────────────────────────────────────

def upload_to_youtube(video_path: str, script_data: dict) -> str:
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("[Publish] Install: pip install google-api-python-client google-auth-oauthlib")
        return ""

    TOKEN_FILE = "youtube_token.json"
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

    if not os.path.exists(TOKEN_FILE):
        print("[Publish] YouTube token not found. Run: python agents/publish_agent.py --auth-youtube")
        return ""

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
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

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[Publish] YouTube upload {int(status.progress() * 100)}%")

    url = f"https://youtube.com/shorts/{response['id']}"
    print(f"[Publish] YouTube: {url}")
    return url


# ── TIKTOK ──────────────────────────────────────────────────

def upload_to_tiktok(video_path: str, script_data: dict) -> str:
    try:
        from tiktok_uploader.upload import upload_video
        upload_video(
            filename=video_path,
            description=f"{script_data['caption']} {script_data['hashtags']}"[:2200],
            cookies="tiktok_cookies.txt"
        )
        print("[Publish] TikTok uploaded")
        return "TikTok posted"
    except ImportError:
        print("[Publish] Install: pip install tiktok-uploader")
        return ""
    except Exception as e:
        print(f"[Publish] TikTok failed: {e}")
        return ""


# ── COMBINED ────────────────────────────────────────────────

def publish_video(video_path: str, script_data: dict) -> dict:
    """Publish to all platforms. Returns dict of results."""
    results = {}
    results["youtube"] = upload_to_youtube(video_path, script_data)
    results["tiktok"]  = upload_to_tiktok(video_path, script_data)
    results["x"]       = post_to_x(script_data)
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
