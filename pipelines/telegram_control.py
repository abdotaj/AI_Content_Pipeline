# pipelines/telegram_control.py — Telegram remote control for the pipeline
#
# Runs a daemon thread polling Telegram every 12 s.
# Supported commands (owner chat only):
#   /cancel  — create .cancel_pipeline flag; pipeline exits cleanly
#   /status  — current stage, topic, elapsed time
#   /script  — re-send the latest generated script
#   /logs    — tail of the live log file
#
# The pipeline:
#   1. Creates TelegramController(mode="fast")
#   2. Calls .start() after the topic is known
#   3. Calls .update_stage(name, detail) at each major step
#   4. Calls .is_cancelled() at safe checkpoint; exits if True
#   5. Calls .stop() at the end

import os
import time
import threading
import requests

CANCEL_FLAG = ".cancel_pipeline"
_LOG_FILE   = "output/pipeline_current.log"


class TelegramController:
    """Background Telegram command handler for the pipeline."""

    def __init__(self, topic: str = "", mode: str = "fast"):
        self._topic     = topic
        self._mode      = mode
        self._stage     = "Starting"
        self._stage_t   = time.time()
        self._t0        = time.time()
        self._cancelled = False
        self._latest_script: dict = {}
        self._offset: int | None  = None
        self._thread: threading.Thread | None = None
        self._running   = False

        # Remove stale cancel flag from a previous run
        try:
            if os.path.exists(CANCEL_FLAG):
                os.remove(CANCEL_FLAG)
        except Exception:
            pass

        # Start a fresh log file for this run
        try:
            os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
            with open(_LOG_FILE, "w", encoding="utf-8") as _f:
                _f.write(f"[TelegramCtrl] Pipeline started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        except Exception:
            pass

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background polling. Call after the topic is known."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop, name="TelegramCtrl", daemon=True
        )
        self._thread.start()
        print("[TelegramCtrl] Listener started — /cancel /status /script /logs")

    def stop(self) -> None:
        self._running = False

    def update_stage(self, stage: str, detail: str = "") -> None:
        """Record a new pipeline stage for /status and /logs."""
        self._stage   = stage
        self._stage_t = time.time()
        line = f"[STAGE] {stage}" + (f": {detail}" if detail else "")
        self._append_log(line)

    def set_topic(self, topic: str) -> None:
        self._topic = topic

    def set_latest_script(self, script_data: dict) -> None:
        self._latest_script = script_data

    def add_log(self, line: str) -> None:
        """Write a log line to the live log file (called by _log() in fast_pipeline)."""
        self._append_log(line)

    def is_cancelled(self) -> bool:
        return self._cancelled or os.path.exists(CANCEL_FLAG)

    # ── Internals ────────────────────────────────────────────────────────────

    def _append_log(self, line: str) -> None:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._process_commands()
            except Exception:
                pass
            time.sleep(12)

    def _process_commands(self) -> None:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        base   = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        params = {"timeout": 0, "limit": 10, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset

        try:
            r = requests.get(f"{base}/getUpdates", params=params, timeout=15)
            if not r.ok:
                return
            updates = r.json().get("result", [])
        except Exception:
            return

        for upd in updates:
            self._offset = upd["update_id"] + 1
            msg     = upd.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue
            text = (msg.get("text") or "").strip()
            cmd  = text.lower()
            if cmd in ("/cancel", "cancel"):
                self._handle_cancel(base)
            elif cmd == "/status":
                self._handle_status(base)
            elif cmd == "/script":
                self._handle_script(base)
            elif cmd == "/logs":
                self._handle_logs(base)

    def _send(self, base: str, text: str) -> None:
        from config import TELEGRAM_CHAT_ID
        try:
            requests.post(
                f"{base}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
                timeout=15,
            )
        except Exception:
            pass

    def _handle_cancel(self, base: str) -> None:
        self._cancelled = True
        try:
            with open(CANCEL_FLAG, "w", encoding="utf-8") as f:
                f.write(f"cancelled via Telegram at {time.strftime('%H:%M:%S')}\n")
        except Exception:
            pass
        elapsed = int(time.time() - self._t0)
        self._send(
            base,
            f"[{self._mode.upper()}] Cancel received at "
            f"{elapsed // 60}m {elapsed % 60}s.\n"
            "Pipeline will stop safely after current operation.",
        )
        print("[TelegramCtrl] CANCEL received")

    def _handle_status(self, base: str) -> None:
        elapsed   = int(time.time() - self._t0)
        stage_el  = int(time.time() - self._stage_t)
        self._send(
            base,
            f"[{self._mode.upper()} STATUS]\n"
            f"Topic:    {self._topic or '(selecting...)'}\n"
            f"Stage:    {self._stage}\n"
            f"In stage: {stage_el // 60}m {stage_el % 60}s\n"
            f"Elapsed:  {elapsed // 60}m {elapsed % 60}s",
        )

    def _handle_script(self, base: str) -> None:
        if not self._latest_script:
            self._send(base, f"[{self._mode.upper()}] No script generated yet.")
            return
        try:
            from agent.notify_agent import send_english_script_preview
            send_english_script_preview(self._latest_script, label="SCRIPT (on demand)")
        except Exception as e:
            self._send(base, f"[{self._mode.upper()}] Script send failed: {e}")

    def _handle_logs(self, base: str) -> None:
        try:
            with open(_LOG_FILE, encoding="utf-8") as f:
                lines = f.readlines()
            tail = "".join(lines[-60:]).strip()
        except Exception:
            tail = ""
        if not tail:
            self._send(base, f"[{self._mode.upper()}] No log lines yet.")
            return
        text = f"[{self._mode.upper()} LOGS — last 60 lines]\n{tail}"
        if len(text) > 3900:
            text = "[...truncated]\n" + text[-3850:]
        self._send(base, text)
