#!/bin/bash
# Lancement du dashboard en développement local
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
exec python -m streamlit run dashboard.py --server.headless=true
