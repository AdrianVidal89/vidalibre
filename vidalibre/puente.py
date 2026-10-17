"""El puente entre la copia del portátil y la del NAS.

El problema que resuelve, que es de los que no se ven hasta que muerden:

La biblioteca del NAS se ve en el portátil como una carpeta de red (SMB). Leer
de ahí va bien, pero **escribir en el catálogo no**: SQLite necesita echar
cerrojos sobre trozos del fichero y por red eso no funciona; el intento acaba
en «database is locked» y, si se forzara, en un catálogo roto. Cualquier
programa que guarde datos en un SQLite sobre una carpeta compartida tiene este
problema, aunque muchos no se enteren hasta que pierden datos.

La solución es la de siempre para estas cosas: **que escriba uno solo**. El NAS
es el dueño del catálogo. La copia del portátil lee directamente de la carpeta
(es rápido y no molesta a nadie) y, cuando hay algo que apuntar —por dónde vas
leyendo, un formato nuevo recién convertido, un libro nuevo—, se lo pide al NAS
por la misma web que usa el navegador.

Si el NAS no responde, no se pierde nada: se avisa y el fichero convertido
sigue en su sitio, listo para que lo recoja el NAS cuando vuelva.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

TIEMPO_LIMITE = 25


class ErrorPuente(Exception):
    pass


class Puente:
    def __init__(self, servidor):
        self.servidor = (servidor or "").rstrip("/")

    def __bool__(self):
        return bool(self.servidor)

    # ------------------------------------------------------------------ bajo
    def _pedir(self, ruta, datos=None, tipo="application/json", metodo="POST"):
        if not self.servidor:
            raise ErrorPuente("No sé dónde está la biblioteca de casa.")
        peticion = urllib.request.Request(
            self.servidor + ruta, data=datos, method=metodo,
            headers={"Content-Type": tipo, "X-Requested-With": "vidalibre-puente"},
        )
        try:
            with urllib.request.urlopen(peticion, timeout=TIEMPO_LIMITE) as respuesta:
                cuerpo = respuesta.read()
                if respuesta.headers.get_content_type() == "application/json":
                    return json.loads(cuerpo.decode("utf-8"))
                return {"ok": 200 <= respuesta.status < 400}
        except urllib.error.HTTPError as ex:
            raise ErrorPuente(
                f"La biblioteca de casa ha dicho que no ({ex.code})."
            ) from ex
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            raise ErrorPuente(
                "No he podido hablar con la biblioteca de casa. ¿Está encendido el NAS?"
            ) from ex

    def _multiparte(self, ruta, campo, fichero: Path, extras=None):
        """Una subida de fichero, a mano: no merece traerse una biblioteca
        entera para esto."""
        limite = "----vidalibre" + uuid.uuid4().hex
        partes = []
        for clave, valor in (extras or {}).items():
            partes.append(
                f"--{limite}\r\nContent-Disposition: form-data; name=\"{clave}\"\r\n\r\n{valor}\r\n"
                .encode("utf-8")
            )
        tipo = mimetypes.guess_type(fichero.name)[0] or "application/octet-stream"
        partes.append(
            f"--{limite}\r\nContent-Disposition: form-data; name=\"{campo}\"; "
            f"filename=\"{fichero.name}\"\r\nContent-Type: {tipo}\r\n\r\n".encode("utf-8")
        )
        cuerpo = b"".join(partes) + fichero.read_bytes() + f"\r\n--{limite}--\r\n".encode("utf-8")
        return self._pedir(ruta, cuerpo, tipo=f"multipart/form-data; boundary={limite}")

    # ----------------------------------------------------------------- alto
    def estado(self):
        try:
            return self._pedir("/estado", metodo="GET")
        except ErrorPuente:
            return None

    def guardar_posicion(self, id_libro, formato, posicion, fraccion):
        return self._pedir(
            "/api/posicion",
            json.dumps({"libro": id_libro, "formato": formato,
                        "posicion": posicion, "fraccion": fraccion}).encode("utf-8"),
        )

    def registrar_formato(self, id_libro, formato):
        """El fichero ya está en su carpeta (escribir ficheros por red sí
        funciona); esto solo le dice al NAS que lo apunte en el catálogo."""
        return self._pedir(
            "/api/registrar-formato",
            json.dumps({"libro": id_libro, "formato": formato}).encode("utf-8"),
        )

    def subir_libro(self, ruta):
        return self._multiparte("/subir", "libros", Path(ruta))
