#!/data/data/com.termux/files/usr/bin/sh
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python -m darkcodex "$@"
