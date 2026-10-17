#!/usr/bin/env python3
"""Arranca Vidalibre.

    python servidor.py                 # con lo que digan las variables de entorno
    python servidor.py --local         # contra la biblioteca del NAS montada, con Kindle

En el NAS lo arranca gunicorn desde el Dockerfile; esto es para el portátil y
para desarrollar.
"""

import argparse
import os
import sys
import webbrowser

from vidalibre import Ajustes, crear_app


def principal():
    parser = argparse.ArgumentParser(description="Vidalibre — la biblioteca de libros")
    parser.add_argument("--biblioteca", help="carpeta de la biblioteca de Calibre")
    parser.add_argument("--puerto", type=int, help="puerto (por defecto 8084)")
    parser.add_argument("--datos", help="carpeta de trabajo")
    parser.add_argument("--local", action="store_true",
                        help="modo portátil: busca el Kindle conectado")
    parser.add_argument("--abrir", action="store_true", help="abrir el navegador al arrancar")
    parser.add_argument("--depurar", action="store_true")
    args = parser.parse_args()

    if args.biblioteca:
        os.environ["VIDALIBRE_BIBLIOTECA"] = args.biblioteca
    if args.datos:
        os.environ["VIDALIBRE_DATOS"] = args.datos
    if args.puerto:
        os.environ["VIDALIBRE_PUERTO"] = str(args.puerto)
    if args.local:
        os.environ["VIDALIBRE_MODO"] = "local"

    ajustes = Ajustes()
    app = crear_app(ajustes)

    if not ajustes.biblioteca.joinpath("metadata.db").is_file():
        print(f"Aviso: no encuentro metadata.db en {ajustes.biblioteca}", file=sys.stderr)

    direccion = f"http://127.0.0.1:{ajustes.puerto}/"
    print(f"Vidalibre en {direccion}  (biblioteca: {ajustes.biblioteca})")
    if args.abrir:
        import threading
        threading.Timer(1.2, lambda: webbrowser.open(direccion)).start()

    if args.depurar:
        app.run(host="0.0.0.0", port=ajustes.puerto, debug=True)
        return
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=ajustes.puerto, threads=6, ident="Vidalibre")
    except ImportError:
        app.run(host="0.0.0.0", port=ajustes.puerto)


if __name__ == "__main__":
    principal()
