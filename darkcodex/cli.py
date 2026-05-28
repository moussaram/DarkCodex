#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import agent_fichiers, langues, licence, memory, security

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
            """
        )
        self.db.commit()

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

    def add_message(self, session_id: int, role: str, content: str) -> None:
        self.db.execute(
            "insert into messages(session_id, role, content, created_at) values (?, ?, ?, ?)",
            (session_id, role, content, self.now()),
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
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")


def detect_provider(config: dict) -> str:
    provider = config.get("provider", "auto")
    if provider != "auto":
        return provider
    api_key_env = str(config.get("api_key_env") or "DARKCODEX_API_KEY")
    api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
    if os.environ.get(api_key_env) or os.environ.get("GEMINI_API_KEY") or api_key_file.exists():
        return "darkcodex"
    if shutil.which("codex"):
        return "codex"
    return "local"


def project_files(root: Path, limit: int) -> list[Path]:
    ignore_dirs = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".gradle",
        ".idea",
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= limit:
            break
        if path.is_dir():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in ignore_dirs for part in rel_parts):
            continue
        if path.stat().st_size > 80_000:
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".gz", ".xz", ".jar"}:
            continue
        files.append(path)
    return files


def build_context(cwd: Path, store: Store, config: dict) -> str:
    parts = [f"Working directory: {cwd}"]
    memories = store.search_memories(limit=8)
    if memories:
        parts.append("Relevant saved memory:")
        for mem in memories:
            parts.append(f"- {mem['key']}: {mem['value']} [{mem['tags']}]")
    recent = store.recent_messages(cwd)
    if recent:
        parts.append("Recent conversation:")
        for msg in recent:
            parts.append(f"{msg['role']}: {msg['content'][:1000]}")
    files = project_files(cwd, int(config.get("max_context_files", 12)))
    if files:
        parts.append("Project file snapshot:")
        for file in files:
            rel = file.relative_to(cwd)
            try:
                content = file.read_text(errors="replace")[:5000]
            except OSError:
                continue
            parts.append(f"\n--- {rel} ---\n{content}")
    return "\n".join(parts)


def config_timeout(config: dict, default: int = 1200) -> int:
    try:
        return max(30, int(config.get("api_timeout_seconds", default)))
    except (TypeError, ValueError):
        return default


def darkcodex_api_answer(prompt: str, config: dict) -> tuple[int, str]:
    api_key_env = str(config.get("api_key_env") or "DARKCODEX_API_KEY")
    api_key = security.get_api_key() or os.environ.get(api_key_env) or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
        try:
            if api_key_file.exists():
                api_key = api_key_file.read_text().strip()
        except OSError:
            api_key = ""
    if not api_key:
        return 2, (
            f"Missing API key. Set it with:\n"
            f"  darkcodex config api-key YOUR_API_KEY\n\n"
            f"Windows env alternative:\n"
            f"  setx {api_key_env} \"YOUR_API_KEY\"\n\n"
            f"Termux/Linux env alternative:\n"
            f"  export {api_key_env}='YOUR_API_KEY'\n\n"
            f"DarkCodex also accepts GEMINI_API_KEY or {STATE_DIR / 'api_key'} as a fallback."
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


def print_rows(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No memories found.")
        return
    for row in rows:
        print(f"[{row['id']}] {row['key']} :: {row['value']} ({row['tags']})")


def cmd_ask(args: argparse.Namespace) -> int:
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
    allowed, limit_message = licence.consume_request()
    if not allowed:
        print(limit_message)
        return 1
    context = build_context(cwd, store, config) if args.context else f"Working directory: {cwd}"
    prompt = f"{context}\n\nUser request:\n{request}"
    session_id = store.new_session(cwd, provider)
    store.add_message(session_id, "user", request)
    code, output = run_provider(provider, prompt, config, cwd)
    store.add_message(session_id, "assistant", output)
    memory.remember_conversation(request, output)
    print(output)
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
{C_END}{C_CYAN}                       DarkCodex - Unrestricted AI {C_END}
"""

import time

def type_writer(text: str, prefix: str = "", delay: float = 0.005) -> None:
    """Animates text output character by character."""
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
        allowed, limit_message = licence.consume_request()
        if not allowed:
            return 1, limit_message
        return run_provider(provider, prompt, config, cwd)

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

    mode = command.removeprefix("/")
    prompt = agent_fichiers.analysis_prompt(path, content_or_error, mode)
    code, output = call_ai(prompt)
    if mode == "corriger" and code == 0:
        backup = agent_fichiers.backup_file(path)
        agent_fichiers.write_generated_file(path, output)
        print(f"Fichier corrigé: {path}")
        print(f"Sauvegarde créée: {backup}")
        memory.remember_conversation(line, f"Fichier corrigé: {path}")
        return True

    print(output)
    if code == 0:
        memory.remember_conversation(line, output)
    return True


def cmd_chat(args: argparse.Namespace) -> int:
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
    
    # Clear screen and show header
    print("\033[H\033[J", end="")
    print(BANNER)
    print(memory.startup_message())
    print(langues.format_startup())
    print(f" {C_BOLD}STATUS:{C_END} {C_GREEN}ONLINE{C_END} | {C_BOLD}ENGINE:{C_END} {C_MAGENTA}{provider.upper()}{C_END} | {C_BOLD}PATH:{C_END} {C_CYAN}{cwd}{C_END}")
    print(f" {C_BOLD}MODE:{C_END} {C_GREEN}{licence.status_text(refresh=True)}{C_END}")
    print(f" {C_YELLOW}Commands: /help, /run, /activate, /status, /clear, /exit{C_END}")
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
        if line == "/help":
            print(f"\n{C_BOLD}{C_YELLOW}--- DarkCodex Commands ---{C_END}")
            print(f"  {C_CYAN}/memory KEY VAL{C_END} : Store knowledge")
            print(f"  {C_CYAN}/memoire{C_END}        : Affiche la mémoire persistante")
            print(f"  {C_CYAN}/oublier{C_END}        : Efface la mémoire persistante")
            print(f"  {C_CYAN}/contexte TEXTE{C_END} : Définit le contexte du projet")
            print(f"  {C_CYAN}/lire FICHIER{C_END}   : Lit et analyse un fichier")
            print(f"  {C_CYAN}/corriger FICHIER{C_END}: Corrige un fichier avec sauvegarde")
            print(f"  {C_CYAN}/expliquer FICHIER{C_END}: Explique un fichier")
            print(f"  {C_CYAN}/generer FICHIER \"DESC\"{C_END}: Génère un fichier")
            print(f"  {C_CYAN}/resume DOSSIER{C_END} : Résume un dossier")
            print(f"  {C_CYAN}/langue CODE{C_END}    : Change la langue active")
            print(f"  {C_CYAN}/langues{C_END}        : Liste les langues disponibles")
            print(f"  {C_CYAN}/traduire TEXTE{C_END} : Traduit vers la langue active")
            print(f"  {C_CYAN}/run CMD{C_END}        : Execute shell")
            print(f"  {C_CYAN}/activate{C_END}       : Activate Pro license")
            print(f"  {C_CYAN}/status{C_END}         : Show Free/Pro status")
            print(f"  {C_CYAN}/clear{C_END}          : Reset terminal")
            print(f"  {C_CYAN}/config{C_END}         : View settings")
            print(f"  {C_CYAN}/exit{C_END}           : Shut down")
            print(f"{C_YELLOW}--------------------------{C_END}\n")
            continue
        if line == "/status":
            print(f"\n{C_GREEN}{licence.status_text(refresh=True)}{C_END}\n")
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
            code, output = run_provider(provider, langues.translate_prompt(text, langues.get_active_language()), config, cwd)
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
            continue
        if line == "/clear":
            print("\033[H\033[J", end="")
            print(BANNER)
            print(f" {C_BOLD}STATUS:{C_END} {C_GREEN}ONLINE{C_END} | {C_BOLD}ENGINE:{C_END} {C_MAGENTA}{provider.upper()}{C_END}")
            print(f" {C_BOLD}MODE:{C_END} {C_GREEN}{licence.status_text(refresh=True)}{C_END}")
            print(f"{C_CYAN}=" * 60 + f"{C_END}\n")
            continue
        if line.startswith("/run "):
            command = line.removeprefix("/run ").strip()
            print(f"\n{C_BOLD}{C_YELLOW}[SYSTEM EXECUTION]{C_END} {command}")
            exit_code = run_shell(command, cwd, store)
            color = C_GREEN if exit_code == 0 else C_RED
            print(f"{color}Finished with exit code: {exit_code}{C_END}\n")
            continue

        # AI Response Area
        allowed, limit_message = licence.consume_request()
        if not allowed:
            print(f"\n{C_RED}{limit_message}{C_END}\n")
            continue
        store.add_message(session_id, "user", line)
        context = build_context(cwd, store, config)
        
        print(f"\n{C_MAGENTA}DarkCodex is processing request...{C_END}", end="\r")
        code, output = run_provider(provider, f"{context}\n\nUser request:\n{line}", config, cwd)
        print(" " * 40, end="\r") # Clear loader
        
        store.add_message(session_id, "assistant", output)
        memory.remember_conversation(line, output)
        
        # Display AI Response with a clear border/separator and animation
        print(f"{C_BOLD}{C_CYAN}┌─[ DarkCodex Response ]{C_END}")
        
        # Animate the output with a typewriter effect
        prefix = f"{C_BOLD}{C_CYAN}│{C_END} "
        type_writer(output, prefix=prefix, delay=0.005)
        
        print(f"{C_BOLD}{C_CYAN}└" + "─" * 58 + f"{C_END}\n")
        
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
    api_key_file.parent.mkdir(parents=True, exist_ok=True)
    api_key_file.write_text(api_key.strip() + "\n")
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
    root = Path(args.cwd).resolve()
    project_dir = root / ".darkcodex"
    project_dir.mkdir(exist_ok=True)
    notes = project_dir / "notes.md"
    if not notes.exists():
        notes.write_text(
            "# DarkCodex Project Notes\n\n"
            "- Purpose: describe this project here.\n"
            "- Commands: add build, test, and run commands here.\n"
            "- Preferences: add local coding preferences here.\n"
        )
    print(f"Initialized {project_dir}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    provider = detect_provider(config)
    api_key_env = str(config.get("api_key_env") or "DARKCODEX_API_KEY")
    api_key_file = Path(str(config.get("api_key_file") or STATE_DIR / "api_key"))
    api_key_status = "set" if os.environ.get(api_key_env) or os.environ.get("GEMINI_API_KEY") or api_key_file.exists() else "not set"
    checks = [
        ("python", shutil.which("python")),
        ("darkcodex api key", api_key_status),
        ("gemini cli", shutil.which("gemini")),
        ("codex", shutil.which("codex")),
    ]
    print("DarkCodex doctor")
    print(f"state: {STATE_DIR}")
    print(f"database: {DB_PATH}")
    print(f"configured provider: {config.get('provider', 'auto')}")
    print(f"detected provider: {provider}")
    for name, path in checks:
        print(f"{name}: {path or 'not found'}")
    if provider in {"darkcodex", "gemini"} and api_key_status != "set":
        return 1
    return 0 if provider != "local" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="darkcodex", description="DarkCodex terminal coding assistant.")
    parser.add_argument("--version", action="version", version="DarkCodex 0.1.0")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ["chat"]
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)
