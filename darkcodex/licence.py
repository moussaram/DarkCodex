import datetime as dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DATA_PATH = Path(os.environ.get("DARKCODEX_LICENSE_PATH", Path.home() / ".darkcodex_data.json"))
FREE_DAILY_LIMIT = 20

SUPABASE_URL = os.environ.get("DARKCODEX_SUPABASE_URL", "https://jjbcoflysycachhjfxsz.supabase.co")
SUPABASE_ANON_KEY = os.environ.get("DARKCODEX_SUPABASE_ANON_KEY", "")

LIMIT_MESSAGE = """? Limite gratuite atteinte (20/20) !
? Passe a la version Pro - Prix : 9900 FCFA
? Paye via Moov Money au : +2290154189985
? Envoie ta capture + ton email a : WhatsApp : +2290143866096
? Apres confirmation, tape /activate pour entrer ta cle"""


def today() -> str:
    return dt.date.today().isoformat()


def default_data() -> dict:
    return {
        "date": today(),
        "free_requests": 0,
        "license_key": "",
        "license_email": "",
        "pro_active": False,
    }


def load_data(path: Path = DATA_PATH) -> dict:
    if not path.exists():
        data = default_data()
        save_data(data, path)
        return data
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        data = default_data()

    merged = {**default_data(), **data}
    if merged.get("date") != today():
        merged["date"] = today()
        merged["free_requests"] = 0
        save_data(merged, path)
    return merged


def save_data(data: dict, path: Path = DATA_PATH) -> None:
    if path.parent != Path("."):
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def supabase_headers(key: str = SUPABASE_ANON_KEY) -> dict:
    if not key:
        raise RuntimeError("DARKCODEX_SUPABASE_ANON_KEY is not configured.")
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def verify_license(license_key: str, timeout: int = 20) -> tuple[bool, str]:
    license_key = license_key.strip()
    if not license_key:
        return False, ""

    base_url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/licences"
    params = {
        "key": f"eq.{license_key}",
        "actif": "eq.true",
        "select": "key,email,actif",
        "limit": "1",
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    try:
        request = urllib.request.Request(url, headers=supabase_headers(), method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except RuntimeError:
        return False, ""
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False, ""
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False, ""

    if not rows:
        return False, ""
    return bool(rows[0].get("actif")), str(rows[0].get("email") or "")


def refresh_license_status(path: Path = DATA_PATH) -> dict:
    data = load_data(path)
    license_key = str(data.get("license_key") or "")
    if not license_key:
        data["pro_active"] = False
        save_data(data, path)
        return data
    active, email = verify_license(license_key)
    data["pro_active"] = active
    if email:
        data["license_email"] = email
    save_data(data, path)
    return data


def activate_license(license_key: str, path: Path = DATA_PATH) -> tuple[bool, str]:
    active, email = verify_license(license_key)
    data = load_data(path)
    if not active:
        data["pro_active"] = False
        save_data(data, path)
        return False, "Cle Pro invalide ou inactive."
    data["license_key"] = license_key.strip()
    data["license_email"] = email
    data["pro_active"] = True
    save_data(data, path)
    return True, "Licence Pro activee. Mode PRO illimite."


def status_text(path: Path = DATA_PATH, refresh: bool = False) -> str:
    data = refresh_license_status(path) if refresh else load_data(path)
    if data.get("pro_active"):
        email = data.get("license_email") or "email non renseigne"
        return f"Mode actuel: PRO illimite ({email})"
    used = int(data.get("free_requests") or 0)
    remaining = max(0, FREE_DAILY_LIMIT - used)
    return f"Mode actuel: Gratuit - {remaining}/{FREE_DAILY_LIMIT} requetes restantes aujourd'hui"


def consume_request(path: Path = DATA_PATH) -> tuple[bool, str]:
    data = refresh_license_status(path)
    if data.get("pro_active"):
        return True, ""
    used = int(data.get("free_requests") or 0)
    if used >= FREE_DAILY_LIMIT:
        return False, LIMIT_MESSAGE
    data["free_requests"] = used + 1
    save_data(data, path)
    return True, ""
