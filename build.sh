#!/bin/bash
set -e

echo "Nettoyage..."
find . -name "__pycache__" -type d -exec rm -rf {} +
find . -name "*.pyc" -delete

echo "Compilation DarkCodex..."
pyinstaller darkcodex.spec --clean

echo "Binaire pret dans dist/darkcodex"
echo "Taille : $(du -sh dist/darkcodex | cut -f1)"
