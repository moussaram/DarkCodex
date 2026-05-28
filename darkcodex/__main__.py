try:
    from . import agent_fichiers, langues, memory  # noqa: F401
    from .cli import main
except ImportError:
    from darkcodex import agent_fichiers, langues, memory  # noqa: F401
    from darkcodex.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
