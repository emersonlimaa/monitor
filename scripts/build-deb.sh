#!/bin/bash
# Monta o pacote .deb do Monitor de Recursos.
#
#   ./scripts/build-deb.sh            -> gera dist/monitor-recursos_VERSAO_all.deb
#
# Não precisa de root: o dpkg-deb grava os arquivos já como root:root.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACOTE="monitor-recursos"
VERSAO="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$RAIZ/src/monitor_recursos/util.py")"
APPID="io.github.emersonlimaa.MonitorRecursos"
# Mantenedor: variável de ambiente, senão a identidade do git, senão genérico.
GIT_NOME="$(git -C "$RAIZ" config user.name 2>/dev/null || true)"
GIT_EMAIL="$(git -C "$RAIZ" config user.email 2>/dev/null || true)"
if [ -n "${DEB_MAINTAINER:-}" ]; then
  MANTENEDOR="$DEB_MAINTAINER"
elif [ -n "$GIT_NOME" ] && [ -n "$GIT_EMAIL" ]; then
  MANTENEDOR="$GIT_NOME <$GIT_EMAIL>"
else
  MANTENEDOR="Monitor de Recursos <nobody@localhost>"
fi

BUILD="$RAIZ/build/$PACOTE"
DIST="$RAIZ/dist"
LIB="$BUILD/usr/lib/$PACOTE"
LIBEXEC="$BUILD/usr/libexec/$PACOTE"

echo "==> Montando $PACOTE $VERSAO"
rm -rf "$BUILD"
mkdir -p "$BUILD/DEBIAN" "$LIB" "$LIBEXEC" "$BUILD/usr/bin" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/scalable/apps" \
         "$BUILD/usr/share/polkit-1/actions" \
         "$BUILD/usr/share/metainfo" \
         "$BUILD/usr/share/doc/$PACOTE"

# Código do aplicativo (sem caches de execução).
cp -r "$RAIZ/src/monitor_recursos" "$LIB/"
find "$LIB" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$LIB" -name '*.py[co]' -delete

# Auxiliar privilegiado.
install -m 0755 "$RAIZ/helper/monitor-recursos-helper" "$LIBEXEC/"

# Lançador.
cat > "$BUILD/usr/bin/$PACOTE" <<'LAUNCH'
#!/bin/sh
PYTHONPATH="/usr/lib/monitor-recursos${PYTHONPATH:+:$PYTHONPATH}" \
  exec python3 -m monitor_recursos "$@"
LAUNCH
chmod 0755 "$BUILD/usr/bin/$PACOTE"

# Dados.
install -m 0644 "$RAIZ/data/$APPID.desktop" "$BUILD/usr/share/applications/"
install -m 0644 "$RAIZ/data/icons/$APPID.svg" \
        "$BUILD/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$RAIZ/data/$APPID.policy" "$BUILD/usr/share/polkit-1/actions/"
install -m 0644 "$RAIZ/data/$APPID.metainfo.xml" "$BUILD/usr/share/metainfo/"
install -m 0644 "$RAIZ/LICENSE" "$BUILD/usr/share/doc/$PACOTE/copyright"
if [ -f "$RAIZ/README.md" ]; then
  install -m 0644 "$RAIZ/README.md" "$BUILD/usr/share/doc/$PACOTE/README.md"
fi
printf '%s (%s) unstable; urgency=low\n\n  * Primeira versão.\n\n -- %s  %s\n' \
  "$PACOTE" "$VERSAO" "$MANTENEDOR" "$(date -R)" \
  | gzip -9n > "$BUILD/usr/share/doc/$PACOTE/changelog.Debian.gz"
chmod 0644 "$BUILD/usr/share/doc/$PACOTE/changelog.Debian.gz"

TAMANHO="$(du -sk --exclude=DEBIAN "$BUILD" | cut -f1)"

cat > "$BUILD/DEBIAN/control" <<CONTROL
Package: $PACOTE
Version: $VERSAO
Section: utils
Priority: optional
Architecture: all
Maintainer: $MANTENEDOR
Installed-Size: $TAMANHO
Depends: python3 (>= 3.10), python3-gi (>= 3.42), gir1.2-gtk-4.0 (>= 4.14),
 gir1.2-adw-1 (>= 1.5), gir1.2-glib-2.0, libgtk-4-1 (>= 4.14),
 libadwaita-1-0 (>= 1.5), pkexec | policykit-1
Recommends: snapd, flatpak
Description: Recursos do sistema, uso do disco e desinstalação de programas
 Mostra em tempo real o uso de processador, memória, discos e rede, diz qual
 porcentagem do disco é vídeo, música, documento, projeto de programação, jogo
 ou cache, e desinstala programas do apt, Snap e Flatpak sem abrir o terminal.
 .
 A remoção passa por um auxiliar privilegiado separado, que confere a lista de
 pacotes protegidos antes de tirar qualquer coisa do sistema.
CONTROL

cat > "$BUILD/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    if command -v gtk4-update-icon-cache >/dev/null 2>&1; then
        gtk4-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    elif command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    python3 -m compileall -q /usr/lib/monitor-recursos >/dev/null 2>&1 || true
fi
exit 0
POSTINST

cat > "$BUILD/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
find /usr/lib/monitor-recursos -name '__pycache__' -type d -prune \
     -exec rm -rf {} + 2>/dev/null || true
exit 0
PRERM

cat > "$BUILD/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    rm -rf /usr/lib/monitor-recursos 2>/dev/null || true
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk4-update-icon-cache >/dev/null 2>&1; then
        gtk4-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
POSTRM

chmod 0755 "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/prerm" "$BUILD/DEBIAN/postrm"
find "$BUILD" -type d -exec chmod 0755 {} +
find "$BUILD/usr/lib" "$BUILD/usr/share" -type f -exec chmod 0644 {} +
chmod 0755 "$BUILD/usr/bin/$PACOTE" "$LIBEXEC/monitor-recursos-helper" \
           "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/prerm" "$BUILD/DEBIAN/postrm"

mkdir -p "$DIST"
ARQUIVO="$DIST/${PACOTE}_${VERSAO}_all.deb"
dpkg-deb --root-owner-group --build "$BUILD" "$ARQUIVO" >/dev/null

echo "==> Pronto: $ARQUIVO ($(du -h "$ARQUIVO" | cut -f1))"
echo
dpkg-deb --info "$ARQUIVO" | sed -n '2,14p'
echo "--- conteúdo ---"
dpkg-deb --contents "$ARQUIVO" | awk '{print $1, $6}' | sed 's|\./|/|' | head -40
