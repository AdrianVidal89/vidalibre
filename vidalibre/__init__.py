"""Vidalibre — la biblioteca de libros de Adrian.

Dos modos de funcionamiento con el mismo código:

- **nas**: corre en el NAS, siempre encendida, sirve la biblioteca a la casa
  entera. No ve ningún Kindle porque los USB se enchufan al portátil.
- **local**: corre en el portátil contra la misma biblioteca (la carpeta del
  NAS montada) y además **sí** ve el Kindle conectado: enviar libros, reponer
  portadas y guardar conversiones en una carpeta del ordenador.

Todo se configura con variables de entorno, sin ficheros de configuración:

    VIDALIBRE_BIBLIOTECA   carpeta de la biblioteca de Calibre (con metadata.db)
    VIDALIBRE_DATOS        carpeta de trabajo (miniaturas, temporales)
    VIDALIBRE_MODO         nas | local
    VIDALIBRE_PUERTO       puerto en el que escucha
    VIDALIBRE_TITULO       nombre que sale en la cabecera
    VIDALIBRE_SOLO_LECTURA 1 para no dejar escribir nada (modo pánico)
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "1.0.0"
NOMBRE = "Vidalibre"


def _bool(valor, por_defecto=False):
    if valor is None:
        return por_defecto
    return str(valor).strip().lower() in ("1", "si", "sí", "true", "yes", "on")


class Ajustes:
    def __init__(self, entorno=None):
        entorno = entorno or os.environ
        self.biblioteca = Path(
            entorno.get("VIDALIBRE_BIBLIOTECA", "/biblioteca")
        ).expanduser()
        self.datos = Path(entorno.get("VIDALIBRE_DATOS", "/datos")).expanduser()
        self.modo = entorno.get("VIDALIBRE_MODO", "nas").strip().lower()
        self.puerto = int(entorno.get("VIDALIBRE_PUERTO", "8084"))
        self.titulo = entorno.get("VIDALIBRE_TITULO", NOMBRE)
        self.solo_lectura = _bool(entorno.get("VIDALIBRE_SOLO_LECTURA"))
        self.por_pagina = int(entorno.get("VIDALIBRE_POR_PAGINA", "48"))
        self.carpeta_descargas = entorno.get("VIDALIBRE_DESCARGAS", "")
        # La biblioteca de casa, para cuando esta copia no puede escribir en el
        # catálogo (carpeta de red). Ver `puente.py`.
        self.servidor = entorno.get("VIDALIBRE_SERVIDOR", "").strip().rstrip("/")
        self.subida_maxima_mb = int(entorno.get("VIDALIBRE_SUBIDA_MAXIMA_MB", "600"))

    @property
    def con_dispositivo(self):
        """El Kindle solo se busca donde puede haber uno enchufado."""
        return self.modo == "local"

    @property
    def miniaturas(self):
        return self.datos / "miniaturas"

    @property
    def temporales(self):
        return self.datos / "temporales"


def _barrer_temporales(ajustes, dias=2):
    """Tira los convertidos sueltos de hace días.

    Lo que se convierte "para descargármelo" no entra en la biblioteca: se
    queda en una carpeta de paso. Sin esto, cada conversión de esas deja un
    libro ahí para siempre y la carpeta de datos crece sola.
    """
    import shutil
    import time

    limite = time.time() - dias * 86400
    try:
        for carpeta in ajustes.temporales.iterdir():
            try:
                if carpeta.stat().st_mtime < limite:
                    shutil.rmtree(carpeta, ignore_errors=True) if carpeta.is_dir() \
                        else carpeta.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def puede_escribir(ajustes, catalogo):
    """¿Escribe esta copia en el catálogo, o se lo pide a la biblioteca de casa?

    Si hay un servidor configurado, **siempre** escribe él. No es manía: el
    catálogo vive en una carpeta de red y los cerrojos de SQLite no cruzan bien
    por ahí. Probándolo pasó justo eso: el portátil intentó apuntar algo
    mientras el NAS convertía un libro y se encontró la base de datos
    bloqueada. Con un solo escritor, eso no ocurre.
    """
    if getattr(ajustes, "servidor", ""):
        return False
    return catalogo.escritura_posible()


def crear_app(ajustes=None):
    from flask import Flask

    from .catalogo import Catalogo
    from .motor import ColaTrabajos
    from .servicio import Biblioteca

    ajustes = ajustes or Ajustes()
    app = Flask(
        __name__,
        static_folder="estatico",
        static_url_path="/estatico",
        template_folder="plantillas",
    )
    app.config["AJUSTES"] = ajustes
    app.config["MAX_CONTENT_LENGTH"] = ajustes.subida_maxima_mb * 1024 * 1024
    app.config["SECRET_KEY"] = os.environ.get("VIDALIBRE_CLAVE", "vidalibre-local")
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = _bool(os.environ.get("VIDALIBRE_DEPURAR"))

    for carpeta in (ajustes.datos, ajustes.miniaturas, ajustes.temporales):
        try:
            carpeta.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    _barrer_temporales(ajustes)

    catalogo = Catalogo(ajustes.biblioteca)
    app.config["CATALOGO"] = catalogo
    from .puente import Puente

    app.config["PUENTE"] = Puente(ajustes.servidor)
    app.config["BIBLIOTECA"] = Biblioteca(
        catalogo, firma=f"{NOMBRE} ({__version__})", puente=app.config["PUENTE"]
    )
    app.config["COLA"] = ColaTrabajos()

    from . import lector_web, opds, web

    app.register_blueprint(web.paginas)
    app.register_blueprint(lector_web.paginas_lector)
    app.register_blueprint(opds.catalogo_opds)
    return app
