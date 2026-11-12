#!/bin/zsh
# Construit MagCard.dmg à partir de MagCard.app (lance d'abord build_app.sh).
set -e
cd "$(dirname "$0")"
NAME="MagCard"
[ -d "$NAME.app" ] || { echo "Construis d'abord l'application : ./build_app.sh"; exit 1; }

STAGE="$(mktemp -d /tmp/${NAME}_dmg.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$NAME.app" "$STAGE/$NAME.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$NAME.dmg"
hdiutil create -volname "$NAME" -srcfolder "$STAGE" -ov -format UDZO "$NAME.dmg"
shasum -a 256 "$NAME.dmg" | tee "$NAME.dmg.sha256"
echo "OK : $NAME.dmg"
