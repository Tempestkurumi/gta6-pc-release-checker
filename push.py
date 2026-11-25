#!/usr/bin/env python3
"""Web Push for the GTA 6 PC release checker. Used by the GitHub workflows, not by you.

  python3 push.py notify        send notify.json (written by check.py) to everyone who wants it
  python3 push.py subscribe     add/update a device   (env SUB_JSON, SUB_PREFS, LABEL)
  python3 push.py unsubscribe   remove a device       (env ENDPOINT)
  python3 push.py test          send a test to one device after a short wait (env ENDPOINT, DELAY)

Needs the library "pywebpush" (the workflows install it) and the secret VAPID_PRIVATE_KEY.
Devices are stored in subscriptions.json. Dead devices (browser says 404/410) are removed.
"""

import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

SUBS_FILE = os.environ.get("SUBS_FILE", "subscriptions.json")
VAPID_FILE = os.environ.get("VAPID_FILE", "vapid.json")
NOTIFY_FILE = os.environ.get("NOTIFY_FILE", "notify.json")
MAX_DEVICES = 500
DEFAULT_PREFS = {"official": True, "released": True, "rumor": False}


def die(msg):
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(1)


def b64u_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# ---------------------------------------------------------------- storage

def load_subs():
    try:
        with open(SUBS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except ValueError:
        die(f"{SUBS_FILE} is not valid JSON")


def save_subs(subs):
    with open(SUBS_FILE, "w", encoding="utf-8") as fh:
        json.dump(subs, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def clean_prefs(raw):
    try:
        p = json.loads(raw) if isinstance(raw, str) and raw.strip() else dict(raw or {})
    except ValueError:
        p = {}
    return {k: bool(p.get(k, DEFAULT_PREFS[k])) for k in DEFAULT_PREFS}


def valid_subscription(sub):
    """Check a browser PushSubscription (as JSON) before we store it."""
    try:
        url = urlparse(sub["endpoint"])
        local_ok = os.environ.get("PUSH_ALLOW_LOCAL") == "1" and url.hostname in ("localhost", "127.0.0.1")
        if url.scheme != "https" and not local_ok:
            return "endpoint must be https"
        if len(b64u_decode(sub["keys"]["p256dh"])) != 65:
            return "p256dh key has the wrong size"
        if len(b64u_decode(sub["keys"]["auth"])) != 16:
            return "auth secret has the wrong size"
    except Exception as e:  # noqa: BLE001
        return f"not a valid subscription ({e})"
    return ""


# ---------------------------------------------------------------- VAPID

def vapid_private():
    key = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    return key


def vapid_public_of(private_key):
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    v = Vapid.from_string(private_key)
    raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    return b64u(raw)


def check_vapid_matches(private_key):
    """The public key on the website (vapid.json) must belong to the secret private key."""
    try:
        with open(VAPID_FILE, encoding="utf-8") as fh:
            site_pub = json.load(fh)["publicKey"]
    except Exception:  # noqa: BLE001
        die(f"{VAPID_FILE} is missing or broken. Upload the vapid.json made by make-vapid.html.")
    try:
        mine = vapid_public_of(private_key)
    except Exception as e:  # noqa: BLE001
        die(f"The VAPID_PRIVATE_KEY secret is not a valid key ({e}).")
    if mine != site_pub:
        die("vapid.json and the VAPID_PRIVATE_KEY secret do not belong together. "
            "Both must come from the same run of make-vapid.html.")


# ---------------------------------------------------------------- sending

def send_one(sub, payload, private_key):
    from pywebpush import WebPushException, webpush
    repo = os.environ.get("REPO") or os.environ.get("GITHUB_REPOSITORY") or "owner/repo"
    # "sub" tells the push service who is sending: this site's address (origin only)
    contact = f"https://{repo.split('/')[0].lower()}.github.io"
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                data=json.dumps(payload), vapid_private_key=private_key,
                vapid_claims={"sub": contact},
                ttl=86400, timeout=20, headers={"Urgency": "high"})
        return "ok"
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        return "gone" if code in (404, 410) else f"error {code}: {e}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def short(sub):
    host = urlparse(sub["endpoint"]).hostname or "?"
    return f"{sub.get('label') or 'device'} ({host})"


# ---------------------------------------------------------------- actions

def do_notify():
    if not os.path.exists(NOTIFY_FILE):
        print("Nothing to notify about.")
        return 0
    with open(NOTIFY_FILE, encoding="utf-8") as fh:
        n = json.load(fh)
    key = vapid_private()
    if not key:
        print("No VAPID_PRIVATE_KEY secret set: skipping push notifications.")
        return 0
    check_vapid_matches(key)
    cat = n.get("category", "rumor")
    payload = {"title": n.get("title", "GTA 6 PC release update"), "body": n.get("body", ""),
               "tag": "gta6pc"}
    subs, keep, sent = load_subs(), [], 0
    for sub in subs:
        prefs = clean_prefs(sub.get("prefs"))
        if not prefs.get(cat, False):
            keep.append(sub)
            print("skip (not wanted):", short(sub))
            continue
        res = send_one(sub, payload, key)
        print(f"{res:>8}  {short(sub)}")
        if res == "gone":
            continue            # the browser says this device no longer exists
        keep.append(sub)
        sent += res == "ok"
    if len(keep) != len(subs):
        save_subs(keep)
    print(f"Sent to {sent} device(s).")
    return 0


def do_subscribe():
    try:
        sub = json.loads(os.environ.get("SUB_JSON", ""))
    except ValueError:
        die("SUB_JSON is not valid JSON")
    problem = valid_subscription(sub)
    if problem:
        die(problem)
    key = vapid_private()
    if key:
        check_vapid_matches(key)      # catch a wrong/missing vapid.json early
    entry = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["keys"]["p256dh"],
                                                     "auth": sub["keys"]["auth"]},
             "prefs": clean_prefs(os.environ.get("SUB_PREFS", "")),
             "label": re.sub(r"[^\w .·()+-]", "", os.environ.get("LABEL", ""))[:60],
             "added": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    subs = [s for s in load_subs() if s.get("endpoint") != entry["endpoint"]]
    subs.append(entry)
    subs = subs[-MAX_DEVICES:]
    save_subs(subs)
    print("Registered:", short(entry), "| devices now:", len(subs))
    return 0


def do_unsubscribe():
    endpoint = os.environ.get("ENDPOINT", "")
    subs = load_subs()
    keep = [s for s in subs if s.get("endpoint") != endpoint]
    if len(keep) != len(subs):
        save_subs(keep)
    print("Removed" if len(keep) != len(subs) else "Device was not registered.")
    return 0


def do_test():
    endpoint = os.environ.get("ENDPOINT", "")
    key = vapid_private()
    if not key:
        die("The VAPID_PRIVATE_KEY secret is not set (Settings > Secrets and variables > Actions).")
    check_vapid_matches(key)
    sub = next((s for s in load_subs() if s.get("endpoint") == endpoint), None)
    if not sub:
        die("This device is not registered yet. Turn notifications on and save first.")
    try:
        delay = max(0, min(60, int(os.environ.get("DELAY", "15") or 0)))
    except ValueError:
        delay = 15
    print(f"Waiting {delay} s so you can close the app...")
    time.sleep(delay)
    res = send_one(sub, {"title": "GTA 6 PC checker",
                         "body": "Test notification: it works! \U0001F389", "tag": "gta6pc-test"}, key)
    print(f"{res:>8}  {short(sub)}")
    if res == "gone":
        save_subs([s for s in load_subs() if s.get("endpoint") != endpoint])
        die("The browser says this device is no longer subscribed. Turn notifications off and on again.")
    if res != "ok":
        die(res)
    return 0


def main():
    actions = {"notify": do_notify, "subscribe": do_subscribe,
               "unsubscribe": do_unsubscribe, "test": do_test}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        die("usage: push.py notify|subscribe|unsubscribe|test")
    return actions[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
