#!/data/data/com.termux/files/usr/bin/sh
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$DIR"
exec python -m darkcodex "$@"
