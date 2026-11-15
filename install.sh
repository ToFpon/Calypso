#!/bin/sh
# Installation de Calypso (visionneuse PDF) pour l'utilisateur courant (~/.local), sans droits root.
#   ./install.sh             installe
#   ./install.sh --uninstall désinstalle (les récents et les réglages restent)
set -e

PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share"
APPDIR="$PREFIX/lib/calypso"          # le programme ; les données de l'utilisateur restent dans $SHARE/calypso
SRC="$(cd "$(dirname "$0")" && pwd)"
APP_ID="io.github.calypso.Calypso"
DESKTOP="$APP_ID.desktop"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$APPDIR" "$PREFIX/bin/calypso" "$SHARE/applications/$DESKTOP" \
        "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg" \
        "$SHARE/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
    update-desktop-database "$SHARE/applications" 2>/dev/null || true
    if [ -f "$SHARE/icons/hicolor/icon-theme.cache" ]; then
        gtk-update-icon-cache -f -t "$SHARE/icons/hicolor" 2>/dev/null || rm -f "$SHARE/icons/hicolor/icon-theme.cache"
    fi
    echo "Calypso a été désinstallé."
    exit 0
fi

if [ -d /usr/share/calypso ]; then
    echo "Attention : le paquet calypso (PPA) est déjà installé. Cette installation dans ~/.local"
    echo "passera devant lui ; pour revenir au paquet : ./install.sh --uninstall"
fi

missing=""
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Adw; assert Adw.MINOR_VERSION >= 5" 2>/dev/null \
    || missing="$missing python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
python3 -c "import gi; gi.require_version('Poppler','0.18'); from gi.repository import Poppler" 2>/dev/null \
    || missing="$missing gir1.2-poppler-0.18"
python3 -c "import cairo; import gi; gi.require_foreign('cairo')" 2>/dev/null || missing="$missing python3-cairo python3-gi-cairo"
command -v qpdf >/dev/null 2>&1 || missing="$missing qpdf"
if [ -n "$missing" ]; then
    echo "Dépendances manquantes. Debian/Ubuntu/Zorin : sudo apt install$missing"
    exit 1
fi
command -v gs >/dev/null 2>&1 || echo "Conseillé (compression des PDF) : sudo apt install ghostscript"
python3 -c "import pypdf" 2>/dev/null || echo "Conseillé (cases à cocher des formulaires) : sudo apt install python3-pypdf"
command -v pk12util >/dev/null 2>&1 || echo "Conseillé (importer un certificat de signature) : sudo apt install libnss3-tools"

mkdir -p "$APPDIR" "$PREFIX/bin" "$SHARE/applications" "$SHARE/icons/hicolor/scalable/apps" \
    "$SHARE/icons/hicolor/symbolic/apps"
rm -rf "$APPDIR/calypso" "$APPDIR/data"
cp -r "$SRC/calypso" "$SRC/data" "$APPDIR/"
find "$APPDIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
cat > "$PREFIX/bin/calypso" <<LAUNCH
#!/bin/sh
# -P : pas de dossier courant dans les chemins d'import (un dossier supprimé faisait planter le
# démarrage, et un « gi.py » posé dans le dossier courant aurait été exécuté)
exec python3 -P -c 'import sys; sys.path.insert(0, "$APPDIR"); from calypso.app import main; sys.exit(main(sys.argv))' "\$@"
LAUNCH
chmod +x "$PREFIX/bin/calypso"
sed "s|^Exec=calypso|Exec=$PREFIX/bin/calypso|" "$SRC/data/$DESKTOP" > "$SHARE/applications/$DESKTOP"
cp "$SRC/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$SHARE/icons/hicolor/scalable/apps/"
cp "$SRC/data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" "$SHARE/icons/hicolor/symbolic/apps/"
update-desktop-database "$SHARE/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$SHARE/icons/hicolor" 2>/dev/null || true

echo "Calypso est installé. Lancez-le depuis le menu des applications ou avec : calypso"
echo "Pour en faire la visionneuse PDF par défaut : xdg-mime default $DESKTOP application/pdf"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Astuce : ajoutez $PREFIX/bin à votre PATH." ;; esac
