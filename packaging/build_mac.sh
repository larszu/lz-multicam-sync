#!/bin/sh
# Builds "LZ Multicam Sync.app" and a DMG into dist/. Run from the repo root.
set -e
PY=${PY:-.venv/bin/python}
VER=$($PY -c "import lzsync; print(lzsync.__version__)")
rm -rf build dist
$PY -m PyInstaller --noconfirm --windowed --name "LZ Multicam Sync" \
  --osx-bundle-identifier de.zumpelars.lzsync \
  --collect-submodules lzsync packaging/entry.py
mkdir -p dist/dmg
cp -R "dist/LZ Multicam Sync.app" dist/dmg/
ln -s /Applications dist/dmg/Programme
hdiutil create -volname "LZ Multicam Sync $VER" -srcfolder dist/dmg -ov -format UDZO \
  "dist/LZ-Multicam-Sync-$VER-macOS-$(uname -m).dmg"
rm -rf dist/dmg
