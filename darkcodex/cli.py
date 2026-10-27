#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import (
    __version__,
    agent_debug,
    agent_fichiers,
    agent_git,
    agent_session,
    agent_terminal,
    agent_tests,
    cout,
    historique,
    langues,
    licence,
    memory,
    navigateur,
    updater,
    voix,
)
from .fsutil import atomic_write_text

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except OSError:
            pass

APP_NAME = "DarkCodex"
HOME = Path.home()
STATE_DIR = Path(os.environ.get("DARKCODEX_HOME", HOME / ".darkcodex"))
DB_PATH = STATE_DIR / "darkcodex.sqlite"
CONFIG_PATH = STATE_DIR / "config.json"
AUTH_PATH = STATE_DIR / "auth.json"
HANDSHAKE_PATH = STATE_DIR / "handshake.json"

_DOTENV_CHARGE = False


def ensure_dotenv() -> None:
    """C15 : charge .env uniquement si DARKCODEX_LOAD_DOTENV est défini."""
    global _DOTENV_CHARGE
    if _DOTENV_CHARGE:
        return
    actif = str(os.environ.get("DARKCODEX_LOAD_DOTENV") or "").strip().lower()
    if actif not in {"1", "true", "oui", "yes", "on"}:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
    _DOTENV_CHARGE = True

# API DarkCodex (device code, /chat, /solde, /verify-token).
API_BASE = os.environ.get("DARKCODEX_API_BASE", "https://darkcodex.onrender.com").rstrip("/")
ABONNEMENTS_URL = f"{API_BASE}/abonnements"
DEVICE_URL = f"{API_BASE}/device"
LOGIN_POLL_SECONDS = 5
LOGIN_TIMEOUT_SECONDS = 600  # 10 minutes, comme la durée de vie d'un code

SYSTEM_PROMPT = """You are DarkCodex, a powerful terminal coding assistant.
You provide direct, technical, and complete help for coding, debugging, project analysis,
terminal workflows, and authorized security work in the user's environment.
Be concise, technical, and action-oriented."""

DEFAULT_CONFIG = {
    "provider": "auto",
    "model": "gemini-2.5-flash",
    "api_key_env": "DARKCODEX_API_KEY",
    "api_key_file": str(STATE_DIR / "api_key"),
    "api_timeout_seconds": 1200,
    "editor": os.environ.get("EDITOR", "nano"),
    "max_context_files": 12,
    "app_id": "dc-cli",
    "confirm_always": False,
    "chat_include_files": True,
}


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.init()

    def init(self) -> None:
        self.db.executescript(
            """
            create table if not exists memories (
                id integer primary key autoincrement,
                key text not null,
                value text not null,
                tags text not null default '',
                created_at text not null
            );
            create table if not exists sessions (
                id integer primary key autoincrement,
                cwd text not null,
                provider text not null,
                created_at text not null
            );
            create table if not exists messages (
                id integer primary key autoincrement,
                session_id integer not null,
                role text not null,
                content text not null,
                meta_json text not null default '',
                created_at text not null,
                foreign key(session_id) references sessions(id)
            );
            create table if not exists runs (
                id integer primary key autoincrement,
                cwd text not null,
                command text not null,
                exit_code integer not null,
                output text not null,
                created_at text not null
            );
            create table if not exists modifications (
                id integer primary key autoincrement,
                session_id integer not null,
                path text not null,
                action text not null,
                created_at text not null,
                foreign key(session_id) references sessions(id)
            );
            """
        )
        self.db.commit()
        # Migration : les bases créées avant C12 n'ont pas meta_json.
        try:
            self.db.execute(
                "alter table messages add column meta_json text not null default ''"
            )
            self.db.commit()
        except sqlite3.OperationalError:
            pass

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def now(self) -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def add_memory(self, key: str, value: str, tags: str = "") -> None:
        self.db.execute(
            "insert into memories(key, value, tags, created_at) values (?, ?, ?, ?)",
            (key, value, tags, self.now()),
        )
        self.db.commit()

    def search_memories(self, query: str = "", limit: int = 20) -> list[sqlite3.Row]:
        if query:
            pattern = f"%{query}%"
            cur = self.db.execute(
                """
                select * from memories
                where key like ? or value like ? or tags like ?
                order by id desc limit ?
                """,
                (pattern, pattern, pattern, limit),
            )
        else:
            cur = self.db.execute("select * from memories order by id desc limit ?", (limit,))
        return list(cur.fetchall())

    def new_session(self, cwd: Path, provider: str) -> int:
        cur = self.db.execute(
            "insert into sessions(cwd, provider, created_at) values (?, ?, ?)",
            (str(cwd), provider, self.now()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def add_message(self, session_id: int, role: str, content: str, meta_json: str = "") -> None:
        self.db.execute(
            "insert into messages(session_id, role, content, meta_json, created_at) values (?, ?, ?, ?, ?)",
            (session_id, role, content, meta_json, self.now()),
        )
        self.db.commit()

    def recent_messages(self, cwd: Path, limit: int = 8) -> list[sqlite3.Row]:
        cur = self.db.execute(
            """
            select m.role, m.content from messages m
            join sessions s on s.id = m.session_id
            where s.cwd = ?
            order by m.id desc limit ?
            """,
            (str(cwd), limit),
        )
        return list(reversed(cur.fetchall()))

    def log_modification(self, session_id: int, path: str, action: str) -> None:
        self.db.execute(
            "insert into modifications(session_id, path, action, created_at) values (?, ?, ?, ?)",
            (session_id, path, action, self.now()),
        )
        self.db.commit()

    def recent_modifications(self, session_id: int, limit: int = 50) -> list[sqlite3.Row]:
        cur = self.db.execute(
            "select path, action, created_at from modifications where session_id = ? order by id desc limit ?",
            (session_id, limit),
        )
        return list(reversed(cur.fetchall()))

    def messages_session(self, session_id: int, limit: int = 200) -> list[sqlite3.Row]:
        cur = self.db.execute(
            "select role, content, meta_json, created_at from messages where session_id = ? order by id desc limit ?",
            (session_id, limit),
        )
        return list(reversed(cur.fetchall()))

    def recent_runs(self, cwd: Path, limit: int = 50) -> list[sqlite3.Row]:
        cur = self.db.execute(
            "select command, exit_code, created_at from runs where cwd = ? order by id desc limit ?",
            (str(cwd), limit),
        )
        return list(reversed(cur.fetchall()))

    def import_messages(self, session_id: int, messages: list[tuple[str, str]]) -> int:
        count = 0
        for role, content in messages:
            if not content.strip():
                continue
            self.db.execute(
                "insert into messages(session_id, role, content, created_at) values (?, ?, ?, ?)",
                (session_id, role, content[:20000], self.now()),
            )
            count += 1
        self.db.commit()
        return count

    def log_run(self, cwd: Path, command: str, exit_code: int, output: str) -> None:
        self.db.execute(
            "insert into runs(cwd, command, exit_code, output, created_at) values (?, ?, ?, ?, ?)",
            (str(cwd), command, exit_code, output[-12000:], self.now()),
        )
        self.db.commit()


def load_config() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return {**DEFAULT_CONFIG, **data}
        except (OSError, json.JSONDecodeError):
            pass
    atomic_write_text(CONFIG_PATH, json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    atomic_write_text(CONFIG_PATH, json.dumps(config, indent=2) + "\n")


def detect_provider(config: dict) -> str:
    provider = config.get("provider", "auto")
    if provider != "auto":
        return provider
    api_key_env = str(config.get("api_key_env") or "DARKCODEX_API_KEY")
    api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
    if os.environ.get(api_key_env) or api_key_file.exists():
        return "darkcodex"
    if shutil.which("codex"):
        return "codex"
    return "local"


IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", "build", "dist",
    ".venv", "venv", ".idea", ".gradle",
}
SKIPPED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".jar", ".exe", ".dll",
    ".so", ".dylib", ".bin", ".class", ".pyc", ".pyo",
}


def is_sensitive_path(relative_path: str) -> bool:
    lower = relative_path.lower()
    name = Path(lower).name
    sensitive_names = {
        ".env", ".env.local", ".env.production", ".env.development",
        "auth.json", "credentials.json", "service-account.json",
        "service_account.json", "id_rsa", "id_ed25519", "id_ecdsa",
        "id_dsa", "known_hosts",
    }
    if name in sensitive_names:
        return True
    if name.startswith(".env."):
        return True
    return (
        name.endswith(".pem")
        or name.endswith(".key")
        or name.endswith(".p12")
        or name.endswith(".pfx")
        or name.endswith("keystore")
        or name.endswith("secrets.json")
        or name.endswith("secret.json")
        or name.endswith("credentials.json")
        or "secrets/" in lower
        or ".ssh/" in lower
        or ".aws/" in lower
        or "/secrets/" in f"/{lower}"
    )


def project_files(root: Path, limit: int, include_sensitive: bool = False) -> list[Path]:
    """Fichiers envoyés à l'IA. Jamais de fichiers sensibles ni de liens symboliques."""
    files: list[Path] = []
    seen = set()
    for path in root.rglob("*"):
        if len(files) >= limit:
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in IGNORED_DIRS for part in rel_parts):
                continue
            relative_path = path.relative_to(root).as_posix()
            if not include_sensitive and is_sensitive_path(relative_path):
                continue
            real = str(path.resolve())
            if real in seen:
                continue
            seen.add(real)
            stat_result = path.stat()
        except (OSError, ValueError):
            continue
        if stat_result.st_size > 80_000:
            continue
        if path.suffix.lower() in SKIPPED_SUFFIXES:
            continue
        files.append(path)
    return files


def recent_ai_answers(cwd: Path, limit: int = 10) -> list[sqlite3.Row]:
    """C12 : dernières réponses IA (rôle assistant) du projet, avec leur meta."""
    with Store() as store:
        cur = store.db.execute(
            """
            select m.content, m.meta_json, m.created_at from messages m
            join sessions s on s.id = m.session_id
            where s.cwd = ? and m.role = 'assistant'
            order by m.id desc limit ?
            """,
            (str(cwd), limit),
        )
        return list(reversed(cur.fetchall()))


def _redact_source(text: str) -> str:
    text = re.sub(r"(?i)\b(api[_-]?key|secret|token|password|passphrase|private[_-]?key)\b\s*[:=]\s*['\"][^'\"]+['\"]", r"\1=[REDACTED]", text)
    text = re.sub(r"AIza[0-9A-Za-z_\-]{35}", "[REDACTED]", text)
    text = re.sub(r"(?i)glpat-[0-9A-Za-z_\-]{20}", "[REDACTED]", text)
    text = re.sub(r"(?i)xox[baprs]-[0-9A-Za-z-]{10,}", "[REDACTED]", text)
    text = re.sub(r"(?i)AKIA[0-9A-Z]{16}", "[REDACTED]", text)
    return text


def _redact(text: str, *, in_file: bool = False) -> str:
    text = _redact_source(text)
    if in_file:
        return text
    text = re.sub(
        r"(?im)^(\s*(?:export\s+)?[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASS|PASSWORD)[A-Z0-9_]*\s*=\s*).+$",
        r"\1[REDACTED]",
        text,
    )
    if "[REDACTED]" in text:
        text += "\n[REDACTED] : valeurs sensibles retirées de l'envoi."
    return text


def build_context(cwd: Path, store: Store, config: dict, include_files: bool = True) -> str:
    parts = [f"Working directory: {cwd}"]
    regles = load_agents_rules(cwd)
    if regles:
        parts.append("Project rules to always follow (AGENTS.md):")
        parts.append(_redact(regles))

    contexte = ""
    try:
        contexte = str(memory.get_project_context() or "").strip()
    except (OSError, json.JSONDecodeError):
        contexte = ""
    if contexte:
        parts.append("Project context:")
        parts.append(_redact(contexte))

    memories = store.search_memories(limit=8)
    if memories:
        parts.append("Relevant saved memory:")
        for mem in memories:
            parts.append(f"- {mem['key']}: {_redact(str(mem['value']))} [{mem['tags']}]")
    recent = store.recent_messages(cwd)
    if recent:
        parts.append("Recent conversation:")
        for msg in recent:
            parts.append(f"{msg['role']}: {_redact(str(msg['content'])[:1000])}")
    if not include_files:
        return "\n".join(parts)
    files = project_files(cwd, int(config.get("max_context_files", 12)))
    if files:
        parts.append("Project file snapshot:")
        for file in files:
            rel = file.relative_to(cwd)
            try:
                content = file.read_text(errors="replace")[:5000]
            except OSError:
                continue
            parts.append(f"\n--- {rel} ---\n{_redact(content, in_file=True)}")
    return "\n".join(parts)


def config_timeout(config: dict, default: int = 1200) -> int:
    try:
        return max(30, int(config.get("api_timeout_seconds", default)))
    except (TypeError, ValueError):
        return default


def darkcodex_api_answer(prompt: str, config: dict) -> tuple[int, str]:
    # C08 : fichier de clé d'abord, puis variable d'environnement. Jamais de
    # clé intégrée au code client.
    api_key_env = str(config.get("api_key_env") or "DARKCODEX_API_KEY")
    api_key = ""
    api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
    try:
        if api_key_file.exists():
            api_key = api_key_file.read_text().strip()
    except OSError:
        api_key = ""
    if not api_key:
        api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return 2, (
            f"Missing API key. Set it with:\n"
            f"  darkcodex config api-key YOUR_API_KEY\n\n"
            f"Windows env alternative:\n"
            f"  setx {api_key_env} \"YOUR_API_KEY\"\n\n"
            f"Termux/Linux env alternative:\n"
            f"  export {api_key_env}='YOUR_API_KEY'\n\n"
            f"DarkCodex also accepts {STATE_DIR / 'api_key'} as a fallback."
        )

    model = str(config.get("model") or DEFAULT_CONFIG["model"])
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config_timeout(config)) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        try:
            error_data = json.loads(error_body)
            message = str(error_data.get("error", {}).get("message") or "")
            status = str(error_data.get("error", {}).get("status") or "")
            if status == "INVALID_ARGUMENT" and "API key not valid" in message:
                return exc.code, (
                    "DarkCodex API key invalid. The Gemini key is configured, but Google rejected it.\n"
                    "Create or copy a valid key from Google AI Studio, then run:\n"
                    "  darkcodex config api-key YOUR_VALID_GEMINI_KEY"
                )
        except json.JSONDecodeError:
            pass
        return exc.code, f"DarkCodex API HTTP {exc.code}:\n{error_body}"
    except urllib.error.URLError as exc:
        return 1, f"DarkCodex API network error: {exc.reason}"
    except TimeoutError:
        return 124, f"DarkCodex API timed out after {config_timeout(config)} seconds."
    except json.JSONDecodeError as exc:
        return 1, f"DarkCodex API returned invalid JSON: {exc}"

    parts: list[str] = []
    for candidate in data.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                parts.append(text)
    if parts:
        return 0, "\n".join(parts).strip()

    return 1, "DarkCodex API returned no text:\n" + json.dumps(data, indent=2)[:4000]


def run_provider(provider: str, prompt: str, config: dict, cwd: Path) -> tuple[int, str]:
    full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
    if provider in {"darkcodex", "gemini"}:
        return darkcodex_api_answer(full_prompt, config)
    if provider == "gemini_cli":
        cmd = ["gemini", "--prompt", full_prompt]
        if config.get("model"):
            cmd.extend(["--model", str(config["model"])])
    elif provider == "codex":
        cmd = ["codex", "exec", full_prompt]
        if config.get("model"):
            cmd.extend(["--model", str(config["model"])])
    else:
        return 0, local_answer(prompt)

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            input="",
            capture_output=True,
            timeout=config_timeout(config),
        )
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0 and provider == "gemini_cli":
            fallback = ["gemini", "-p", full_prompt]
            proc = subprocess.run(fallback, cwd=cwd, text=True, input="", capture_output=True, timeout=config_timeout(config))
            output = (proc.stdout + proc.stderr).strip()
        return proc.returncode, output or "(no output)"
    except FileNotFoundError:
        return 127, f"Provider '{provider}' is not installed."
    except subprocess.TimeoutExpired:
        return 124, f"Provider '{provider}' timed out after {config_timeout(config)} seconds."


def local_answer(prompt: str) -> str:
    request = prompt.rsplit("User request:", 1)[-1].strip() if "User request:" in prompt else prompt.strip()
    return "\n".join(
        [
            "DarkCodex local mode is active.",
            "",
            "Your request was saved. Local mode can manage memory, run commands,",
            "scan files, and prepare context. For real AI answers, configure:",
            "  darkcodex config api-key YOUR_GEMINI_API_KEY",
            "  darkcodex config provider darkcodex",
            "  darkcodex config provider codex",
            "",
            "Request:",
            request,
        ]
    )


# ── Connexion DarkCodex (device code + jeton) ────────────────

BOX_LINE = "━" * 43
EQ_LINE = "═" * 42


def box(*lines: str) -> str:
    return "\n".join([BOX_LINE, *lines, BOX_LINE])


def fmt_tokens(value: object) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def plan_label(plan: object) -> str:
    return str(plan or "gratuit").capitalize()


def load_auth() -> dict | None:
    data = read_auth_file()
    if data.get("token"):
        return data
    return None


def save_auth(data: dict) -> None:
    merged = dict(data)
    if "mode" not in merged:  # ne jamais perdre le mode d'autorisation
        existant = read_auth_file().get("mode")
        if existant:
            merged["mode"] = existant
    atomic_write_text(AUTH_PATH, json.dumps(merged, indent=2) + "\n")
    try:
        AUTH_PATH.chmod(0o600)
    except OSError:
        pass


def clear_auth() -> None:
    try:
        AUTH_PATH.unlink()
    except OSError:
        pass


def appliquer_reponse_serveur(session: dict, data: dict) -> None:
    """Met à jour le solde ET le plan après une réponse de /chat."""
    session["tokens_solde"] = data.get("solde_restant", session.get("tokens_solde"))
    if data.get("plan"):
        session["plan"] = data["plan"]
    save_auth(session)


def deconnecter() -> None:
    """Révoque le jeton d'appareil côté serveur (meilleur effort), puis efface la session locale."""
    session = load_auth()
    jeton = str((session or {}).get("token") or "")
    if jeton.startswith("dkx_"):
        api_call("/logout", method="POST", token=jeton, timeout=10)
    clear_auth()


def api_call(
    path: str,
    method: str = "GET",
    payload: dict | None = None,
    token: str | None = None,
    timeout: int = 60,
) -> tuple[int, dict]:
    """Retourne (code_http, donnees). code_http vaut 0 si le serveur est injoignable."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"detail": raw[:300]}
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 0, {"detail": f"Serveur injoignable ({exc.reason}). Vérifie ta connexion internet."}
    except TimeoutError:
        return 0, {"detail": f"Le serveur {API_BASE} ne répond pas (délai dépassé)."}
    except json.JSONDecodeError:
        return 0, {"detail": "Réponse illisible du serveur DarkCodex."}


def api_error_text(data: dict) -> str:
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("status") or detail)
    if detail:
        return str(detail)
    return str(data)


def session_info(token: str) -> tuple[int, dict]:
    return api_call("/verify-token", token=token)


def ensure_session(interactive: bool = True, announce: bool = True) -> dict | None:
    """1. auth.json ? 2. /verify-token ? 3. bienvenue. 4. sinon /login."""
    session = load_auth()
    if session:
        status, data = session_info(session["token"])
        if status == 200:
            session.update(
                {
                    "email": data.get("email") or session.get("email", ""),
                    "nom": data.get("nom") or session.get("nom", ""),
                    "tokens_solde": data.get("tokens_solde", session.get("tokens_solde", 0)),
                    "tokens_utilises": data.get("tokens_utilises", 0),
                    "plan": data.get("plan") or session.get("plan") or "gratuit",
                }
            )
            save_auth(session)
            ecrire_handshake(session)  # C13 : handshake rafraîchi à chaque login
            if announce:
                print_welcome(session)
            return session
        if status == 401:
            clear_auth()
            session = None
        else:
            # Serveur hors ligne : on garde le jeton, on prévient clairement.
            print(f"{C_YELLOW}[!] {api_error_text(data)}{C_END}")
            print(f"{C_YELLOW}[!] Démarrage hors ligne, reconnexion impossible pour l'instant.{C_END}\n")
            return session

    if not interactive:
        return None
    return device_login()


def print_welcome(session: dict) -> None:
    nom = session.get("nom") or session.get("email") or "toi"
    print(
        "\n".join(
            [
                EQ_LINE,
                f"DarkCodex — Unrestricted AI v{__version__}",
                f"Bonjour {nom} ! 👋",
                "",
                f"💰 Solde : {fmt_tokens(session.get('tokens_solde'))} tokens restants",
                f"📦 Plan : {plan_label(session.get('plan'))}",
                EQ_LINE,
                "",
            ]
        )
    )


def device_login() -> dict | None:
    status, data = api_call("/device/code", method="POST")
    if status != 200:
        print(f"{C_RED}⛔ Impossible de démarrer la connexion.{C_END}")
        print(f"{C_RED}   {api_error_text(data)}{C_END}")
        print(f"{C_YELLOW}   Réessaie avec /login dès que le serveur est disponible.{C_END}\n")
        return None

    code = str(data.get("code") or "")
    lien = str(data.get("lien") or DEVICE_URL)
    try:
        duree = min(LOGIN_TIMEOUT_SECONDS, int(data.get("expire_dans") or LOGIN_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        duree = LOGIN_TIMEOUT_SECONDS

    separateur = "&" if "?" in lien else "?"
    lien_direct = f"{lien}{separateur}code={urllib.parse.quote(code)}"
    if navigateur.ouvrir(lien_direct):
        consignes = [
            "Votre navigateur s'ouvre pour vous connecter.",
            "S'il ne s'ouvre pas, ouvrez ce lien :",
            f"→ {lien_direct}",
        ]
    else:
        consignes = ["Ouvrez ce lien dans votre navigateur :", f"→ {lien_direct}"]

    print()
    print(
        box(
            "🔐 Connexion à DarkCodex",
            "",
            *consignes,
            "",
            f"Code affiché sur la page : {code}",
            "",
            "⏳ En attente de confirmation...",
        )
    )
    print()

    fin = time.time() + duree
    try:
        while time.time() < fin:
            time.sleep(LOGIN_POLL_SECONDS)
            status, data = api_call(f"/device/verify?code={urllib.parse.quote(code)}")
            if status != 200:
                print(f"{C_RED}⛔ {api_error_text(data)}{C_END}")
                print(f"{C_YELLOW}Tapez /login pour réessayer.{C_END}")
                return None
            etat = str(data.get("status") or "")
            if etat == "confirme":
                session = {
                    "token": data.get("token", ""),
                    "email": data.get("email", ""),
                    "nom": data.get("nom", ""),
                    "tokens_solde": data.get("tokens_solde", 0),
                    "plan": data.get("plan") or "gratuit",
                }
                save_auth(session)
                ecrire_handshake(session)  # C13
                print()
                print(f"✅ Connexion réussie ! Bienvenue {session.get('nom') or 'toi'} !")
                print()
                print(f"💰 Tokens gratuits : {fmt_tokens(session.get('tokens_solde'))} tokens")
                print()
                print("Commencez à utiliser DarkCodex !")
                print()
                print_welcome(session)
                return session
            if etat == "expire":
                print(f"{C_RED}Code expiré. Tapez /login pour réessayer{C_END}")
                return None
            print(".", end="", flush=True)
    except KeyboardInterrupt:
        print(f"\n{C_YELLOW}Connexion interrompue. Tapez /login pour réessayer.{C_END}")
        return None

    print(f"\n{C_RED}Code expiré. Tapez /login pour réessayer{C_END}")
    return None


def show_solde() -> None:
    session = load_auth()
    if not session:
        print("Non connecté. Tapez /login")
        return
    status, data = api_call("/solde", token=session["token"])
    if status == 401:
        clear_auth()
        print("Session expirée. Tapez /login")
        return
    if status != 200:
        print(f"{C_RED}⛔ {api_error_text(data)}{C_END}")
        return
    print(
        box(
            "💰 Votre solde DarkCodex",
            f"Tokens restants : {fmt_tokens(data.get('tokens_solde'))}",
            f"Tokens utilisés : {fmt_tokens(data.get('tokens_utilises'))}",
            f"Plan actuel : {plan_label(data.get('plan'))}",
        )
    )


def plans_text() -> str:
    return box(
        "📦 Plans disponibles",
        "⭐ Starter → 50 000 tokens = 500 FCFA",
        "🚀 Pro → 500 000 tokens = 2 500 FCFA",
        "",
        "Rechargez votre compte sur :",
        f"→ {ABONNEMENTS_URL}",
    )


def show_plans() -> None:
    print(plans_text())


def show_exhausted() -> None:
    print(
        box(
            "⛔ Vos tokens gratuits sont épuisés !",
            "",
            "Rechargez votre compte sur :",
            f"→ {ABONNEMENTS_URL}",
            "",
            "📦 Plans disponibles :",
            "⭐ Starter → 50 000 tokens = 500 FCFA",
            "🚀 Pro → 500 000 tokens = 2 500 FCFA",
        )
    )


def show_token_line(used: object, remaining: object) -> None:
    print(box(f"💬 Tokens utilisés : {fmt_tokens(used)} | 💰 Restants : {fmt_tokens(remaining)}"))


def server_answer(session: dict, prompt: str) -> tuple[int, str, dict]:
    """POST /chat. Retourne (0, reponse, donnees) ou (code_http, erreur, donnees)."""
    status, data = api_call(
        "/chat", method="POST", payload={"message": prompt}, token=session["token"], timeout=300
    )
    if status == 200:
        return 0, str(data.get("reponse") or ""), data
    return status, api_error_text(data), data


def ai_answer(provider: str, prompt: str, config: dict, cwd: Path) -> tuple[int, str]:
    """Réponse IA : via l'API DarkCodex si connecté, sinon via le fournisseur local."""
    session = load_auth()
    if session:
        ok_cout, message_cout = cout.SESSION.verifier(cout.estimer_tokens(prompt))
        if not ok_cout:
            return 1, message_cout
        code, output, data = server_answer(session, prompt)
        if code == 0:  # server_answer renvoie 0 en cas de succès (et non 200)
            cout.SESSION.ajouter(data.get("tokens_utilises"))
            appliquer_reponse_serveur(session, data)
            show_token_line(data.get("tokens_utilises"), data.get("solde_restant"))
            return 0, output
        if code == 401:
            clear_auth()
            return 1, "Session expirée. Tapez /login"
        if code == 402:
            show_exhausted()
            session["tokens_solde"] = 0
            session["plan"] = "gratuit"
            save_auth(session)
            return 1, ""
        return code, output

    ok_cout, message_cout = cout.SESSION.verifier(cout.estimer_tokens(prompt))
    if not ok_cout:
        return 1, message_cout
    allowed, limit_message = licence.consume_request()
    if not allowed:
        return 1, limit_message
    code, output = run_provider(provider, prompt, config, cwd)
    if code == 0:
        cout.SESSION.ajouter_estimation(prompt, output)
    return code, output


# ── Modes d'autorisation de l'agent (Partie 1) ────────────

MODES = ("suggest", "autoedit", "fullauto")
MODE_LABELS = {
    "suggest": "SUGGEST — je propose et je demande confirmation avant d'agir",
    "autoedit": "AUTO EDIT — je modifie les fichiers seul, je demande avant les commandes",
    "fullauto": "FULL AUTO — autonomie totale, tout est affiché en temps réel",
}
AGENTS_FILENAME = "AGENTS.md"
CONFIRM_HINT = "(o/n)"


def read_auth_file() -> dict:
    """Contenu brut de ~/.darkcodex/auth.json (jeton ET mode d'autorisation)."""
    if not AUTH_PATH.exists():
        return {}
    try:
        data = json.loads(AUTH_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


PLANS_GRATUITS = {"", "gratuit", "gratuite", "free"}


def est_pro(session: dict | None = None) -> bool:
    """Vrai seulement avec un plan payant ET des tokens restants."""
    if session is None:
        session = load_auth()
    if not session:
        return False
    plan = str(session.get("plan") or "gratuit").strip().lower()
    if plan in PLANS_GRATUITS:
        return False
    try:
        return int(session.get("tokens_solde") or 0) > 0
    except (TypeError, ValueError):
        return False


def load_mode() -> str:
    """C03 : défaut « suggest ». Un utilisateur gratuit est toujours ramené à suggest."""
    mode = str(read_auth_file().get("mode") or "suggest").strip().lower()
    if mode not in MODES:
        return "suggest"
    if mode in {"autoedit", "fullauto"} and not est_pro():
        return "suggest"
    return mode


def save_mode(mode: str) -> None:
    data = read_auth_file()
    data["mode"] = mode
    atomic_write_text(AUTH_PATH, json.dumps(data, indent=2) + "\n")
    try:
        AUTH_PATH.chmod(0o600)
    except OSError:
        pass


MOTIF_APP_ID = re.compile(r"^dc-(cli|pro|term)-[a-f0-9]{8,}$")


def ecrire_handshake(session: dict | None = None) -> dict:
    """C13 : écrit ~/.darkcodex/handshake.json (app_id, mode, pro_active)."""
    if session is None:
        session = load_auth() or {}
    config = load_config()
    app_id = str(session.get("app_id") or config.get("app_id") or "")
    if not MOTIF_APP_ID.match(app_id):
        # C13 : l'app_id doit être du type dc-cli-<hex> ; on en génère un
        # stable au premier écriture et on le garde dans la config.
        app_id = f"dc-cli-{uuid.uuid4().hex[:12]}"
        config["app_id"] = app_id
        save_config(config)
    data = {
        "app_id": app_id,
        "mode": load_mode(),
        "pro_active": est_pro(session),
        "mis_a_jour": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    atomic_write_text(HANDSHAKE_PATH, json.dumps(data, indent=2) + "\n")
    try:
        HANDSHAKE_PATH.chmod(0o600)
    except OSError:
        pass
    return data


def lire_handshake() -> dict:
    try:
        data = json.loads(HANDSHAKE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def handshake_valide() -> tuple[bool, str]:
    """C13 : (valide, raison). app_id correct + écrit dans les 7 derniers jours."""
    data = lire_handshake()
    if not data:
        return False, "aucun handshake (lance `darkcodex` ou `darkcodex doctor`)"
    app_id = str(data.get("app_id") or "")
    if not MOTIF_APP_ID.match(app_id):
        return False, "app_id invalide"
    try:
        stamp = dt.datetime.fromisoformat(str(data.get("mis_a_jour") or ""))
    except ValueError:
        return False, "date de handshake illisible"
    if dt.datetime.now(dt.timezone.utc) - stamp > dt.timedelta(days=7):
        return False, "handshake expiré (relance `darkcodex` ou `darkcodex doctor`)"
    return True, "validé"


def confirm_interactive() -> bool:
    """C13 : autorise le fullauto uniquement si handshake valide ET Pro."""
    ok, raison = handshake_valide()
    if not ok:
        print(f"⛔ Handshake invalide : {raison}")
        return False
    data = lire_handshake()
    if str(data.get("mode") or "") != "fullauto":
        print("⛔ Mode fullauto non autorisé par le handshake.")
        return False
    if not data.get("pro_active"):
        print("⛔ Ce mode est réservé aux comptes Pro. Passe à la version Pro pour l'activer.")
        return False
    return True


def confirm_user(question: str) -> bool:
    """Demande 'question (o/n)' à l'utilisateur. Non = annulation."""
    try:
        reponse = input(f"{question} {CONFIRM_HINT} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return reponse in {"o", "oui", "y", "yes"}


def load_agents_rules(cwd: Path) -> str:
    """Règles du projet définies dans AGENTS.md (Partie 7)."""
    path = cwd / AGENTS_FILENAME
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


class Ctx:
    """Contexte transmis à tous les agents : projet, IA, confirmations, journal."""

    def __init__(
        self,
        cwd: Path,
        provider: str,
        config: dict,
        store: "Store | None" = None,
        session_id: int | None = None,
        mode: str | None = None,
    ):
        self.cwd = cwd
        self.provider = provider
        self.config = config
        self.store = store
        self.session_id = session_id
        self.mode = mode if mode in MODES else load_mode()

    # ── IA ──
    def rules(self) -> str:
        return load_agents_rules(self.cwd)

    def ask(self, prompt: str) -> tuple[int, str]:
        regles = self.rules()
        if regles:
            prompt = f"Règles du projet à toujours respecter (AGENTS.md) :\n{regles}\n\n{prompt}"
        return ai_answer(self.provider, prompt, self.config, self.cwd)

    # ── Affichage ──
    @staticmethod
    def note(texte: str = "") -> None:
        print(texte)

    # ── Confirmations (C04) : toute action qui modifie ou exécute demande
    # confirmation dans tous les modes, sauf si confirm_always=True (Pro).
    def _confirme_sans_friction(self) -> bool:
        valeur = str(self.config.get("confirm_always") or "").strip().lower()
        return valeur in {"1", "true", "oui", "yes", "on"}

    def confirm_edit(self, question: str) -> bool:
        if self._confirme_sans_friction():
            return True
        return confirm_user(question)

    def confirm_command(self, question: str) -> bool:
        if self._confirme_sans_friction():
            return True
        return confirm_user(question)

    def confirm_always(self, question: str) -> bool:
        """Confirmation obligatoire, même en FULL AUTO (git push, .env, auth.json)."""
        return confirm_user(question)

    # ── Journal de session ──
    def note_modification(self, path: Path, action: str) -> None:
        if self.store is not None and self.session_id is not None:
            self.store.log_modification(self.session_id, str(path), action)

    def note_commande(self, commande: str, exit_code: int, sortie: str = "") -> None:
        if self.store is not None:
            self.store.log_run(self.cwd, commande, exit_code, sortie)


def print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No memories found.")
        return
    for row in rows:
        print(f"[{row['id']}] {row['key']} :: {row['value']} ({row['tags']})")


def cmd_ask(args: argparse.Namespace) -> int:
    ensure_dotenv()  # C15
    config = load_config()
    langues.ensure_language()
    store = Store()
    cwd = Path(args.cwd).resolve()
    provider = detect_provider(config) if not args.provider or args.provider == "auto" else args.provider
    request = " ".join(args.prompt).strip()
    if not request:
        request = sys.stdin.read().strip()
    if not request:
        print("No prompt provided.", file=sys.stderr)
        return 2
    context = (
        build_context(cwd, store, config, include_files=bool(config.get("chat_include_files", True)))
        if args.context
        else f"Working directory: {cwd}"
    )
    prompt = f"{context}\n\nUser request:\n{request}"
    session_id = store.new_session(cwd, provider)
    store.add_message(session_id, "user", request)

    tokens_used = tokens_left = None
    debut = time.time()
    session = ensure_session(interactive=False, announce=False)
    if session:
        code, output, data = server_answer(session, prompt)
        if code == 402:
            show_exhausted()
            session["tokens_solde"] = 0
            session["plan"] = "gratuit"
            save_auth(session)
            return 1
        if code == 401:
            clear_auth()
            print("Session expirée. Tapez /login", file=sys.stderr)
            return 1
        if code != 0:
            print(output, file=sys.stderr)
            return code
        appliquer_reponse_serveur(session, data)
        tokens_used = data.get("tokens_utilises")
        tokens_left = data.get("solde_restant")
    else:
        allowed, limit_message = licence.consume_request()
        if not allowed:
            print(limit_message)
            return 1
        code, output = run_provider(provider, prompt, config, cwd)

    meta = {
        "commande": "ask",
        "duree_secondes": round(time.time() - debut, 1),
        "tokens_utilises": tokens_used,
        "tokens_restants": tokens_left,
    }
    store.add_message(session_id, "assistant", output, json.dumps(meta, ensure_ascii=False))
    memory.remember_conversation(request, output)
    print(output)
    if tokens_used is not None:
        show_token_line(tokens_used, tokens_left)
    return code


C_GREEN = "\033[92m"
C_CYAN = "\033[96m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_MAGENTA = "\033[95m"
C_BOLD = "\033[1m"
C_END = "\033[0m"

BANNER = f"""{C_RED}{C_BOLD}
 ██████╗  █████╗ ██████╗ ██╗  ██╗ ██████╗ ██████╗ ██████╗ ███████╗██╗  ██╗
 ██╔══██╗██╔══██╗██╔══██╗██║ ██╔╝██╔════╝██╔═══██╗██╔══██╗██╔════╝╚██╗██╔╝
 ██║  ██║███████║██████╔╝█████╔╝ ██║     ██║   ██║██║  ██║█████╗   ╚███╔╝ 
 ██║  ██║██╔══██║██╔══██╗██╔═██╗ ██║     ██║   ██║██║  ██║██╔══╝   ██╔██╗ 
 ██████╔╝██║  ██║██║  ██║██║  ██╗╚██████╗╚██████╔╝██████╔╝███████╗██╔╝ ██╗
 ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝╚═╝  ╚═╝
{C_END}{C_CYAN}          DarkCodex v{__version__} - Unrestricted AI {C_END}
"""


def type_writer(text: str, prefix: str = "", delay: float = 0.001) -> None:
    """Animates text output character by character (C11 : 0.001 s)."""
    for line in text.splitlines():
        sys.stdout.write(prefix)
        for char in line:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(delay)
        sys.stdout.write("\n")
        sys.stdout.flush()


def handle_file_agent_command(line: str, cwd: Path, provider: str, config: dict) -> bool:
    commands = ("/lire ", "/corriger ", "/expliquer ", "/generer ", "/resume ")
    if not any(line.startswith(command) for command in commands):
        return False

    def call_ai(prompt: str) -> tuple[int, str]:
        return ai_answer(provider, prompt, config, cwd)

    if line.startswith("/resume "):
        folder = agent_fichiers.resolve_path(cwd, line.removeprefix("/resume ").strip())
        ok, prompt = agent_fichiers.summary_prompt(folder)
        if not ok:
            print(prompt)
            return True
        code, output = call_ai(prompt)
        print(output)
        if code == 0:
            memory.remember_conversation(line, output)
        return True

    if line.startswith("/generer "):
        try:
            parts = shlex.split(line, posix=False)
        except ValueError as exc:
            print(f"Commande invalide: {exc}")
            return True
        if len(parts) < 3:
            print('Usage: /generer [fichier] "[description]"')
            return True
        path = agent_fichiers.resolve_path(cwd, parts[1])
        description = " ".join(parts[2:]).strip('"')
        code, output = call_ai(agent_fichiers.generate_prompt(path, description))
        if code == 0:
            historique.enregistrer(cwd, path)
            agent_fichiers.write_generated_file(path, output)
            print(f"Fichier généré: {path}")
            memory.remember_conversation(line, f"Fichier généré: {path}")
        else:
            print(output)
        return True

    command, value = line.split(" ", 1)
    path = agent_fichiers.resolve_path(cwd, value)
    ok, content_or_error = agent_fichiers.read_text_file(path)
    if not ok:
        print(content_or_error)
        return True
    print(agent_fichiers.fichier_infos(path, content_or_error))

    mode = command.removeprefix("/")
    prompt = agent_fichiers.analysis_prompt(path, content_or_error, mode)
    code, output = call_ai(prompt)
    if mode == "corriger" and code == 0:
        backup = agent_fichiers.backup_file(path)
        historique.enregistrer(cwd, path)
        agent_fichiers.write_generated_file(path, output)
        print(f"Fichier corrigé: {path}")
        print(f"Sauvegarde créée: {backup}")
        memory.remember_conversation(line, f"Fichier corrigé: {path}")
        return True

    print(output)
    if code == 0:
        memory.remember_conversation(line, output)
    return True


def commande_historique(cwd: Path) -> None:
    print(historique.formater(historique.lister(cwd, limite=10)))


def commande_annuler(cwd: Path) -> None:
    entree = historique.prochaine_annulation(cwd)
    if entree is None:
        print("Rien à annuler.")
        return
    action = "restaurer" if entree.get("existait") else "supprimer le fichier créé"
    if not confirm_user(f"Annuler la dernière modification ({action} : {entree['chemin']}) ?"):
        print("Annulation abandonnée.")
        return
    ok, message = historique.annuler(cwd)
    print(("✅ " if ok else "⛔ ") + message)


def commande_diff(line: str, cwd: Path) -> None:
    fichier = line.removeprefix("/diff").strip() or None
    ok, texte = historique.diff_derniere(cwd, fichier)
    print(historique.colorier_diff(texte) if ok else texte)


def commande_cout(session: dict | None) -> None:
    suivi = cout.SESSION
    lignes = ["💰 Coût de la session", f"Tokens utilisés (mesurés) : {fmt_tokens(suivi.utilises)}"]
    if suivi.estimes:
        lignes.append(f"Tokens estimés (mode local) : ~{fmt_tokens(suivi.estimes)}")
    lignes.append("Limite de session : " + (fmt_tokens(suivi.limite) if suivi.limite else "aucune"))
    lignes.append(f"Confirmation au-delà de : ~{fmt_tokens(suivi.seuil)} tokens par demande")
    if session:
        lignes.append(f"Solde du compte : {fmt_tokens(session.get('tokens_solde'))}")
    print(box(*lignes))


def commande_limite(line: str) -> None:
    valeur = line.removeprefix("/limite").strip()
    if not valeur:
        limite = cout.SESSION.limite
        print(f"Limite de session : {fmt_tokens(limite)} tokens." if limite else "Aucune limite de session. Exemple : /limite 20000")
        return
    print(cout.SESSION.definir_limite(valeur)[1])


def commande_parrainer(line: str, session: dict | None) -> None:
    if not session:
        print("Connecte-toi d'abord avec /login.")
        return
    morceaux = line.split()
    if len(morceaux) >= 2 and morceaux[1].lower() == "utiliser":
        if len(morceaux) < 3:
            print("Usage : /parrainer utiliser DK-XXXXXX")
            return
        status, data = api_call("/parrainage/utiliser", method="POST", payload={"code": morceaux[2]}, token=session["token"], timeout=30)
        if status == 200:
            ajoutes = int(data.get("tokens_ajoutes") or 0)
            session["tokens_solde"] = int(session.get("tokens_solde") or 0) + ajoutes
            save_auth(session)
            print(f"✅ Code accepté : +{fmt_tokens(ajoutes)} tokens pour toi et pour ton parrain !")
        else:
            print(f"⛔ {api_error_text(data)}")
        return
    status, data = api_call("/parrainage", token=session["token"], timeout=30)
    if status != 200:
        print(f"⛔ {api_error_text(data)}")
        return
    print(box(
        "🎁 Parrainage",
        f"Ton code : {data.get('code')}",
        f"Chaque filleul : +{fmt_tokens(data.get('bonus'))} tokens pour lui ET pour toi",
        f"Filleuls : {data.get('filleuls', 0)} | Tokens gagnés : {fmt_tokens(data.get('tokens_gagnes'))}",
        f"Ton ami tape : /parrainer utiliser {data.get('code')}",
    ))


def handle_nouvelles_commandes(line: str, cwd: Path, session: dict | None) -> bool:
    commande = line.split(" ", 1)[0]
    if commande == "/historique":
        commande_historique(cwd)
    elif commande == "/annuler":
        commande_annuler(cwd)
    elif commande == "/diff":
        commande_diff(line, cwd)
    elif commande == "/cout":
        commande_cout(session)
    elif commande == "/limite":
        commande_limite(line)
    elif commande == "/parrainer":
        commande_parrainer(line, session)
    else:
        return False
    return True


def cmd_chat(args: argparse.Namespace) -> int:
    ensure_dotenv()  # C15
    config = load_config()
    if not langues.has_active_language() and sys.stdin.isatty():
        print(langues.tr("preferred_language"))
        print(langues.list_languages())
        choice = input("> ").strip()
        ok, message = langues.set_active_language(choice)
        if not ok:
            langues.ensure_language()
            print(message)
    else:
        langues.ensure_language()
    store = Store()
    cwd = Path(args.cwd).resolve()
    provider = detect_provider(config) if not args.provider or args.provider == "auto" else args.provider
    session_id = store.new_session(cwd, provider)
    ctx = Ctx(cwd, provider, config, store=store, session_id=session_id)
    print("\033[H\033[J", end="")
    print(BANNER)
    session = ensure_session(interactive=sys.stdin.isatty())
    ecrire_handshake(session or {})  # C13 : handshake rafraîchi à chaque démarrage
    print(memory.startup_message())
    print(langues.format_startup())
    print(f" {C_BOLD}STATUS:{C_END} {C_GREEN}ONLINE{C_END} | {C_BOLD}ENGINE:{C_END} {C_MAGENTA}{provider.upper()}{C_END} | {C_BOLD}PATH:{C_END} {C_CYAN}{cwd}{C_END}")
    mode = (
        f"Plan {plan_label(session.get('plan'))} - {fmt_tokens(session.get('tokens_solde'))} tokens"
        if session
        else licence.status_text(refresh=True)
    )
    print(f" {C_BOLD}PLAN:{C_END} {C_GREEN}{mode}{C_END}")
    print(f" {C_BOLD}AGENT:{C_END} {C_GREEN}{ctx.mode.upper()}{C_END} | {MODE_LABELS[ctx.mode]}")
    if ctx.rules():
        lignes_regles = len(ctx.rules().splitlines())
        print(f" {C_BOLD}RULES:{C_END} {C_GREEN}AGENTS.md chargé ({lignes_regles} lignes){C_END}")
    hs_ok, hs_raison = handshake_valide()
    if hs_ok:
        print(f" {C_BOLD}HANDSHAKE:{C_END} {C_GREEN}OK ({hs_raison}){C_END}")
    else:
        print(f" {C_BOLD}HANDSHAKE:{C_END} {C_YELLOW}inactif — {hs_raison}{C_END}")
    print(f" {C_YELLOW}Commands: /help, /mode, /ask, /projet, /run, /auto, /git, /tests, /session, /config, /exit{C_END}")
    print(f"{C_CYAN}=" * 60 + f"{C_END}\n")

    while True:
        try:
            # Styled input prompt that looks like a dedicated field
            print(f"{C_BOLD}{C_RED}┌─[ User Input ]{C_END}")
            line = input(f"{C_BOLD}{C_RED}└─> {C_END}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n{C_YELLOW}Termination signal received. Exiting DarkCodex...{C_END}")
            return 0
            
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return 0
        if line == "/dicter":
            texte_dicte = voix.dicter_et_valider()
            if not texte_dicte:
                continue
            line = texte_dicte  # le texte dicté est traité comme s'il avait été tapé
        if handle_nouvelles_commandes(line, cwd, session):
            continue
        if line == "/mode" or line.startswith("/mode "):
            commander_mode(line, ctx)
            continue
        if line == "/ask" or line.startswith("/ask "):
            demande = line.removeprefix("/ask").strip()
            if not demande:
                print("Usage: /ask [question]")
                continue
            try:
                morceaux = shlex.split(demande)
            except ValueError:
                morceaux = demande.split()
            cmd_ask(argparse.Namespace(prompt=morceaux, cwd=str(cwd), provider=None, context=True))
            continue
        if line == "/config" or line.startswith("/config "):
            configurer_chat(line, ctx)
            continue
        if line == "/memory" or line.startswith("/memory "):
            gerer_memory_chat(line, ctx)
            continue
        if handle_agent_command(line, ctx):
            continue
        if line == "/help":
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ AGENT DE FICHIERS ━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/projet [chemin]{C_END}          → analyser un projet")
            print(f"  {C_CYAN}/lire [fichier]{C_END}           → lire et analyser un fichier")
            print(f"  {C_CYAN}/modifier [fichier] [msg]{C_END} → modifier un fichier")
            print(f"  {C_CYAN}/creer [fichier] [desc]{C_END}   → créer un nouveau fichier")
            print(f"  {C_CYAN}/refactoriser [fichier]{C_END}   → améliorer le code")
            print(f"  {C_CYAN}/resume [dossier]{C_END}         → résumer un projet")
            print(f"  {C_CYAN}/corriger [fichier]{C_END}       → corriger avec sauvegarde")
            print(f"  {C_CYAN}/expliquer [fichier]{C_END}      → expliquer un fichier")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ AGENT TERMINAL ━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/run [commande]{C_END}          → exécuter une commande")
            print(f"  {C_CYAN}/install{C_END}                  → installer les dépendances")
            print(f"  {C_CYAN}/build{C_END}                    → compiler le projet")
            print(f"  {C_CYAN}/start{C_END}                    → démarrer le serveur")
            print(f"  {C_CYAN}/test{C_END}                     → lancer les tests")
            print(f"  {C_CYAN}/lint{C_END}                     → vérifier la qualité du code")
            print(f"  {C_CYAN}/auto [description]{C_END}       → séquence automatique")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ DÉBOGAGE ━━━━━━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/debug [erreur]{C_END}          → analyser une erreur")
            print(f"  {C_CYAN}/fix [fichier]{C_END}            → corriger automatiquement")
            print(f"  {C_CYAN}/review [fichier]{C_END}         → revue de code")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ GIT ━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/git status | /git diff | /git log | /git branches{C_END}")
            print(f"  {C_CYAN}/git commit [message]{C_END}     → commiter les changements")
            print(f"  {C_CYAN}/git push{C_END}                  → pousser vers GitHub")
            print(f"  {C_CYAN}/git pull | /git branch [nom]{C_END}")
            print(f"  {C_CYAN}/git explain [hash]{C_END}       → expliquer un commit")
            print(f"  {C_CYAN}/git resume{C_END}                → résumer les changements")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ TESTS ━━━━━━━━━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/tests ecrire [fichier]{C_END}  → générer des tests")
            print(f"  {C_CYAN}/tests run{C_END}                → lancer tous les tests")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ MODES ━━━━━━━━━━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/mode suggest{C_END}             → demande confirmation")
            print(f"  {C_CYAN}/mode autoedit{C_END}            → modifie automatiquement")
            print(f"  {C_CYAN}/mode fullauto{C_END}            → autonomie totale")
            print(f"  {C_CYAN}/mode{C_END}                     → affiche le mode actuel")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ SESSION ━━━━━━━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/session save [nom]{C_END}      → sauvegarder la session")
            print(f"  {C_CYAN}/session list{C_END}             → voir les sessions")
            print(f"  {C_CYAN}/session resume [nom]{C_END}     → reprendre une session")
            print(f"  {C_CYAN}/init{C_END}                     → générer AGENTS.md")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ COMPTE ━━━━━━━━━━━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/login | /logout | /solde | /plans | /status | /activate{C_END}")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ HISTORIQUE & COÛT ━━━━━━━━━━━━{C_END}")
            print(f" {C_CYAN}/historique{C_END} → dernières modifications de fichiers")
            print(f" {C_CYAN}/annuler{C_END} → annuler la dernière modification")
            print(f" {C_CYAN}/diff [fichier]{C_END} → voir ce qui a changé")
            print(f" {C_CYAN}/cout{C_END} → tokens utilisés dans la session")
            print(f" {C_CYAN}/limite [nombre|off]{C_END} → plafond de tokens pour la session")
            print(f" {C_CYAN}/dicter{C_END} → parler au lieu d'écrire (Termux:API)")
            print(f" {C_CYAN}/parrainer{C_END} → ton code de parrainage (+ tokens offerts)")
            print(f"\n{C_BOLD}{C_YELLOW}━━━━━━ MÉMOIRE & DIVERS ━━━━━━━━━━━━━{C_END}")
            print(f"  {C_CYAN}/memory KEY VAL{C_END}           → stocker une connaissance")
            print(f"  {C_CYAN}/memoire | /oublier{C_END}        → lire / effacer la mémoire")
            print(f"  {C_CYAN}/contexte TEXTE{C_END}           → définir le contexte projet")
            print(f"  {C_CYAN}/langue CODE | /langues{C_END}   → changer la langue")
            print(f"  {C_CYAN}/traduire TEXTE{C_END}           → traduire vers la langue active")
            print(f"  {C_CYAN}/clear | /config | /exit{C_END}")
            print(f"{C_YELLOW}{'━' * 40}{C_END}\n")
            continue
        if line == "/status":
            if session:
                show_solde()
            else:
                print(f"\n{C_GREEN}{licence.status_text(refresh=True)}{C_END}\n")
            continue
        if line == "/login":
            nouvelle_session = device_login()
            if nouvelle_session:
                session = nouvelle_session
            continue
        if line == "/logout":
            deconnecter()
            session = None
            print(f"\n{C_GREEN}✅ Déconnecté avec succès. À bientôt !{C_END}\n")
            return 0
        if line == "/solde":
            show_solde()
            continue
        if line == "/plans":
            show_plans()
            continue
        if line == "/memoire":
            print(memory.memory_summary())
            continue
        if line == "/oublier":
            memory.clear_memory()
            langues.ensure_language()
            print("Mémoire effacée.")
            continue
        if line.startswith("/contexte "):
            memory.set_project_context(line.removeprefix("/contexte ").strip())
            print("Contexte projet enregistré.")
            continue
        if line == "/langues":
            print(langues.list_languages())
            continue
        if line.startswith("/langue "):
            ok, message = langues.set_active_language(line.removeprefix("/langue ").strip())
            color = C_GREEN if ok else C_RED
            print(f"{color}{message}{C_END}")
            continue
        if line.startswith("/traduire "):
            text = line.removeprefix("/traduire ").strip()
            code, output = ai_answer(provider, langues.translate_prompt(text, langues.get_active_language()), config, cwd)
            print(output)
            if code == 0:
                memory.remember_conversation(line, output)
            continue
        if handle_file_agent_command(line, cwd, provider, config):
            continue
        if line == "/activate":
            license_key = input(f"{C_BOLD}{C_MAGENTA}Clé Pro: {C_END}").strip()
            ok, message = licence.activate_license(license_key)
            color = C_GREEN if ok else C_RED
            print(f"\n{color}{message}{C_END}\n")
            if ok:
                print(
                    f"{C_YELLOW}ℹ️ Après confirmation du paiement, ta clé Pro sera "
                    "activée automatiquement dans DarkCodex.{C_END}\n"
                )
            continue
        if line == "/clear":
            print("\033[H\033[J", end="")
            print(BANNER)
            print(f" {C_BOLD}STATUS:{C_END} {C_GREEN}ONLINE{C_END} | {C_BOLD}ENGINE:{C_END} {C_MAGENTA}{provider.upper()}{C_END}")
            print(f" {C_BOLD}MODE:{C_END} {C_GREEN}{licence.status_text(refresh=True)}{C_END}")
            print(f"{C_CYAN}=" * 60 + f"{C_END}\n")
            continue
        # AI Response Area
        if not session:
            allowed, limit_message = licence.consume_request()
            if not allowed:
                print(f"\n{C_RED}{limit_message}{C_END}\n")
                continue
        ok_cout, message_cout = cout.SESSION.verifier(cout.estimer_tokens(line), confirmer=False)
        if not ok_cout:
            print(f"\n{C_RED}{message_cout}{C_END}\n")
            continue
        store.add_message(session_id, "user", line)
        context = build_context(cwd, store, config, include_files=bool(config.get("chat_include_files", True)))
        
        print(f"\n{C_MAGENTA}DarkCodex is processing request...{C_END}", end="\r")
        tokens_used = tokens_left = None
        debut_reponse = time.time()
        if session:
            code, output, data = server_answer(session, f"{context}\n\nUser request:\n{line}")
            print(" " * 40, end="\r")  # Clear loader
            if code == 402:
                show_exhausted()
                session["tokens_solde"] = 0
                session["plan"] = "gratuit"
                save_auth(session)
                continue
            if code == 401:
                clear_auth()
                session = None
                print(f"{C_YELLOW}Session expirée. Tapez /login{C_END}\n")
                continue
            if code != 0:
                print(f"{C_RED}⛔ {output}{C_END}\n")
                continue
            appliquer_reponse_serveur(session, data)
            tokens_used = data.get("tokens_utilises")
            tokens_left = data.get("solde_restant")
            cout.SESSION.ajouter(tokens_used)
        else:
            code, output = run_provider(provider, f"{context}\n\nUser request:\n{line}", config, cwd)
            if code == 0:
                cout.SESSION.ajouter_estimation(f"{context}{line}", output)
            print(" " * 40, end="\r")  # Clear loader
        
        store.add_message(
            session_id,
            "assistant",
            output,
            json.dumps(
                {
                    "commande": "chat",
                    "duree_secondes": round(time.time() - debut_reponse, 1),
                    "tokens_utilises": tokens_used,
                    "tokens_restants": tokens_left,
                },
                ensure_ascii=False,
            ),
        )
        memory.remember_conversation(line, output)
        
        # Display AI Response with a clear border/separator and animation
        print(f"{C_BOLD}{C_CYAN}┌─[ DarkCodex Response ]{C_END}")
        
        # Animate the output with a typewriter effect
        prefix = f"{C_BOLD}{C_CYAN}│{C_END} "
        type_writer(output, prefix=prefix, delay=0.001)
        
        print(f"{C_BOLD}{C_CYAN}└" + "─" * 58 + f"{C_END}\n")
        
        if tokens_used is not None:
            show_token_line(tokens_used, tokens_left)
            print()
        if code not in (0, None):
            print(f"{C_RED}[!] ENGINE ERROR: {code}{C_END}\n")


def run_shell(command: str, cwd: Path, store: Store) -> int:
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd, text=True, capture_output=True, timeout=300)
        output = (proc.stdout + proc.stderr).strip()
        if output:
            print(output)
        store.log_run(cwd, command, proc.returncode, output)
        return proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = f"Command timed out: {exc}"
        print(output)
        store.log_run(cwd, command, 124, output)
        return 124


def cmd_memory(args: argparse.Namespace) -> int:
    store = Store()
    if args.action == "add":
        store.add_memory(args.key, " ".join(args.value), args.tags or "")
        print("Memory saved.")
    else:
        print_rows(store.search_memories(args.query or "", args.limit))
    return 0


def save_api_key(api_key: str, config: dict) -> Path:
    api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
    atomic_write_text(api_key_file, api_key.strip() + "\n")
    try:
        api_key_file.chmod(0o600)
    except OSError:
        pass
    return api_key_file


def cmd_config(args: argparse.Namespace) -> int:
    config = load_config()
    if args.key is None:
        print(json.dumps(config, indent=2))
        return 0
    if args.key in {"api-key", "apikey"}:
        if args.value is None:
            api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
            print("set" if api_key_file.exists() else "not set")
            return 0
        path = save_api_key(args.value, config)
        config["provider"] = "darkcodex"
        save_config(config)
        print(f"API key saved in {path}")
        print("provider=darkcodex")
        return 0
    if args.value is None:
        print(config.get(args.key, ""))
        return 0
    config[args.key] = args.value
    save_config(config)
    print(f"{args.key}={args.value}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    root = Path(args.cwd).resolve()
    for file in project_files(root, args.limit):
        print(file.relative_to(root))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    return run_shell(" ".join(args.command), Path(args.cwd).resolve(), Store())


def cmd_init(args: argparse.Namespace) -> int:
    config = load_config()
    provider = detect_provider(config)
    store = Store()
    ctx = Ctx(Path(args.cwd).resolve(), provider, config, store=store)
    return generer_agents_md(ctx)


def decouper(line: str) -> tuple[str, str]:
    parts = line.split(None, 1)
    if not parts:
        # "/session" sans argument : ne pas faire planter la boucle de chat.
        return "", ""
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


def commander_mode(line: str, ctx: Ctx) -> None:
    """Partie 1 : /mode suggest | autoedit | fullauto | /mode."""
    _, reste = decouper(line)
    if not reste:
        print(f"\n{C_BOLD}Mode actuel : {C_END}{C_GREEN}{ctx.mode.upper()}{C_END}")
        for nom in MODES:
            actif = "← actif" if nom == ctx.mode else ""
            print(f"  {C_CYAN}{nom:<10}{C_END} {MODE_LABELS[nom]} {actif}")
        print()
        return

    aliases = {
        "suggest": "suggest",
        "autoedit": "autoedit",
        "auto-edit": "autoedit",
        "fullauto": "fullauto",
        "full-auto": "fullauto",
    }
    choix = reste.strip().lower()
    if choix not in aliases:
        print(f"{C_RED}Mode inconnu : {choix}. Utilise /mode suggest | autoedit | fullauto{C_END}")
        return
    mode = aliases[choix]

    # C03 : autoedit et fullauto réservés aux comptes Pro.
    if mode in {"autoedit", "fullauto"} and not est_pro():
        print(f"{C_RED}⛔ Ce mode est réservé aux comptes Pro. Passe à la version Pro pour l'activer.{C_END}")
        return
    # C13 : fullauto exige en plus un handshake valide.
    if mode == "fullauto":
        ok, raison = handshake_valide()
        if not ok:
            print(f"{C_RED}⛔ Handshake requis pour le fullauto : {raison}.{C_END}")
            return

    save_mode(mode)
    ctx.mode = mode
    ecrire_handshake()  # C13 : le handshake reflète le mode courant
    print(f"{C_GREEN}✅ {MODE_LABELS[mode]}{C_END}")


def configurer_chat(line: str, ctx: Ctx) -> None:
    """C11-4 : /config dans le chat — lecture/écriture sans ouvrir un éditeur."""
    _, reste = decouper(line)
    if not reste:
        affiche = {cle: valeur for cle, valeur in ctx.config.items() if cle != "api_key"}
        print(json.dumps(affiche, indent=2, ensure_ascii=False))
        return
    cle, _, valeur = reste.partition(" ")
    cle = cle.strip()
    valeur = valeur.strip()

    if cle in {"api-key", "apikey"}:
        if not valeur:
            api_key_file = Path(str(ctx.config.get("api_key_file") or STATE_DIR / "api_key"))
            print("set" if api_key_file.exists() else "not set")
            return
        chemin = save_api_key(valeur, ctx.config)
        ctx.config["provider"] = "darkcodex"
        save_config(ctx.config)
        print(f"✅ Clé API enregistrée dans {chemin} (jamais affichée). provider=darkcodex")
        return
    if cle == "mode":
        commander_mode(f"/mode {valeur}", ctx)
        return
    if cle == "timeout":
        cle = "api_timeout_seconds"
    if cle not in DEFAULT_CONFIG:
        print(f"Clé inconnue : {cle}. Disponibles : {', '.join(sorted(DEFAULT_CONFIG))}")
        return
    if not valeur:
        print(ctx.config.get(cle, ""))
        return
    if cle in {"confirm_always", "chat_include_files"}:
        ctx.config[cle] = valeur.lower() in {"1", "true", "oui", "yes", "on"}
    else:
        ctx.config[cle] = valeur
    save_config(ctx.config)
    print(f"{cle}={ctx.config[cle]}")


def gerer_memory_chat(line: str, ctx: Ctx) -> None:
    """C12-1 : /memory lit les dernières réponses IA ; /memory [clé] [valeur] enregistre."""
    _, reste = decouper(line)
    if not reste or reste == "historique":
        lignes = ["🧠 Dernières réponses IA enregistrées :", ""]
        for row in recent_ai_answers(ctx.cwd, 10):
            try:
                meta = json.loads(row["meta_json"] or "{}")
            except json.JSONDecodeError:
                meta = {}
            extra = []
            if meta.get("tokens_utilises") is not None:
                extra.append(f"tokens {meta['tokens_utilises']}")
            if meta.get("duree_secondes") is not None:
                extra.append(f"{meta['duree_secondes']}s")
            suffixe = f" ({', '.join(extra)})" if extra else ""
            extraits = str(row["content"]).replace("\n", " ")[:160]
            lignes.append(f"• [{row['created_at']}] {extraits}{suffixe}")
        if len(lignes) == 2:
            lignes.append("Aucune réponse IA sauvegardée pour ce projet.")
        print("\n".join(lignes))
        return
    sous, arg = decouper(reste)
    if sous == "add":
        cle, _, valeur = arg.partition(" ")
    else:
        cle, _, valeur = reste.partition(" ")
    if not cle or not valeur:
        ctx.note("Usage: /memory | /memory [clé] [valeur] | /memory add [clé] [valeur]")
        return
    ctx.store.add_memory(cle, valeur)
    ctx.note("Enregistré.")


def generer_agents_md(ctx: Ctx) -> int:
    """Partie 7 : /init analyse le projet et génère AGENTS.md."""
    cible = ctx.cwd / AGENTS_FILENAME
    if cible.exists():
        try:
            existant = cible.read_text(encoding="utf-8", errors="replace")
        except OSError:
            existant = ""
        print(f"{AGENTS_FILENAME} existe déjà : rien écrasé.")
        print(existant)
        return 0

    info = agent_fichiers.scan_project(ctx.cwd)
    commande_tests = "python -m pytest tests/"
    try:
        from . import agent_tests as _agent_tests

        commande_tests = _agent_tests.detecter_test(ctx.cwd) or commande_tests
    except ImportError:
        pass

    prompt = (
        "Tu es un assistant de programmation expert.\n"
        "À partir de la structure du projet fournie ci-dessous, crée le fichier AGENTS.md "
        "qui servira de contexte permanent à l'agent DarkCodex.\n\n"
        "Le fichier doit contenir exactement ces sections :\n"
        "# AGENTS.md — Instructions pour DarkCodex\n"
        "## Architecture\n"
        "## Stack technique\n"
        "## Structure des dossiers\n"
        "## Commandes importantes\n"
        f"- {commande_tests}\n"
        "## Règles de code\n"
        "## Ce qu'il ne faut jamais faire\n\n"
        "Règles :\n"
        "- Réponds uniquement avec le contenu du fichier AGENTS.md.\n"
        "- N'ajoute aucun commentaire avant ou après.\n"
        "- Ne crée pas de bloc de code Markdown autour du fichier.\n"
        "- Écris-le en français.\n"
        "- Reste concis : maximum 60 lignes.\n\n"
        "Voici la structure du projet :\n\n"
        f"{agent_fichiers.resume_projet(info)}"
    )
    code, texte = ctx.ask(prompt)
    if code != 0:
        print(texte)
        return code

    contenu = agent_fichiers.strip_code_fence(texte)
    if sys.stdin.isatty():
        if not agent_fichiers.appliquer_fichier(cible, contenu, ctx, action="agents_md"):
            return 1
    else:  # exécution non interactive : darkcodex init
        cible.write_text(contenu, encoding="utf-8")
        print(f"{AGENTS_FILENAME} créé : {cible}")
        ctx.note_modification(cible, "agents_md")
    return 0


def handle_agent_command(line: str, ctx: Ctx) -> bool:
    """Partie 9 : route les commandes vers le bon agent. True si traité."""
    commande, reste = decouper(line)

    # ── Agent de fichiers ──
    if commande in {"/projet", "/modifier", "/creer", "/refactoriser"}:
        agent_fichiers_command(commande, reste, ctx)
        return True

    # ── Agent terminal ──
    if commande in {"/install", "/build", "/start", "/lint", "/test"}:
        agent_terminal.lancer(commande.removeprefix("/"), ctx)
        return True
    if commande == "/auto":
        if not reste:
            ctx.note("Usage: /auto [description de la tâche]")
            return True
        agent_terminal.auto(reste, ctx)
        return True
    if commande == "/run":
        if not reste:
            ctx.note("Usage: /run [commande]")
            return True
        agent_terminal.executer(reste, ctx)
        return True

    # ── Débogage ──
    if commande == "/debug":
        if not reste:
            ctx.note("Usage: /debug [description de l'erreur]")
            return True
        agent_debug.analyser(reste, ctx)
        return True
    if commande == "/fix":
        if not reste:
            ctx.note("Usage: /fix [fichier]")
            return True
        agent_debug.fixer(agent_fichiers.resolve_path(ctx.cwd, reste), ctx)
        return True
    if commande == "/review":
        if not reste:
            ctx.note("Usage: /review [fichier]")
            return True
        agent_debug.review(agent_fichiers.resolve_path(ctx.cwd, reste), ctx)
        return True

    # ── Git ──
    if commande == "/git":
        agent_git.executer(reste.split(), ctx)
        return True

    # ── Tests ──
    if commande == "/tests":
        sous, arg = decouper(reste)
        if sous == "run":
            agent_tests.lancer(ctx)
        elif sous == "ecrire":
            if not arg:
                ctx.note("Usage: /tests ecrire [fichier]")
            else:
                agent_tests.ecrire(ctx, agent_fichiers.resolve_path(ctx.cwd, arg))
        else:
            ctx.note("Usage: /tests run  |  /tests ecrire [fichier]")
        return True

    # ── Sessions ──
    if commande == "/session":
        sous, arg = decouper(reste)
        if sous == "save":
            # C06-2 : sauvegarde sur confirmation explicite.
            if not confirm_user("Sauvegarder la session dans .darkcodex/sessions ?"):
                ctx.note("Sauvegarde annulée.")
                return True
            agent_session.sauvegarder_et_afficher(arg, ctx)
        elif sous == "list":
            agent_session.afficher_liste(ctx)
        elif sous == "resume":
            agent_session.reprendre(arg or None, ctx)
        else:
            ctx.note("Usage: /session save [nom] | /session list | /session resume [nom]")
        return True

    # ── AGENTS.md ──
    if commande == "/init":
        generer_agents_md(ctx)
        return True

    return False


def agent_fichiers_command(commande: str, reste: str, ctx: Ctx) -> None:
    """/projet /modifier /creer /refactoriser (Partie 2)."""
    if commande == "/projet":
        cible = agent_fichiers.resolve_path(ctx.cwd, reste or ".")
        ok, prompt = agent_fichiers.project_prompt(cible)
        if not ok:
            ctx.note(prompt)
            return
        info = agent_fichiers.scan_project(cible)
        ctx.note(agent_fichiers.resume_projet(info))
        code, texte = ctx.ask(prompt)
        ctx.note(f"\n\033[95m💡 {texte}\033[0m")
        return

    if commande == "/modifier":
        fichier, _, instruction = reste.partition(" ")
        if not fichier or not instruction:
            ctx.note("Usage: /modifier [fichier] [instruction]")
            return
        cible = agent_fichiers.resolve_path(ctx.cwd, fichier)
        ok, contenu = agent_fichiers.read_text_file(cible)
        if not ok:
            ctx.note(contenu)
            return
        code, nouveau = ctx.ask(agent_fichiers.modify_prompt(cible, contenu, instruction))
        if code != 0:
            ctx.note(nouveau)
            return
        agent_fichiers.appliquer_fichier(cible, nouveau, ctx, action="modification")
        return

    if commande == "/creer":
        fichier, _, description = reste.partition(" ")
        if not fichier or not description:
            ctx.note('Usage: /creer [fichier] "[description]"')
            return
        cible = agent_fichiers.resolve_path(ctx.cwd, fichier)
        code, contenu = ctx.ask(agent_fichiers.generate_prompt(cible, description.strip('"')))
        if code != 0:
            ctx.note(contenu)
            return
        agent_fichiers.appliquer_fichier(cible, contenu, ctx, action="creation")
        return

    if commande == "/refactoriser":
        if not reste:
            ctx.note("Usage: /refactoriser [fichier]")
            return
        cible = agent_fichiers.resolve_path(ctx.cwd, reste)
        ok, contenu = agent_fichiers.read_text_file(cible)
        if not ok:
            ctx.note(contenu)
            return
        code, nouveau = ctx.ask(agent_fichiers.refactor_prompt(cible, contenu))
        if code != 0:
            ctx.note(nouveau)
            return
        agent_fichiers.appliquer_fichier(cible, nouveau, ctx, action="refactorisation")


def cmd_handshake(args: argparse.Namespace) -> int:
    """C13 : écrit/rafraîchit ~/.darkcodex/handshake.json sans interaction."""
    data = ecrire_handshake()
    ok, raison = handshake_valide()
    print(f"handshake: {'ok' if ok else 'invalide'} ({raison})")
    print(f"app_id: {data['app_id']}")
    print(f"mode: {data['mode']}")
    print(f"pro_active: {data['pro_active']}")
    return 0 if ok else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    provider = detect_provider(config)
    api_key_env = str(config.get("api_key_env") or "DARKCODEX_API_KEY")
    api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
    api_key_status = "set" if os.environ.get(api_key_env) or api_key_file.exists() else "not set"
    checks = [
        ("python", shutil.which("python")),
        ("darkcodex api key", api_key_status),
        ("gemini cli", shutil.which("gemini")),
        ("codex", shutil.which("codex")),
    ]
    print("DarkCodex doctor")
    print(f"state: {STATE_DIR}")
    print(f"database: {DB_PATH}")
    hs_ok, hs_raison = handshake_valide()
    data_hs = lire_handshake()
    print(f"handshake: {'ok' if hs_ok else 'invalide'} ({hs_raison})")
    print(f"handshake app_id: {data_hs.get('app_id', '-')}")
    print(f"handshake mode: {data_hs.get('mode', '-')}")
    print(f"handshake pro: {data_hs.get('pro_active', '-')}")
    print(f"Mode: {load_mode()}")
    print(f"configured provider: {config.get('provider', 'auto')}")
    print(f"detected provider: {provider}")
    for name, path in checks:
        print(f"{name}: {path or 'not found'}")
    if provider in {"darkcodex", "gemini"} and api_key_status != "set":
        return 1
    return 0 if provider != "local" else 1


def cmd_update(args: argparse.Namespace) -> int:
    return updater.mettre_a_jour_maintenant(API_BASE, __version__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="darkcodex", description="DarkCodex terminal coding assistant.")
    parser.add_argument("--version", action="version", version=f"DarkCodex {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="Ask one question and print the answer.")
    ask.add_argument("prompt", nargs="*")
    ask.add_argument("--cwd", default=os.getcwd())
    ask.add_argument("--provider", choices=["auto", "darkcodex", "gemini", "gemini_cli", "codex", "local"])
    ask.add_argument("--context", action="store_true", help="Include project files and saved memory.")
    ask.set_defaults(func=cmd_ask)

    chat = sub.add_parser("chat", help="Start an interactive terminal session.")
    chat.add_argument("--cwd", default=os.getcwd())
    chat.add_argument("--provider", choices=["auto", "darkcodex", "gemini", "gemini_cli", "codex", "local"])
    chat.set_defaults(func=cmd_chat)

    memory = sub.add_parser("memory", help="Add or search persistent memory.")
    memory_sub = memory.add_subparsers(dest="action", required=True)
    add = memory_sub.add_parser("add")
    add.add_argument("key")
    add.add_argument("value", nargs="+")
    add.add_argument("--tags", default="")
    add.set_defaults(func=cmd_memory)
    search = memory_sub.add_parser("search")
    search.add_argument("query", nargs="?")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_memory)

    config = sub.add_parser("config", help="Read or write configuration.")
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    config.set_defaults(func=cmd_config)

    scan = sub.add_parser("scan", help="List project files used for context.")
    scan.add_argument("--cwd", default=os.getcwd())
    scan.add_argument("--limit", type=int, default=80)
    scan.set_defaults(func=cmd_scan)

    run = sub.add_parser("run", help="Run and log a shell command in the project.")
    run.add_argument("command", nargs="+")
    run.add_argument("--cwd", default=os.getcwd())
    run.set_defaults(func=cmd_run)

    init = sub.add_parser("init", help="Create project-local DarkCodex notes.")
    init.add_argument("--cwd", default=os.getcwd())
    init.set_defaults(func=cmd_init)

    doctor = sub.add_parser("doctor", help="Check local setup and providers.")
    doctor.set_defaults(func=cmd_doctor)

    handshake = sub.add_parser("handshake", help="Write and validate the local handshake file.")
    handshake.set_defaults(func=cmd_handshake)

    update = sub.add_parser("update", help="Check for a new version of DarkCodex and install it.")
    update.set_defaults(func=cmd_update)
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["chat"]
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in ("chat", "ask"):
        updater.verifier_au_lancement(API_BASE, __version__)
    return int(args.func(args) or 0)
