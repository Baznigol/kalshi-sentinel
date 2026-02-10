"""Notifications.

Supports:
- Desktop notification (macOS) via osascript
- Telegram via Bot API (requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)

All optional; failures should not crash the runner.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

import requests


def notify_desktop(title: str, message: str) -> None:
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{message.replace("\"", "\\\"")}" with title "{title.replace("\"", "\\\"")}"',
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def notify_telegram(text: str) -> Optional[dict]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        # don't raise; return details for logging if needed
        try:
            return r.json()
        except Exception:
            return {"ok": False, "status": r.status_code, "text": r.text[:200]}
    except Exception:
        return None
