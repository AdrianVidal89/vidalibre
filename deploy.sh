#!/bin/sh
# ==============================================================================
# deploy.sh — construir, levantar y vigilar Vidalibre en el NAS
# ==============================================================================
#   sh deploy.sh            construir y levantar (lo normal)
#   sh deploy.sh estado     ver si está en marcha
#   sh deploy.sh logs [n]   últimas n líneas del registro
#   sh deploy.sh parar      pararlo
#   sh deploy.sh reiniciar  reiniciarlo sin reconstruir
#
# En el shell del QNAP, `docker` no está en el PATH y el aviso de permisos de
# ~/.docker/config.json ensucia la salida: las dos primeras líneas lo arreglan.
# ==============================================================================
set -e

PATH="/share/CACHEDEV1_DATA/.qpkg/container-station/bin:$PATH"
export PATH
DOCKER_CONFIG=/tmp/dockercfg
export DOCKER_CONFIG

CARPETA=$(cd "$(dirname "$0")" && pwd)
cd "$CARPETA"

aviso() { echo "[$(date '+%H:%M:%S')] $1"; }

puerto() {
    if [ -f .env ]; then
        grep '^PUERTO=' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || true
    fi
}
PUERTO=$(puerto); [ -n "$PUERTO" ] || PUERTO=8084

case "${1:-desplegar}" in
  estado)
    docker compose ps
    echo
    curl -fsS "http://localhost:$PUERTO/estado" 2>/dev/null || echo "No responde en el $PUERTO"
    echo
    exit 0 ;;
  logs)
    docker compose logs --tail "${2:-60}"
    exit 0 ;;
  parar)
    docker compose down
    aviso "Parado."
    exit 0 ;;
  reiniciar)
    docker compose restart
    aviso "Reiniciado."
    exit 0 ;;
esac

# --- Comprobaciones antes de tocar nada ---------------------------------------
[ -f .env ] || { echo "ERROR: falta el fichero .env (cópialo de .env.example)"; exit 1; }

BIBLIOTECA=$(grep '^BIBLIOTECA=' .env | cut -d= -f2- | tr -d ' ')
[ -n "$BIBLIOTECA" ] || { echo "ERROR: define BIBLIOTECA en .env"; exit 1; }
[ -f "$BIBLIOTECA/metadata.db" ] || {
    echo "ERROR: no hay ningún metadata.db en $BIBLIOTECA"
    echo "       Esa carpeta tiene que ser la biblioteca de Calibre."
    exit 1
}

# --- Copia de seguridad del catálogo ------------------------------------------
# El catálogo (metadata.db) es el único fichero que no se puede reponer solo:
# los libros están en disco, pero qué libro es cada uno, con su autor y sus
# etiquetas, vive aquí. Se guarda una copia antes de cada despliegue.
COPIAS="$CARPETA/datos/copias"
mkdir -p "$COPIAS"
COPIA="$COPIAS/metadata-$(date '+%Y%m%d-%H%M%S').db"
cp "$BIBLIOTECA/metadata.db" "$COPIA"
aviso "Copia del catálogo en $COPIA"
# Se guardan las diez últimas y se tiran las demás.
ls -1t "$COPIAS"/metadata-*.db 2>/dev/null | tail -n +11 | while read -r VIEJA; do rm -f "$VIEJA"; done

# --- Construir y levantar -----------------------------------------------------
aviso "Construyendo la imagen (la primera vez tarda: lleva Calibre dentro)…"
docker compose build

aviso "Levantando…"
docker compose up -d

aviso "Esperando a que responda…"
INTENTO=0
while [ "$INTENTO" -lt 30 ]; do
    if curl -fsS "http://localhost:$PUERTO/estado" >/dev/null 2>&1; then
        aviso "Listo: http://localhost:$PUERTO/"
        curl -fsS "http://localhost:$PUERTO/estado"
        echo
        exit 0
    fi
    INTENTO=$((INTENTO + 1))
    sleep 3
done

echo "ERROR: no responde en el puerto $PUERTO. Mira el registro:"
docker compose logs --tail 40
exit 1
