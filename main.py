#!/usr/bin/env python3

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

import os
import time
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
from rdap import RdapClient
from telegram import Bot
import firebase_admin
from firebase_admin import credentials, messaging

# Load .env
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Initialize Firebase Admin SDK if service key exists
SERVICE_ACCOUNT_KEY = Path(__file__).parent / "serviceAccountKey.json"
fcm_initialized = False

if SERVICE_ACCOUNT_KEY.exists():
    try:
        cred = credentials.Certificate(str(SERVICE_ACCOUNT_KEY))
        firebase_admin.initialize_app(cred)
        fcm_initialized = True
    except Exception as e:
        print(f"Failed to initialize Firebase Admin SDK: {e}")
else:
    print("Warning: serviceAccountKey.json not found. FCM notifications are disabled.")

# Load domains from file
with open("domains.txt", "r") as f:
    DOMAINS = [
        line.strip().lower()
        for line in f
        if line.strip() and not line.startswith("#")
    ]

telegram_enabled = False
bot = None
if BOT_TOKEN and CHAT_ID:
    try:
        bot = Bot(token=BOT_TOKEN)
        telegram_enabled = True
    except Exception as e:
        print(f"Failed to initialize Telegram Bot: {e}")
else:
    print("Warning: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. Telegram alerts are disabled.")

rdap = RdapClient()

# Simple persistent cache to throttle availability notifications across cron runs
CACHE_PATH = Path(__file__).parent / ".domain_cache.json"


def load_cache():
    try:
        if CACHE_PATH.exists():
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_cache(data):
    try:
        CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _rdap_field(obj, name, default=None):
    """Safely get a field from an RDAP result which may be a dict or object."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)

    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            return getter(name, default)
        except Exception:
            pass

    # common attribute name variants
    candidates = [name]
    if name == "status":
        candidates += ["statuses", "status_list"]
    if name in ("expiration_date", "expiry", "expires"):
        candidates += ["expiration_date", "expiry", "expires", "expiration"]

    for attr in candidates:
        if hasattr(obj, attr):
            try:
                return getattr(obj, attr)
            except Exception:
                continue

    # try __dict__ fallback
    try:
        data = getattr(obj, "__dict__", None)
        if isinstance(data, dict) and name in data:
            return data.get(name, default)
    except Exception:
        pass

    return default


def send(msg):
    if not telegram_enabled or bot is None:
        return

    def _send_sync():
        try:
            asyncio.run(bot.send_message(chat_id=CHAT_ID, text=msg, connect_timeout=3.0, read_timeout=3.0))
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")

    import threading
    threading.Thread(target=_send_sync, daemon=True).start()


def send_fcm_notification(title, body, category="System Alerts", priority="High"):
    if not fcm_initialized:
        print(f"Skipping FCM notification (FCM not initialized): {title}")
        return
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={
                "title": title,
                "message": body,
                "category": category,
                "priority": priority,
                "timestamp": str(int(time.time() * 1000))
            },
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="monitor_alerts",
                    default_sound=True,
                    default_vibrate_timings=True
                )
            ),
            fcm_options=messaging.FCMOptions(
                analytics_label="domain-watcher-alerts"
            ),
            topic="all"
        )
        messaging.send(message)
    except Exception as e:
        print(f"Failed to send FCM notification: {e}")


cache = load_cache()

for domain in DOMAINS:
    try:
        result = rdap.get_domain(domain)

        status = _rdap_field(result, "status", [])
        expiry = _rdap_field(result, "expiration_date")

        if isinstance(status, (list, tuple)):
            status_text = " ".join([str(s) for s in status]).lower()
        else:
            status_text = str(status or "").lower()

        state = "active"

        if "pending delete" in status_text:
            state = "pending_delete"
        elif "redemption" in status_text:
            state = "grace_period"

        # Only send a Telegram notification for non-active states
        if state != "active":
            msg = (
                f"🌐 Domain: {domain}\n"
                f"Status: {state}\n"
                f"RDAP: {status_text or 'active'}\n"
                f"Expiry: {expiry}"
            )
            send(msg)
            send_fcm_notification(
                title=f"Domain Status Alert: {domain}",
                body=f"Domain '{domain}' status changed to {state} (RDAP: {status_text or 'active'}).",
                category="System Alerts",
                priority="High"
            )

        # If domain was previously marked available, reset its counter
        entry = cache.get(domain, {})
        if entry.get("last_state") == "available":
            cache[domain] = {"available_count": 0, "last_state": "not_available"}
            save_cache(cache)

    except Exception as e:
        error = str(e).lower()

        if (
            "not found" in error
            or "404" in error
            or "no object found" in error
        ):
            # Domain appears available. Throttle notifications: allow only first 10 consecutive sends.
            entry = cache.get(domain, {})
            count = int(entry.get("available_count", 0)) + 1

            if count <= 10:
                msg = f"🔥 DOMAIN AVAILABLE\n\n{domain}\n\n(notification {count}/10)"
                send(msg)
                send_fcm_notification(
                    title="Domain Available!",
                    body=f"Domain '{domain}' appears to be available for registration! (Check {count}/10)",
                    category="Emergency Notifications",
                    priority="Critical"
                )

            cache[domain] = {"available_count": count, "last_state": "available"}
            save_cache(cache)
        else:
            msg = f"⚠️ Error checking {domain}\n\n{e}"
            send(msg)
            send_fcm_notification(
                title="Domain Check Error",
                body=f"An error occurred while checking '{domain}': {e}",
                category="System Alerts",
                priority="Medium"
            )

    time.sleep(2)