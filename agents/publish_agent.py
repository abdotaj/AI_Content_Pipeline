# ============================================================
#  agents/publish_agent.py  —  Posts to YouTube, TikTok, Instagram & Facebook
#  X/Twitter removed (requires paid plan)
# ============================================================
import os
import config as _cfg
from config import (
    TIKTOK_SESSION_ID, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET,
    INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_BUSINESS_ID,
    FACEBOOK_ACCESS_TOKEN, FACEBOOK_PAGE_ID,
)

# Token file: channel configs expose YOUTUBE_TOKEN_FILE; fall back to default
_YOUTUBE_TOKEN_FILE = getattr(_cfg, "YOUTUBE_TOKEN_FILE", "youtube_token.json")


# ── YOUTUBE ─────────────────────────────────────────────────

def upload_to_youtube(video_path: str, script_data: dict) -> str:
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("[Publish] Install: pip install google-api-python-client google-auth")
        return ""

    TOKEN_FILE = _YOUTUBE_TOKEN_FILE
    if not os.path.exists(TOKEN_FILE):
        print("[Publish] YouTube token not found. Skipping.")
        return ""

    try:
        import json as _json

        creds = Credentials.from_authorized_user_file(
            TOKEN_FILE,
            ["https://www.googleapis.com/auth/youtube.upload"]
        )

        if creds.expired and creds.refresh_token:
            print("[Publish] YouTube token expired — refreshing...")
            creds.refresh(Request())
            # Preserve channel_id when re-saving refreshed token
            with open(TOKEN_FILE, "r+", encoding="utf-8") as f:
                existing = _json.load(f)
                updated  = _json.loads(creds.to_json())
                if "channel_id" in existing:
                    updated["channel_id"] = existing["channel_id"]
                f.seek(0); f.truncate()
                _json.dump(updated, f, indent=2)
            print("[Publish] YouTube token refreshed.")

        # Read channel_id saved during auth flow (informational / future CMS use)
        with open(TOKEN_FILE, encoding="utf-8") as f:
            _token_meta = _json.load(f)
        _channel_id = _token_meta.get("channel_id", "")
        if _channel_id:
            print(f"[Publish] Uploading to YouTube channel: {_channel_id}")

        youtube = build("youtube", "v3", credentials=creds)

        title    = script_data.get("title", "Untitled")
        caption  = script_data.get("caption", script_data.get("title", ""))
        hashtags = script_data.get("hashtags", "")
        keywords = script_data.get("keywords", [])

        body = {
            "snippet": {
                "title": title,
                "description": f"{caption}\n\n{hashtags}".strip(),
                "tags": keywords,
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
        _caption  = script_data.get("caption", script_data.get("title", ""))
        _hashtags = script_data.get("hashtags", "")
        caption = f"{_caption} {_hashtags}".strip()[:2200]

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


# ── INSTAGRAM ───────────────────────────────────────────────

def upload_to_instagram(video_path: str, script_data: dict) -> str:
    """Upload video to Instagram Reels via Instagram Graph API."""
    try:
        import requests

        _caption  = script_data.get("caption", script_data.get("title", ""))
        _hashtags = script_data.get("hashtags", "")
        caption = f"{_caption} {_hashtags}".strip()[:2200]
        base_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_BUSINESS_ID}"

        # Step 1: Create media container
        container_r = requests.post(
            f"{base_url}/media",
            params={
                "media_type": "REELS",
                "video_url": video_path,
                "caption": caption,
                "access_token": INSTAGRAM_ACCESS_TOKEN,
            }
        )
        container_r.raise_for_status()
        container_id = container_r.json()["id"]

        # Step 2: Publish the container
        publish_r = requests.post(
            f"{base_url}/media_publish",
            params={
                "creation_id": container_id,
                "access_token": INSTAGRAM_ACCESS_TOKEN,
            }
        )
        publish_r.raise_for_status()
        media_id = publish_r.json()["id"]

        url = f"https://www.instagram.com/p/{media_id}/"
        print(f"[Publish] Instagram: {url}")
        return url

    except Exception as e:
        print(f"[Publish] Instagram failed: {e}")
        return ""


# ── FACEBOOK ────────────────────────────────────────────────

def upload_to_facebook(video_path: str, script_data: dict) -> str:
    """Upload video to a Facebook Page via Graph API (graph-video endpoint)."""
    try:
        import requests

        _caption  = script_data.get("caption", script_data.get("title", ""))
        _hashtags = script_data.get("hashtags", "")
        description = f"{_caption} {_hashtags}".strip()[:63206]
        upload_url = f"https://graph-video.facebook.com/v18.0/{FACEBOOK_PAGE_ID}/videos"

        with open(video_path, "rb") as video_file:
            upload_r = requests.post(
                upload_url,
                data={
                    "title": script_data.get("title", "Untitled"),
                    "description": description,
                    "access_token": FACEBOOK_ACCESS_TOKEN,
                },
                files={"source": video_file}
            )

        print(f"[Publish] Facebook response HTTP {upload_r.status_code}: {upload_r.text}")

        upload_r.raise_for_status()
        video_id = upload_r.json()["id"]

        url = f"https://www.facebook.com/video/{video_id}/"
        print(f"[Publish] Facebook: {url}")
        return url

    except Exception as e:
        print(f"[Publish] Facebook failed: {e}")
        return ""


# ── COMBINED ────────────────────────────────────────────────

def publish_video(video_path: str, script_data: dict) -> dict:
    """Publish based on language: English → YouTube + Facebook, Arabic → TikTok + Instagram."""
    results = {}
    language = script_data.get("language", "english")

    if language == "english":
        results["youtube"] = upload_to_youtube(video_path, script_data)
        results["facebook"] = upload_to_facebook(video_path, script_data)
    else:
        results["tiktok"] = upload_to_tiktok(video_path, script_data)
        results["instagram"] = upload_to_instagram(video_path, script_data)

    return results


def tiktok_auth_flow():
    """Interactive TikTok OAuth flow with PKCE — single run, saves tiktok_token.json."""
    import base64
    import hashlib
    import json
    import os as _os
    import requests
    import urllib.parse
    from urllib.parse import urlparse, parse_qs
    from config import TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET

    # Step 1: Generate PKCE values (RFC 7636 S256)
    code_verifier = base64.urlsafe_b64encode(_os.urandom(32)).rstrip(b'=').decode()
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode('ascii')).digest()).rstrip(b'=').decode()

    # Step 2: Build and print auth URL
    is_sandbox = TIKTOK_CLIENT_KEY.startswith("sb")
    env_label = "SANDBOX" if is_sandbox else "PRODUCTION"
    redirect_uri = (
        "http://localhost:8080/"
        if is_sandbox else
        "https://abdotaj.github.io/AI_Content_Pipeline/"
    )

    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?client_key={TIKTOK_CLIENT_KEY}&scope=user.info.basic,video.publish,video.upload&response_type=code&redirect_uri={redirect_uri}&code_challenge={code_challenge}&code_challenge_method=S256"

    print(f"\n[TikTok Auth] Starting OAuth flow... ({env_label})")
    print(f"[TikTok Auth] Client key:      {TIKTOK_CLIENT_KEY}")
    print(f"[TikTok Auth] Redirect URI:    {redirect_uri}")
    print(f"[TikTok Auth] code_verifier:   {code_verifier} (len={len(code_verifier)})")
    print(f"[TikTok Auth] code_challenge:  {code_challenge} (len={len(code_challenge)})")
    print(f"\n[TikTok Auth] Full auth URL:\n  {auth_url}\n")
    print("1. Open the URL above in your browser and authorize the app.")
    if is_sandbox:
        print("   (Sandbox: browser will redirect to http://localhost:8080/?code=...)")

    # Step 3: Get code from user (accepts raw code or full redirect URL)
    raw = input("\n2. Paste the 'code' or the full redirect URL: ").strip()
    if "?" in raw or "&" in raw:
        params = parse_qs(urlparse(raw).query)
        code = params.get("code", [raw])[0]
    else:
        code = raw
    print(f"[TikTok Auth] code (raw):             {code}")
    code = urllib.parse.unquote(code)
    print(f"[TikTok Auth] code (after unquote):  {code}")
    code = urllib.parse.unquote(code)
    print(f"[TikTok Auth] code (after unquote²): {code}")

    # Step 4: Exchange code for token
    raw_body = f"client_key={TIKTOK_CLIENT_KEY}&client_secret={TIKTOK_CLIENT_SECRET}&code={urllib.parse.quote(code, safe='')}&grant_type=authorization_code&redirect_uri={redirect_uri}&code_verifier={code_verifier}"
    print(f"\n[TikTok Auth] Raw POST body:\n  {raw_body}")

    token_r = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        data=raw_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    print(f"\n[TikTok Auth] Token exchange response (HTTP {token_r.status_code}):")
    print(f"  {token_r.text}")
    token_r.raise_for_status()
    token_data = token_r.json()

    # Step 5: Save token
    with open("tiktok_token.json", "w") as f:
        json.dump(token_data, f, indent=2)

    access_token = token_data.get("access_token", "")
    print(f"\n[Auth] TikTok token saved to tiktok_token.json")
    print(f"       Access token expires in {token_data.get('expires_in', '?')} seconds")
    if access_token:
        print(f"       Set TIKTOK_SESSION_ID={access_token} in your .env")
    else:
        print(f"       Warning: no access_token in response: {token_data}")


def youtube_auth_flow(token_file: str = "youtube_token.json") -> None:
    """
    Full YouTube OAuth flow with channel selection.
    Lists all channels on the authenticated Google account,
    prompts the user to pick one, saves credentials + channel_id
    to token_file.

    Usage:
        python agents/publish_agent.py --auth-youtube
        python agents/publish_agent.py --auth-youtube --channel darkcrimed
        python agents/publish_agent.py --auth-youtube --channel shopmart
    """
    import json
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    client_config = {
        "installed": {
            "client_id": YOUTUBE_CLIENT_ID,
            "client_secret": YOUTUBE_CLIENT_SECRET,
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }

    print(f"\n[Auth] Starting YouTube OAuth flow → will save to: {token_file}")
    print("[Auth] A browser window will open. Sign in and grant access.\n")

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ]
    )
    creds = flow.run_local_server(port=0)

    # ── List channels on this account ────────────────────────
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(
        part="snippet", mine=True, maxResults=50
    ).execute()
    channels = resp.get("items", [])

    channel_id = ""

    if not channels:
        print("[Auth] No YouTube channels found on this account.")

    elif len(channels) == 1:
        ch = channels[0]
        channel_id = ch["id"]
        name   = ch["snippet"]["title"]
        handle = ch["snippet"].get("customUrl", channel_id)
        print(f"\n[Auth] One channel found: {name} ({handle})")
        print(f"       Channel ID: {channel_id}")

    else:
        print("\n[Auth] Channels on this Google account:")
        for i, ch in enumerate(channels, 1):
            name   = ch["snippet"]["title"]
            handle = ch["snippet"].get("customUrl", ch["id"])
            print(f"  [{i}] {name} ({handle})")

        while True:
            raw = input(f"\nSelect channel [1-{len(channels)}]: ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(channels):
                selected   = channels[int(raw) - 1]
                channel_id = selected["id"]
                name       = selected["snippet"]["title"]
                handle     = selected["snippet"].get("customUrl", channel_id)
                print(f"[Auth] Selected: {name} ({handle})")
                print(f"       Channel ID: {channel_id}")
                break
            print(f"  Invalid — enter a number between 1 and {len(channels)}.")

    # ── Save credentials + channel_id ────────────────────────
    token_data = json.loads(creds.to_json())
    if channel_id:
        token_data["channel_id"] = channel_id

    with open(token_file, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    print(f"\n[Auth] Token saved to {token_file}")
    if channel_id:
        print(f"       Uploads from this token will go to channel: {channel_id}")


if __name__ == "__main__":
    import sys

    # ── Parse --channel argument ─────────────────────────────
    _CHANNEL_TOKEN_MAP = {
        "darkcrimed": "youtube_token_darkcrimed.json",
        "shopmart":   "youtube_token_shopmart.json",
    }

    def _get_arg(flag: str) -> str:
        """Return the value after `flag` in sys.argv, or ''."""
        try:
            return sys.argv[sys.argv.index(flag) + 1]
        except (ValueError, IndexError):
            return ""

    if "--auth-youtube" in sys.argv:
        channel_slug = _get_arg("--channel")
        out_file = _CHANNEL_TOKEN_MAP.get(channel_slug, "youtube_token.json")
        youtube_auth_flow(token_file=out_file)

    elif "--auth-tiktok" in sys.argv:
        tiktok_auth_flow()
