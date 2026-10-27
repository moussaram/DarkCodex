#!/data/data/com.termux/files/usr/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

create_wrapper() {
  name="$1"
  cat > "$BIN_DIR/$name" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
export PYTHONPATH="$ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
exec python -m darkcodex "\$@"
EOF
  chmod +x "$BIN_DIR/$name"
}

create_wrapper "darkcodex"
create_wrapper "DarkCodex"

ensure_path_line() {
  file="$1"
  line='export PATH="$HOME/.local/bin:$PATH"'
  touch "$file"
  if ! grep -Fq "$line" "$file"; then
    printf '\n%s\n' "$line" >> "$file"
  fi
}

ensure_path_line "$HOME/.bashrc"
ensure_path_line "$HOME/.profile"

echo "--------------------------------------------------"
echo "DarkCodex installe avec succes dans $BIN_DIR"
echo "Commandes disponibles: darkcodex, DarkCodex"
echo "--------------------------------------------------"
echo "Verification de l'installation..."
python -m pip install python-dotenv || true
python -m darkcodex handshake || true
echo "--------------------------------------------------"
echo "Etat du systeme:"
python -m darkcodex doctor || true
echo "--------------------------------------------------"
echo "Pour commencer, tape: darkcodex"
echo "Si la commande n'est pas trouvee, relance ton terminal"
echo "ou tape: export PATH=\"\$HOME/.local/bin:\$PATH\""
echo "--------------------------------------------------"
