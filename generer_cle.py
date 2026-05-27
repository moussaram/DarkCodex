#!/usr/bin/env python3
import os
import secrets
import string

import requests

SUPABASE_URL = os.environ.get("DARKCODEX_SUPABASE_URL", "https://jjbcoflysycachhjfxsz.supabase.co")
SUPABASE_SERVICE_KEY = os.environ.get("DARKCODEX_SUPABASE_SERVICE_KEY", "")


def generate_key() -> str:
    alphabet = string.ascii_uppercase + string.digits
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "DARK-" + "-".join(groups)


def insert_license(email: str, license_key: str) -> None:
    if not SUPABASE_SERVICE_KEY:
        raise RuntimeError("DARKCODEX_SUPABASE_SERVICE_KEY is not configured.")
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/licences"
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    payload = {
        "key": license_key,
        "email": email,
        "actif": True,
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()


def main() -> int:
    email = input("Email du client: ").strip()
    if not email:
        print("Email obligatoire.")
        return 2

    license_key = generate_key()
    try:
        insert_license(email, license_key)
    except RuntimeError as exc:
        print(f"Configuration manquante: {exc}")
        return 2
    except requests.RequestException as exc:
        print(f"Erreur Supabase: {exc}")
        return 1

    print("Cle Pro generee:")
    print(license_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
