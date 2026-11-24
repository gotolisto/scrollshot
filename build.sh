#!/usr/bin/env bash
# Rebuilds Output/ScrollShot-Setup.exe on Linux (no Windows needed).
# Needs: wine (64-bit), nsis (makensis), xvfb, and a Windows Python made with
#   micromamba create --platform win-64 -p /tmp/winpy -c conda-forge python=3.12 tk
#   wine /tmp/winpy/python.exe -m ensurepip
#   wine /tmp/winpy/python.exe -m pip install -r requirements.txt pyinstaller
set -e
cd "$(dirname "$0")"
export WINEDEBUG=-all WINEDLLOVERRIDES="ucrtbase=n,b" WINEPATH='Z:\tmp\winpy\Library\bin;Z:\tmp\winpy'
rm -rf build dist
wine /tmp/winpy/python.exe -m PyInstaller --noconfirm --clean --windowed \
  --name ScrollShot --icon icon.ico --add-data "icon.png;." \
  --hidden-import pystray._win32 \
  --hidden-import pynput.mouse._win32 --hidden-import pynput.keyboard._win32 \
  scrollshot.py 2>&1 | cat
mkdir -p Output
makensis -V2 installer.nsi
echo "Built Output/ScrollShot-Setup.exe"
