"""Saber si un libro viene con candado (DRM).

Vidalibre **no quita el DRM**: en España saltarse esa protección es ilegal
aunque el libro sea tuyo, y un módulo para hacerlo sería exactamente eso. Lo
que sí hace es **reconocerlo y decirlo claro**, que es lo que faltaba: hasta
ahora, un libro con DRM daba un error incomprensible de Calibre a mitad de una
conversión, o se quedaba en blanco al abrirlo, y no había forma de saber por
qué.

Cómo se reconoce cada formato:

- **EPUB**: lleva dentro un `META-INF/encryption.xml`. Ojo, que ese fichero
  también aparece en libros sin DRM: muchas editoriales lo usan solo para
  *ofuscar las tipografías*, que es una práctica legítima del propio estándar.
  Por eso se mira **qué** está cifrado: si lo que se cifra son las páginas del
  libro, hay DRM; si solo son ficheros de letras, no.
- **MOBI / AZW3**: el tipo de cifrado va en la cabecera, a dos bytes fijos. 0
  es sin protección, 1 el candado viejo de Mobipocket y 2 el de Amazon.
- **PDF**: lleva un diccionario `/Encrypt`. Puede ser solo una contraseña de
  permisos (imprimir, copiar) o un candado de verdad.
- **ACSM**: no es un libro, es el vale de Adobe para descargarlo. Sin su
  programa no hay nada dentro.
"""

from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path

# Lo que marca que un EPUB solo tiene las tipografías ofuscadas, que no es DRM.
ALGORITMOS_TIPOGRAFIA = (
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#RC",
)

SIN_CANDADO = {"protegido": False, "tipo": "", "detalle": ""}


def _resultado(tipo, detalle):
    return {"protegido": True, "tipo": tipo, "detalle": detalle}


def estado(ruta):
    """¿Este fichero tiene DRM? Devuelve un diccionario, nunca lanza."""
    ruta = Path(ruta)
    try:
        if not ruta.is_file():
            return SIN_CANDADO
        extension = ruta.suffix.lower().lstrip(".")
        if extension == "epub":
            return _epub(ruta)
        if extension in ("mobi", "azw", "azw3", "prc", "azw4"):
            return _kindle(ruta)
        if extension == "pdf":
            return _pdf(ruta)
        if extension == "acsm":
            return _resultado(
                "Adobe",
                "Un .acsm no es un libro: es el vale para descargarlo con Adobe Digital "
                "Editions. Hay que canjearlo en su programa.",
            )
        if extension in ("kfx", "azw8"):
            return _resultado(
                "Amazon (KFX)",
                "Formato nuevo de Amazon. Ni se puede convertir ni leer fuera de sus aparatos.",
            )
    except Exception:
        return SIN_CANDADO
    return SIN_CANDADO


def _epub(ruta):
    try:
        with zipfile.ZipFile(ruta) as zip_libro:
            nombres = set(zip_libro.namelist())
            if "META-INF/rights.xml" in nombres:
                return _resultado("Adobe", "Lleva la licencia de Adobe dentro (rights.xml).")
            if "META-INF/encryption.xml" not in nombres:
                return SIN_CANDADO
            cifrado = zip_libro.read("META-INF/encryption.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError, OSError):
        return SIN_CANDADO

    algoritmos = re.findall(r'Algorithm="([^"]+)"', cifrado)
    objetivos = re.findall(r'URI="([^"]+)"', cifrado)
    solo_letras = all(
        any(algoritmo.startswith(bueno) for bueno in ALGORITMOS_TIPOGRAFIA)
        for algoritmo in algoritmos
    ) if algoritmos else False
    if solo_letras or all(
        obj.lower().endswith((".ttf", ".otf", ".woff", ".woff2")) for obj in objetivos
    ) and objetivos:
        return SIN_CANDADO      # solo tipografías ofuscadas: el libro se abre bien
    return _resultado(
        "Adobe / editorial",
        "El contenido del EPUB está cifrado, no solo las tipografías.",
    )


def _kindle(ruta):
    try:
        with open(ruta, "rb") as fichero:
            cabecera = fichero.read(78 + 8)
            if len(cabecera) < 86:
                return SIN_CANDADO
            (registros,) = struct.unpack(">H", cabecera[76:78])
            if registros < 1:
                return SIN_CANDADO
            (inicio,) = struct.unpack(">I", cabecera[78:82])
            fichero.seek(inicio)
            registro = fichero.read(16)
    except (OSError, struct.error):
        return SIN_CANDADO
    if len(registro) < 14:
        return SIN_CANDADO
    (cifrado,) = struct.unpack(">H", registro[12:14])
    if cifrado == 1:
        return _resultado("Mobipocket", "Protección antigua de Mobipocket.")
    if cifrado == 2:
        return _resultado(
            "Amazon",
            "Comprado en Amazon y atado a una cuenta. Solo se abre en sus aparatos y aplicaciones.",
        )
    return SIN_CANDADO


def _pdf(ruta, trozo=2 * 1024 * 1024):
    try:
        with open(ruta, "rb") as fichero:
            principio = fichero.read(trozo)
            fichero.seek(max(0, Path(ruta).stat().st_size - trozo))
            final = fichero.read(trozo)
    except OSError:
        return SIN_CANDADO
    if b"/Encrypt" in principio or b"/Encrypt" in final:
        return _resultado(
            "PDF cifrado",
            "El PDF lleva protección. Si solo es de permisos (no dejar copiar o imprimir), "
            "puede que se lea igual.",
        )
    return SIN_CANDADO


def mensaje(estado_drm, accion="abrir"):
    """Una frase para enseñar en pantalla, sin tecnicismos."""
    if not estado_drm.get("protegido"):
        return ""
    return (
        f"Este libro viene con candado ({estado_drm['tipo']}): {estado_drm['detalle']} "
        f"Vidalibre no puede {accion}lo, y quitar esa protección no es legal aunque el "
        "libro sea tuyo. Si lo compraste con DRM, busca la versión sin él o léelo en la "
        "aplicación de su tienda."
    )
