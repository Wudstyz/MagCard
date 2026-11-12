#!/bin/zsh
# Construit "MagCard.app" (application macOS autonome, avec icône).
# Rien à installer pour l'utilisateur final : tout est embarqué dans l'app.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { python3 -m venv .venv; }
.venv/bin/pip install -q -r requirements.txt pyinstaller pillow
.venv/bin/python build_assets/make_icon.py

HERE_DIR="$(pwd)"
NAME="MagCard"
SIGN_ID=$(security find-identity -v -p codesigning 2>/dev/null | grep -m1 -o '"[^"]*"' | tr -d '"')

# Si ce dossier est synchronisé (iCloud Bureau et Documents, etc.), la synchronisation peut
# réappliquer des métadonnées étendues pendant qu'on signe et faire échouer la signature :
# on construit et on signe entièrement dans /tmp, et on ne copie le résultat déjà signé qu'à la fin.
WORK="$(mktemp -d /tmp/${NAME}_build.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

CARDPRO_APP_MODE=mag .venv/bin/pyinstaller --noconfirm --clean --windowed \
  --distpath "$WORK/dist" --workpath "$WORK/build" --specpath "$WORK" \
  --name "$NAME" --icon "$HERE_DIR/build_assets/icon.icns" \
  --osx-bundle-identifier fr.damien.magcard \
  --add-data "$HERE_DIR/web:web" --collect-submodules webview \
  "$HERE_DIR/app.py"

ditto --noextattr --noqtn "$WORK/dist/$NAME.app" "$WORK/$NAME.app"
xattr -cr "$WORK/$NAME.app"
if [ -n "$SIGN_ID" ]; then
  codesign --force --deep --options runtime --sign "$SIGN_ID" "$WORK/$NAME.app"
else
  codesign --force --deep --sign - "$WORK/$NAME.app"
fi
rm -rf "$HERE_DIR/$NAME.app"
ditto "$WORK/$NAME.app" "$HERE_DIR/$NAME.app"

for i in 1 2 3 4 5; do
  codesign --verify --deep --strict "$HERE_DIR/$NAME.app" 2>/dev/null && break
  xattr -cr "$HERE_DIR/$NAME.app"
done
echo "Signé avec : ${SIGN_ID:-ad-hoc}"
codesign -dv "$HERE_DIR/$NAME.app" 2>&1 | head -3
codesign --verify --deep --strict "$HERE_DIR/$NAME.app" 2>/dev/null \
  && echo "Vérification stricte OK" \
  || echo "Avertissement : un dossier synchronisé a réappliqué ses métadonnées ; relance \"xattr -cr '$NAME.app'\" juste avant de distribuer si besoin."
echo "OK : $NAME.app"
