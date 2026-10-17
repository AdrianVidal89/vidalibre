#!/usr/bin/env bash
# ==============================================================================
# instalar.sh — deja Vidalibre instalado en este ordenador
# ==============================================================================
# Instala la aplicación en ~/.local/share/vidalibre, crea el comando
# `vidalibre` y añade «Vidalibre — biblioteca» al menú de aplicaciones.
#
# En el ordenador, Vidalibre trabaja contra la MISMA biblioteca del NAS (la
# carpeta montada) y además ve el Kindle que haya enchufado por USB: enviar
# libros, convertirlos y reponer portadas.
#
#   ./instalar.sh                 instalar o actualizar
#   ./instalar.sh --desinstalar   quitarlo
# ==============================================================================
set -euo pipefail

ORIGEN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="$HOME/.local/share/vidalibre"
BIN="$HOME/.local/bin"
APLICACIONES="$HOME/.local/share/applications"
ICONOS="$HOME/.local/share/icons/hicolor/512x512/apps"
AJUSTES="$HOME/.config/vidalibre.conf"

if [ "${1:-}" = "--desinstalar" ]; then
    rm -rf "$DESTINO" "$BIN/vidalibre" "$APLICACIONES/vidalibre.desktop" \
           "$ICONOS/vidalibre.png"
    update-desktop-database "$APLICACIONES" 2>/dev/null || true
    echo "Vidalibre desinstalado. Los ajustes siguen en $AJUSTES y la biblioteca no se ha tocado."
    exit 0
fi

command -v python3 >/dev/null || { echo "Falta python3."; exit 1; }
if ! command -v ebook-convert >/dev/null; then
    echo "AVISO: no encuentro Calibre (ebook-convert)."
    echo "       Sin él, Vidalibre abre la biblioteca pero no puede convertir nada."
    echo "       En Arch/CachyOS:  sudo pacman -S calibre"
fi

echo "Instalando en $DESTINO…"
mkdir -p "$DESTINO" "$BIN" "$APLICACIONES" "$ICONOS"
rm -rf "$DESTINO/app"
mkdir -p "$DESTINO/app"
cp -r "$ORIGEN/vidalibre" "$ORIGEN/servidor.py" "$ORIGEN/requirements.txt" "$DESTINO/app/"
find "$DESTINO/app" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

if [ ! -d "$DESTINO/entorno" ]; then
    python3 -m venv "$DESTINO/entorno"
fi
"$DESTINO/entorno/bin/pip" install --quiet --upgrade pip
"$DESTINO/entorno/bin/pip" install --quiet -r "$DESTINO/app/requirements.txt"

cp "$ORIGEN/vidalibre/estatico/icono-512.png" "$ICONOS/vidalibre.png"

# Ajustes del ordenador: dónde está la biblioteca y en qué puerto se abre.
if [ ! -f "$AJUSTES" ]; then
    BIBLIOTECA_POR_DEFECTO="/mnt/calibre-nas/books"
    [ -d "$BIBLIOTECA_POR_DEFECTO" ] || BIBLIOTECA_POR_DEFECTO="$HOME/Biblioteca de calibre"
    cat > "$AJUSTES" <<CONF
# Ajustes de Vidalibre en este ordenador.
# La biblioteca es la del NAS montada en el portátil: la misma que en la web.
VIDALIBRE_BIBLIOTECA="$BIBLIOTECA_POR_DEFECTO"
VIDALIBRE_PUERTO=8484
VIDALIBRE_MODO=local
VIDALIBRE_DATOS="$HOME/.local/share/vidalibre/datos"
# Carpeta que se propone al convertir libros a una carpeta del ordenador.
VIDALIBRE_DESCARGAS="$HOME/Descargas"
CONF
    echo "Ajustes nuevos en $AJUSTES"
fi

cat > "$BIN/vidalibre" <<'LANZADOR'
#!/usr/bin/env bash
# Abre Vidalibre en este ordenador: arranca el servidor si no estaba y lanza
# el navegador. `vidalibre --parar` lo cierra.
set -euo pipefail

BASE="$HOME/.local/share/vidalibre"
AJUSTES="$HOME/.config/vidalibre.conf"
REGISTRO="$BASE/vidalibre.log"
PID="$BASE/vidalibre.pid"

[ -f "$AJUSTES" ] && set -a && . "$AJUSTES" && set +a
PUERTO="${VIDALIBRE_PUERTO:-8484}"

esta_vivo() {
    [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null
}

if [ "${1:-}" = "--parar" ]; then
    if esta_vivo; then kill "$(cat "$PID")" && rm -f "$PID" && echo "Vidalibre parado."
    else echo "No estaba en marcha."; fi
    exit 0
fi

if [ "${1:-}" = "--registro" ]; then
    tail -n "${2:-50}" "$REGISTRO"; exit 0
fi

if ! esta_vivo; then
    mkdir -p "$BASE/datos"
    nohup "$BASE/entorno/bin/python" "$BASE/app/servidor.py" >"$REGISTRO" 2>&1 &
    echo $! > "$PID"
    for _ in $(seq 1 25); do
        sleep 0.4
        curl -fsS "http://127.0.0.1:$PUERTO/estado" >/dev/null 2>&1 && break
    done
fi

if ! curl -fsS "http://127.0.0.1:$PUERTO/estado" >/dev/null 2>&1; then
    echo "Vidalibre no ha arrancado. Mira el registro con: vidalibre --registro"
    exit 1
fi

[ "${1:-}" = "--sin-navegador" ] || xdg-open "http://127.0.0.1:$PUERTO/" >/dev/null 2>&1 || true
LANZADOR
chmod +x "$BIN/vidalibre"

cat > "$APLICACIONES/vidalibre.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Vidalibre — biblioteca
GenericName=Biblioteca de libros
Comment=Tus libros: subirlos, convertirlos y pasarlos al Kindle
Exec=$BIN/vidalibre
Icon=vidalibre
Terminal=false
Categories=Office;Viewer;Literature;
Keywords=libros;epub;kindle;calibre;biblioteca;convertir;
DESKTOP
update-desktop-database "$APLICACIONES" 2>/dev/null || true

echo
echo "Listo."
echo "  · Escribe  vidalibre  en la terminal, o busca «Vidalibre» en el menú."
echo "  · Biblioteca: $(grep VIDALIBRE_BIBLIOTECA "$AJUSTES" | cut -d= -f2-)"
echo "  · Para quitarlo:  ./instalar.sh --desinstalar"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "  · Ojo: $BIN no está en tu PATH." ;;
esac
