#!/usr/bin/env python3
"""Local-only sanity check. Does not print secrets."""

import os
from dotenv import load_dotenv

REPO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(REPO_DIR, "config", ".env"))

key_id = os.getenv("KALSHI_KEY_ID", "")
key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
env = os.getenv("KALSHI_ENV", "demo")

print(f"KALSHI_ENV={env}")
print(f"KALSHI_KEY_ID set? {'yes' if bool(key_id) else 'no'}")
print(f"KALSHI_PRIVATE_KEY_PATH set? {'yes' if bool(key_path) else 'no'}")

if key_path:
    exists = os.path.exists(key_path)
    print(f"Private key file exists? {'yes' if exists else 'no'}")
    if exists:
        with open(key_path, 'r', encoding='utf-8', errors='ignore') as f:
            head = f.read(1200)
        ok = "BEGIN RSA PRIVATE KEY" in head or "BEGIN PRIVATE KEY" in head
        print(f"Private key file looks like PEM? {'yes' if ok else 'no'}")
