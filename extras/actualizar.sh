#!/bin/sh
# ==============================================================================
# actualizar.sh — pone Vidalibre al día en el NAS desde GitHub
# ==============================================================================
#   sh /share/homes/adrian/scripts/actualizar-vidalibre.sh          (rama main)
#   sh /share/homes/adrian/scripts/actualizar-vidalibre.sh otra-rama
#
# En el shell del QNAP no hay git, así que actualizar es bajarse el paquete del
# repositorio y cambiar la carpeta de sitio.
#
# Qué se respeta pase lo que pase:
#   - el fichero .env (la configuración: qué carpeta es la biblioteca, el puerto)
#   - la carpeta datos/ (miniaturas, medidas de los libros y las copias del
#     catálogo que hace deploy.sh antes de cada despliegue)
#
# La versión anterior queda en vidalibre.anterior por si hay que volver atrás.
#
# Este script vive FUERA de la carpeta que reemplaza, a propósito: si viviera
# dentro, se estaría borrando a sí mismo a media ejecución.
# ==============================================================================
set -e

REPO=https://github.com/AdrianVidal89/vidalibre
RAMA="${1:-main}"
APPS="${VIDALIBRE_APPS:-/share/homes/adrian/apps}"
DESTINO="$APPS/vidalibre"
NUEVO="$APPS/.vidalibre-nuevo"
ANTERIOR="$APPS/vidalibre.anterior"
PAQUETE="$APPS/.vidalibre.tgz"

PATH="/share/CACHEDEV1_DATA/.qpkg/container-station/bin:$PATH"
export PATH
DOCKER_CONFIG=/tmp/dockercfg
export DOCKER_CONFIG

aviso() { echo "[$(date '+%H:%M:%S')] $1"; }

[ -d "$DESTINO" ] || { echo "ERROR: no existe $DESTINO"; exit 1; }
[ -f "$DESTINO/.env" ] || { echo "ERROR: falta $DESTINO/.env"; exit 1; }

aviso "Descargando la rama $RAMA…"
rm -rf "$NUEVO" "$PAQUETE"
curl -fsSL "$REPO/archive/refs/heads/$RAMA.tar.gz" -o "$PAQUETE"
tar xzf "$PAQUETE" -C "$APPS"
rm -f "$PAQUETE"
mv "$APPS/vidalibre-$RAMA" "$NUEVO"

# Si la descarga viniera a medias, mejor enterarse ahora que con todo parado.
[ -f "$NUEVO/servidor.py" ] || { echo "ERROR: la descarga no es válida"; rm -rf "$NUEVO"; exit 1; }
[ -f "$NUEVO/docker-compose.yml" ] || { echo "ERROR: la descarga no es válida"; rm -rf "$NUEVO"; exit 1; }

aviso "Conservando la configuración y los datos…"
cp -p "$DESTINO/.env" "$NUEVO/.env"
if [ -d "$DESTINO/datos" ]; then
    mv "$DESTINO/datos" "$NUEVO/datos"
fi

aviso "Parando Vidalibre…"
cd "$DESTINO"
docker compose down || true

aviso "Cambiando de versión…"
rm -rf "$ANTERIOR"
mv "$DESTINO" "$ANTERIOR"
mv "$NUEVO" "$DESTINO"

aviso "Construyendo y levantando…"
cd "$DESTINO"
sh deploy.sh
