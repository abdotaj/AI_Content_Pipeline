# ============================================================
#  agents/publish_agent.py  —  YouTube auto-publish + Telegram short delivery
#
#  Long videos  → auto-upload to YouTube (EN or AR channel)
#  Short videos → sent to Telegram for manual TikTok/Instagram posting
#  All other platforms (TikTok, Instagram, Facebook, X) → skipped with log
# ============================================================
import os
import config as _cfg
from config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET

# Token files for each Dark Crime Decoded channel
_TOKEN_EN = "youtube_token_darkcrimed_en.json"
_TOKEN_AR = "youtube_token_darkcrimed_ar.json"


# ── YOUTUBE ─────────────────────────────────────────────────

def build_youtube_description(script_data: dict, chapters: str) -> str:
    """Build a rich YouTube description with chapter markers and social links."""
    caption  = script_data.get("caption", script_data.get("title", ""))
    hashtags = script_data.get("hashtags", "")
    if isinstance(hashtags, list):
        hashtags = " ".join(hashtags)

    return (
        f"{caption}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"CHAPTERS\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{chapters.strip()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Subscribe for daily true crime documentaries\n"
        f"TikTok: @DarkCrimeDecoded\n"
        f"Instagram: @DarkCrimeDecoded\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{hashtags}\n\n"
        f"#DarkCrimeDecoded #TrueCrime #Documentary"
    ).strip()


def _get_video_duration(video_path: str) -> float:
    """Return video duration in seconds, or 999 on error."""
    try:
        from moviepy import VideoFileClip
        with VideoFileClip(video_path) as clip:
            return clip.duration
    except Exception:
        return 999.0


def upload_to_youtube(video_path: str, script_data: dict, token_file: str = None) -> str:
    """Upload a long-form video to YouTube. Returns the video URL or ''."""
    import traceback as _tb

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("[Publish] Install: pip install google-api-python-client google-auth")
        return ""

    # Choose token file by language if not explicitly provided
    if token_file is None:
        lang = script_data.get("language", "english").lower()
        token_file = _TOKEN_AR if lang == "arabic" else _TOKEN_EN

    print(f"[Publish] Starting YouTube upload: {video_path}")
    print(f"[Publish] Token file: {token_file}")

    if not os.path.exists(video_path):
        print(f"[Publish] ERROR: Video file not found: {video_path}")
        return ""
    if not os.path.exists(token_file):
        print(f"[Publish] ERROR: Token file not found: {token_file}")
        return ""

    # Inspect token before loading credentials
    try:
        import json as _json
        with open(token_file, encoding="utf-8") as _f:
            _peek = _json.load(_f)
        print(f"[Publish] Token channel_id: {_peek.get('channel_id', 'MISSING')}")
        print(f"[Publish] Token has refresh_token: {'refresh_token' in _peek}")
        print(f"[Publish] Token has client_id: {'client_id' in _peek}")
    except Exception as _e:
        print(f"[Publish] WARNING: Could not inspect token file: {_e}")

    # Select OAuth client credentials matching the token file
    if "ar" in os.path.basename(token_file):
        client_id     = os.getenv("YOUTUBE_CLIENT_ID_AR", YOUTUBE_CLIENT_ID)
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET_AR", YOUTUBE_CLIENT_SECRET)
        print("[Publish] YouTube credentials: AR channel")
    else:
        client_id     = os.getenv("YOUTUBE_CLIENT_ID_EN", YOUTUBE_CLIENT_ID)
        client_secret = os.getenv("YOUTUBE_CLIENT_SECRET_EN", YOUTUBE_CLIENT_SECRET)
        print("[Publish] YouTube credentials: EN channel")

    print(f"[Publish] client_id set: {bool(client_id and client_id not in ('', 'YOUR_YT_CLIENT_ID'))}")

    try:
        import json as _json

        creds = Credentials.from_authorized_user_file(
            token_file,
            ["https://www.googleapis.com/auth/youtube.upload"]
        )
        print(f"[Publish] Credentials loaded — expired: {creds.expired}, valid: {creds.valid}")

        if creds.expired and creds.refresh_token:
            print("[Publish] YouTube token expired — refreshing...")
            creds.refresh(Request())
            with open(token_file, "r+", encoding="utf-8") as f:
                existing = _json.load(f)
                updated  = _json.loads(creds.to_json())
                if "channel_id" in existing:
                    updated["channel_id"] = existing["channel_id"]
                f.seek(0); f.truncate()
                _json.dump(updated, f, indent=2)
            print("[Publish] YouTube token refreshed.")

        with open(token_file, encoding="utf-8") as f:
            _token_meta = _json.load(f)
        _channel_id = _token_meta.get("channel_id", "")
        if _channel_id:
            print(f"[Publish] Uploading to YouTube channel: {_channel_id}")

        youtube = build("youtube", "v3", credentials=creds)
        print("[Publish] YouTube API client built successfully")

        title    = script_data.get("title", "Untitled")
        hashtags = script_data.get("hashtags", "")
        keywords = script_data.get("keywords", [])

        topic_str = script_data.get("topic", "")
        niche_str = script_data.get("niche", "")
        seo_tags  = list(keywords) + [
            topic_str, niche_str,
            "true crime", "documentary", "dark crime decoded",
            "real story", "netflix", "crime series",
            "what really happened", "based on true story",
        ]
        seo_tags = [t for t in seo_tags if t]

        chapters    = script_data.get("chapters", "")
        description = (
            build_youtube_description(script_data, chapters)
            if chapters
            else f"{script_data.get('caption', title)}\n\n{hashtags}".strip()
        )

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": seo_tags,
                "categoryId": "25"
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

        video_id = response["id"]
        url = f"https://youtube.com/watch?v={video_id}"
        print(f"[Publish] YouTube upload complete: {url}")
        return url

    except Exception as e:
        print(f"[Publish] YouTube upload ERROR: {e}")
        _tb.print_exc()
        return ""


# ── TELEGRAM SHORT DELIVERY ──────────────────────────────────

def send_short_to_telegram(short_video_path: str, script_data: dict) -> bool:
    """
    Send the short video file to Telegram for manual posting to TikTok/Instagram.
    Returns True on success.
    """
    try:
        import requests as _req
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    except ImportError:
        print("[Publish] Telegram config not available — skipping short delivery")
        return False

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Publish] Skipping Telegram delivery: bot token or chat ID not configured")
        return False

    if not os.path.exists(short_video_path):
        print(f"[Publish] Short video not found, skipping Telegram delivery: {short_video_path}")
        return False

    title    = script_data.get("title", "Untitled")
    lang     = script_data.get("language", "english").title()
    caption  = script_data.get("caption", title)
    hashtags = script_data.get("hashtags", "")
    if isinstance(hashtags, list):
        hashtags = " ".join(hashtags)

    msg = (
        f"Short video ready for manual publish ({lang})\n\n"
        f"{title}\n\n"
        f"{caption}\n\n"
        f"{hashtags}"
    ).strip()[:1024]

    base_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

    try:
        with open(short_video_path, "rb") as vf:
            r = _req.post(
                f"{base_url}/sendVideo",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": msg},
                files={"video": vf},
                timeout=120,
            )
        if r.ok:
            print(f"[Publish] Short video sent to Telegram: {short_video_path}")
            return True
        else:
            print(f"[Publish] Telegram sendVideo failed ({r.status_code}): {r.text[:200]}")
    except Exception as e:
        print(f"[Publish] Telegram delivery error: {e}")

    # Fallback: send as document if sendVideo fails (large files)
    try:
        with open(short_video_path, "rb") as df:
            r = _req.post(
                f"{base_url}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": msg},
                files={"document": df},
                timeout=120,
            )
        if r.ok:
            print(f"[Publish] Short video sent to Telegram as document: {short_video_path}")
            return True
        else:
            print(f"[Publish] Telegram sendDocument failed ({r.status_code}): {r.text[:200]}")
    except Exception as e:
        print(f"[Publish] Telegram document fallback error: {e}")

    return False


# ── SKIPPED PLATFORMS ────────────────────────────────────────

def upload_to_tiktok(video_path: str, script_data: dict) -> str:
    print("[Publish] Manual publish: TikTok - skipping (send short to Telegram instead)")
    return ""


def upload_to_instagram(video_path: str, script_data: dict) -> str:
    print("[Publish] Manual publish: Instagram - skipping (send short to Telegram instead)")
    return ""


def upload_to_facebook(video_path: str, script_data: dict) -> str:
    print("[Publish] Manual publish: Facebook - skipping")
    return ""


def upload_to_x(video_path: str, script_data: dict) -> str:
    print("[Publish] Manual publish: X/Twitter - skipping")
    return ""


# ── MAIN PUBLISH ENTRY POINT ─────────────────────────────────

def publish_video(video_path: str, script_data: dict,
                  short_video_path: str = None) -> dict:
    """
    Publish a long video to YouTube for the correct channel (EN or AR).
    Then send the short video (if provided) to Telegram for manual posting.

    short_video_path can also be supplied via script_data['short_video_path'].
    """
    results = {}
    lang = script_data.get("language", "english").lower()

    # ── Auto-upload long video to YouTube ────────────────────
    print(f"[Publish] Publishing {lang} long video to YouTube...")
    results["youtube"] = upload_to_youtube(video_path, script_data)

    # ── Log skipped platforms ─────────────────────────────────
    for platform in ("TikTok", "Instagram", "Facebook", "X/Twitter"):
        print(f"[Publish] Manual publish: {platform} - skipping")

    # ── Send short clip to Telegram ───────────────────────────
    short_path = short_video_path or script_data.get("short_video_path", "")
    if short_path:
        results["telegram_short"] = send_short_to_telegram(short_path, script_data)
    else:
        print("[Publish] No short video path provided — skipping Telegram delivery")

    return results


# ── AUTH FLOWS (local setup only) ────────────────────────────

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

    code_verifier  = base64.urlsafe_b64encode(_os.urandom(32)).rstrip(b'=').decode()
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('ascii')).digest()
    ).rstrip(b'=').decode()

    is_sandbox = TIKTOK_CLIENT_KEY.startswith("sb")
    env_label  = "SANDBOX" if is_sandbox else "PRODUCTION"
    redirect_uri = (
        "http://localhost:8080/"
        if is_sandbox else
        "https://abdotaj.github.io/AI_Content_Pipeline/"
    )

    auth_url = (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={TIKTOK_CLIENT_KEY}"
        f"&scope=user.info.basic,video.publish,video.upload"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    print(f"\n[TikTok Auth] Starting OAuth flow... ({env_label})")
    print(f"[TikTok Auth] Client key:      {TIKTOK_CLIENT_KEY}")
    print(f"[TikTok Auth] Redirect URI:    {redirect_uri}")
    print(f"[TikTok Auth] code_verifier:   {code_verifier} (len={len(code_verifier)})")
    print(f"[TikTok Auth] code_challenge:  {code_challenge} (len={len(code_challenge)})")
    print(f"\n[TikTok Auth] Full auth URL:\n  {auth_url}\n")
    print("1. Open the URL above in your browser and authorize the app.")
    if is_sandbox:
        print("   (Sandbox: browser will redirect to http://localhost:8080/?code=...)")

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
    print(f"[TikTok Auth] code (after unquote2): {code}")

    raw_body = (
        f"client_key={TIKTOK_CLIENT_KEY}"
        f"&client_secret={TIKTOK_CLIENT_SECRET}"
        f"&code={urllib.parse.quote(code, safe='')}"
        f"&grant_type=authorization_code"
        f"&redirect_uri={redirect_uri}"
        f"&code_verifier={code_verifier}"
    )
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

    with open("tiktok_token.json", "w") as f:
        json.dump(token_data, f, indent=2)

    access_token = token_data.get("access_token", "")
    print(f"\n[Auth] TikTok token saved to tiktok_token.json")
    print(f"       Access token expires in {token_data.get('expires_in', '?')} seconds")
    if not access_token:
        print(f"       Warning: no access_token in response: {token_data}")


def youtube_auth_flow(token_file: str = "youtube_token.json") -> None:
    """
    Full YouTube OAuth flow with channel selection.
    Saves credentials + channel_id to token_file.

    Usage:
        python agents/publish_agent.py --auth-youtube
        python agents/publish_agent.py --auth-youtube --channel darkcrimed_en
        python agents/publish_agent.py --auth-youtube --channel darkcrimed_ar
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

    print(f"\n[Auth] Starting YouTube OAuth flow -> will save to: {token_file}")
    print("[Auth] A browser window will open. Sign in and grant access.\n")

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ]
    )
    creds = flow.run_local_server(port=0)

    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(
        part="snippet", mine=True, maxResults=50
    ).execute()
    channels = resp.get("items", [])

    channel_id = ""

    if not channels:
        print("[Auth] No YouTube channels found on this account.")
    elif len(channels) == 1:
        ch         = channels[0]
        channel_id = ch["id"]
        name       = ch["snippet"]["title"]
        handle     = ch["snippet"].get("customUrl", channel_id)
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
            print(f"  Invalid -- enter a number between 1 and {len(channels)}.")

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

    _CHANNEL_TOKEN_MAP = {
        "darkcrimed":    _TOKEN_EN,
        "darkcrimed_en": _TOKEN_EN,
        "darkcrimed_ar": _TOKEN_AR,
        "shopmart":      "youtube_token_shopmart.json",
    }

    def _get_arg(flag: str) -> str:
        try:
            return sys.argv[sys.argv.index(flag) + 1]
        except (ValueError, IndexError):
            return ""

    if "--auth-youtube" in sys.argv:
        channel_slug = _get_arg("--channel")
        out_file = _CHANNEL_TOKEN_MAP.get(
            channel_slug,
            f"youtube_token_{channel_slug}.json" if channel_slug else "youtube_token.json",
        )
        youtube_auth_flow(token_file=out_file)

    elif "--auth-tiktok" in sys.argv:
        tiktok_auth_flow()
