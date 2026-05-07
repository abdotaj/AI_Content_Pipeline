# pipelines/approval.py — Blocking Telegram approval gate
#
# Every major pipeline stage calls wait_for_approval() before continuing.
# The pipeline blocks until the user replies with a recognised command.
#
# AUTO_APPROVE=1 disables all gates — pipeline runs fully autonomous.
# Default: AUTO_APPROVE=0 (human approval required at each stage).
#
# Supported commands (case-insensitive, leading / optional):
#   /approve   — proceed to next stage
#   /rewrite   — regenerate scripts  (scripts gate)
#   /rerender  — rebuild videos      (render gate)
#   /publish   — same as /approve at the render/upload gate
#   /retry     — alias for /approve
#   /cancel    — stop pipeline safely

import os
import time
import requests

_COMMAND_ALIASES: dict[str, str] = {
    "approve":  "approve",
    "ok":       "approve",
    "yes":      "approve",
    "go":       "approve",
    "continue": "approve",
    "next":     "approve",
    "retry":    "approve",
    "publish":  "publish",
    "upload":   "publish",
    "rewrite":  "rewrite",
    "rerender": "rerender",
    "re-render":"rerender",
    "cancel":   "cancel",
    "stop":     "cancel",
    "abort":    "cancel",
    "no":       "cancel",
}


def wait_for_approval(
    stage_name: str,
    available_commands: list | None = None,
    mode: str = "PIPELINE",
) -> str:
    """
    Pause the pipeline at *stage_name* and block until a Telegram command.

    Sends a "[MODE PAUSED]" notification, then polls every 10 s until the
    user replies with a recognised command.  Returns the resolved command
    string (lowercase, no leading slash):  "approve" / "rewrite" /
    "rerender" / "publish" / "cancel".

    Exits immediately when AUTO_APPROVE=1 is set in the environment
    (returns "approve" without any Telegram interaction).

    *available_commands* is shown in the notification only — all aliases
    are always accepted regardless of this list.
    """
    if os.getenv("AUTO_APPROVE", "0").strip() == "1":
        print(f"[APPROVAL] AUTO_APPROVE=1 — skipping gate: {stage_name}")
        return "approve"

    _cmds = available_commands or [
        "approve", "rewrite", "rerender", "publish", "cancel"
    ]

    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            raise ValueError("empty credentials")
    except Exception:
        print(f"[APPROVAL] Telegram not configured — auto-approving: {stage_name}")
        return "approve"

    base    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    chat_id = TELEGRAM_CHAT_ID

    # Advance the update offset so we ONLY react to messages sent AFTER this
    _offset: int | None = None
    try:
        r = requests.get(
            f"{base}/getUpdates",
            params={"timeout": 0, "limit": 1},
            timeout=10,
        )
        _upds = r.json().get("result", [])
        if _upds:
            _offset = _upds[-1]["update_id"] + 1
    except Exception:
        pass

    # Build the notification message
    _cmd_lines = "\n".join(f"  /{c}" for c in _cmds)
    _notif = (
        f"[{mode.upper()} PAUSED]\n"
        f"Stage: {stage_name}\n\n"
        f"Commands:\n{_cmd_lines}\n\n"
        f"Waiting for approval..."
    )
    try:
        requests.post(
            f"{base}/sendMessage",
            json={"chat_id": chat_id, "text": _notif},
            timeout=15,
        )
    except Exception as e:
        print(f"[APPROVAL] Telegram send failed: {e} — auto-approving")
        return "approve"

    print(f"[APPROVAL] Paused at '{stage_name}' — accepted: {_cmds}")

    while True:
        time.sleep(10)
        try:
            params: dict = {
                "timeout": 0,
                "limit":   10,
                "allowed_updates": ["message"],
            }
            if _offset is not None:
                params["offset"] = _offset
            r = requests.get(f"{base}/getUpdates", params=params, timeout=15)
            if not r.ok:
                continue
            updates = r.json().get("result", [])
        except Exception:
            continue

        for upd in updates:
            _offset = upd["update_id"] + 1
            msg_obj = upd.get("message", {})
            if str(msg_obj.get("chat", {}).get("id", "")) != str(chat_id):
                continue
            text = (msg_obj.get("text") or "").strip()
            cmd  = text.lower().lstrip("/").strip()

            if cmd not in _COMMAND_ALIASES:
                continue  # unknown input — ignore, keep waiting

            resolved = _COMMAND_ALIASES[cmd]
            print(f"[APPROVAL] '{cmd}' → {resolved}")
            try:
                requests.post(
                    f"{base}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": f"[{mode.upper()}] /{resolved} — proceeding.",
                    },
                    timeout=10,
                )
            except Exception:
                pass
            return resolved
