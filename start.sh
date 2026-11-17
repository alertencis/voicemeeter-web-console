#!/bin/sh
# Lance la console Voicemeeter (Linux / macOS). Premier lancement : crée le venv et installe vban-cmd.
cd "$(dirname "$0")" || exit 1

if [ ! -x venv/bin/python ]; then
  echo "Premier lancement : installation de l'environnement Python..."
  if ! { python3 -m venv venv && venv/bin/pip install -r requirements.txt; }; then
    rm -rf venv
    echo "Échec : installez Python 3.10+ avec venv (Debian/Ubuntu : sudo apt install python3-venv)."
    exit 1
  fi
fi

exec venv/bin/python vol_server.py
