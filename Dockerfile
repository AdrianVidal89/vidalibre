# Vidalibre para el NAS.
#
# La imagen lleva Calibre dentro porque el motor de conversión ES Calibre
# (`ebook-convert` y `ebook-meta`). Se instala con el instalador oficial en
# modo aislado, que deja todo en /opt/calibre sin tocar el resto del sistema.
#
# La lista de librerías de abajo no es decorativa: Calibre arrastra Qt aunque
# se use solo por línea de órdenes, y sin ellas `ebook-convert` arranca y se
# cae con un error de biblioteca compartida que no dice nada útil.
FROM python:3.12-slim-bookworm

ARG CALIBRE_VERSION=9.14.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/calibre:${PATH}" \
    HOME=/tmp \
    QT_QPA_PLATFORM=offscreen \
    XDG_CACHE_HOME=/tmp/cache \
    XDG_CONFIG_HOME=/tmp/config

RUN apt-get update && apt-get install -y --no-install-recommends \
        wget xz-utils ca-certificates shared-mime-info libxslt1.1 libxml2 \
        libfontconfig1 libfreetype6 libglib2.0-0 libdbus-1-3 \
        libgl1 libegl1 libopengl0 libxkbcommon0 libxkbcommon-x11-0 \
        libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 \
        libxi6 libxrandr2 libxtst6 libxcomposite1 libxdamage1 libxrender1 \
        libsm6 libice6 libnss3 libnspr4 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Calibre, en modo aislado (sin integración de escritorio ni enlaces fuera).
RUN wget -q -O /tmp/instalar-calibre.sh https://download.calibre-ebook.com/linux-installer.sh \
    && sh /tmp/instalar-calibre.sh version=${CALIBRE_VERSION} install_dir=/opt isolated=y \
    && rm -f /tmp/instalar-calibre.sh \
    && /opt/calibre/ebook-convert --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vidalibre/ ./vidalibre/
COPY servidor.py LEEME.md LICENSE ./

# Calibre escribe su configuración y su caché en estas carpetas. El contenedor
# corre como adrian (1000), no como root: si no existen y con permiso de
# escritura, cada conversión suelta un aviso de «no write access».
# (La comprobación de Calibre durante la construcción corre como root y deja
# /tmp/config/calibre a su nombre: por eso se borra antes de rehacerlo.)
RUN rm -rf /tmp/config /tmp/cache /tmp/calibre-installer-cache \
    && mkdir -p /tmp/config /tmp/cache \
    && chmod 1777 /tmp/config /tmp/cache

ENV VIDALIBRE_BIBLIOTECA=/biblioteca \
    VIDALIBRE_DATOS=/datos \
    VIDALIBRE_MODO=nas \
    VIDALIBRE_PUERTO=8084

EXPOSE 8084
# waitress, un solo proceso con varios hilos: la cola de conversiones vive
# dentro del proceso, así que con varios procesos (gunicorn) cada uno tendría
# su propia cola y los trabajos aparecerían y desaparecerían según quién
# atendiese la petición.
CMD ["python", "servidor.py"]
