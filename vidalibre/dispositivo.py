"""El aparato conectado: Kindle por USB.

Esto solo funciona donde esté enchufado el Kindle, que es el **portátil**, no
el NAS: un servidor no ve los USB de otro ordenador. Vidalibre detecta si hay
un Kindle a mano y, si no lo hay, esconde esta parte.

El truco de la portada viene entero del conversor de libros del escritorio y es
la parte que más costó afinar: el Kindle **no** lee la cubierta de dentro del
libro para pintar la rejilla de la biblioteca. Busca una miniatura en
`system/thumbnails/` con un nombre que incluye el identificador interno del
libro. Si no está, pinta un cuadro gris. Ese identificador vive en la cabecera
EXTH del fichero MOBI/AZW3 y hay que leerlo a mano, byte a byte.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
from pathlib import Path

from . import motor

FORMATOS_USB = ["azw3", "mobi"]        # lo que el Kindle lee copiado por cable
EXTENSIONES_APARATO = (".azw3", ".azw", ".mobi", ".prc", ".pdf", ".txt")

# Donde el escritorio monta los USB. Se puede añadir alguno más con la
# variable VIDALIBRE_BUSCAR_KINDLE (carpetas separadas por dos puntos), que es
# lo que se usa para probar sin un Kindle de verdad delante.
LUGARES = [
    "/run/media/{usuario}",
    "/media/{usuario}",
    "/run/media",
    "/media",
    "/mnt",
    "/Volumes",
] + [l for l in os.environ.get("VIDALIBRE_BUSCAR_KINDLE", "").split(":") if l]


def es_kindle(ruta: Path) -> bool:
    """Un Kindle montado siempre trae las carpetas 'documents' y 'system'."""
    try:
        return (ruta / "documents").is_dir() and (ruta / "system").is_dir()
    except OSError:
        return False


def detectar():
    """Lista de Kindles conectados ahora mismo."""
    usuario = os.environ.get("USER") or os.environ.get("USERNAME") or "adrian"
    vistos, encontrados = set(), []
    for patron in LUGARES:
        base = Path(patron.format(usuario=usuario))
        if not base.is_dir():
            continue
        try:
            candidatos = list(base.iterdir())
        except OSError:
            continue
        for candidato in candidatos:
            if not candidato.is_dir():
                continue
            real = str(candidato.resolve())
            if real in vistos:
                continue
            vistos.add(real)
            if es_kindle(candidato):
                encontrados.append(candidato)
    return encontrados


def kindle_de_ruta(ruta):
    """Si una carpeta está dentro de un Kindle, devuelve la raíz del aparato."""
    try:
        actual = Path(ruta).resolve()
    except OSError:
        return None
    while actual and str(actual) != actual.root:
        if es_kindle(actual):
            return actual
        actual = actual.parent
    return None


def info(kindle: Path):
    """Nombre, espacio y número de libros del aparato."""
    kindle = Path(kindle)
    datos = {"ruta": str(kindle), "nombre": kindle.name, "libros": 0,
             "bytes_libres": None, "bytes_totales": None}
    try:
        uso = shutil.disk_usage(str(kindle))
        datos["bytes_libres"] = uso.free
        datos["bytes_totales"] = uso.total
    except OSError:
        pass
    datos["libros"] = len(libros(kindle))
    # El Kindle pone su número de serie y su modelo aquí.
    version = kindle / "system" / "version.txt"
    if version.is_file():
        try:
            datos["version"] = version.read_text(errors="replace").strip()[:120]
        except OSError:
            pass
    return datos


def libros(kindle: Path, maximo=2000):
    """Lo que ya hay dentro del aparato (sin abrir los ficheros: sería lentísimo)."""
    documentos = Path(kindle) / "documents"
    salida = []
    if not documentos.is_dir():
        return salida
    for ruta in sorted(documentos.rglob("*")):
        if len(salida) >= maximo:
            break
        if not ruta.is_file() or ruta.suffix.lower() not in EXTENSIONES_APARATO:
            continue
        # Las notas y recortes del propio aparato no son libros.
        if ruta.name.startswith(".") or "My Clippings" in ruta.name:
            continue
        salida.append({
            "nombre": ruta.stem,
            "formato": ruta.suffix.lower().lstrip("."),
            "bytes": ruta.stat().st_size,
            "ruta": str(ruta),
            "relativa": str(ruta.relative_to(documentos)),
        })
    return salida


# ------------------------------------------------------------------- portadas
def identificador(ruta):
    """Lee el identificador interno del libro de la cabecera EXTH (MOBI/AZW3).

    Devuelve (identificador, tipo) donde el tipo es EBOK o PDOC. Es lo que el
    Kindle usa para encontrar la miniatura de portada en su caché.
    """
    try:
        with open(ruta, "rb") as fichero:
            cabecera = fichero.read(82)
            if len(cabecera) < 82:
                return None
            registro0 = struct.unpack(">I", cabecera[78:82])[0]
            fichero.seek(registro0)
            bloque = fichero.read(16384)
        if bloque[16:20] != b"MOBI":
            return None
        largo_mobi = struct.unpack(">I", bloque[20:24])[0]
        exth = bloque[16 + largo_mobi:]
        if exth[:4] != b"EXTH":
            return None
        total = struct.unpack(">I", exth[8:12])[0]
        posicion, campos = 12, {}
        for _ in range(total):
            tipo, largo = struct.unpack(">II", exth[posicion:posicion + 8])
            if largo < 8:
                break
            campos[tipo] = exth[posicion + 8:posicion + largo]
            posicion += largo
        ident = campos.get(504) or campos.get(113)      # ASIN si lo hay, si no el uuid
        cde = campos.get(501) or b"EBOK"
        if not ident:
            return None
        return (
            ident.decode("ascii", "ignore").strip(),
            cde.decode("ascii", "ignore").strip(),
        )
    except Exception:
        return None


def poner_portada(libro_en_kindle, kindle, origen=None):
    """Genera la miniatura de portada del Kindle para un libro ya copiado.

    `origen` es el fichero del que salió la conversión: algunos libros pierden
    la cubierta al convertirse pero el original sí la trae, y así se recupera.
    """
    datos = identificador(libro_en_kindle)
    if not datos:
        return False
    ident, cde = datos
    carpeta = Path(kindle) / "system" / "thumbnails"
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        temporal = Path(tmp) / "cover.jpg"
        imagen = motor.extraer_portada(libro_en_kindle, temporal)
        if not imagen and origen and Path(origen).is_file():
            temporal.unlink(missing_ok=True)
            imagen = motor.extraer_portada(origen, temporal)
        if not imagen:
            return False

        destino = carpeta / f"thumbnail_{ident}_{cde}_portrait.jpg"
        # El Kindle deja miniaturas a medio hacer con esta extensión: estorban.
        (carpeta / f"{destino.name}.tmp.partial").unlink(missing_ok=True)
        try:
            from PIL import Image

            img = Image.open(temporal)
            if img.mode != "RGB":
                img = img.convert("RGB")
            ancho = max(1, int(img.width * 500 / img.height))
            img.resize((ancho, 500), Image.LANCZOS).save(str(destino), "JPEG", quality=90)
        except Exception:
            shutil.copy2(str(temporal), str(destino))
    return True


def reparar_portadas(kindle, trabajo=None):
    """Recorre los libros del aparato y les regenera la miniatura.

    Para los que se copiaron a mano arrastrándolos y salieron grises.
    """
    kindle = Path(kindle)
    candidatos = [
        l for l in libros(kindle)
        if l["formato"] in ("azw3", "azw", "mobi", "prc")
    ]
    hechas, fallidas = [], []
    if trabajo:
        trabajo.pasos = max(1, len(candidatos))
    for indice, libro in enumerate(candidatos, start=1):
        if trabajo:
            if trabajo.cancelar:
                break
            trabajo.paso = indice
            trabajo.anotar(f"Portada ({indice}/{len(candidatos)}): {libro['nombre']}")
        if poner_portada(libro["ruta"], kindle):
            hechas.append(libro["nombre"])
        else:
            fallidas.append(libro["nombre"])
    _sincronizar()
    return hechas, fallidas


def _sincronizar():
    """Vuelca a disco lo escrito: si no, al desenchufar se puede perder."""
    try:
        os.sync()
    except Exception:
        subprocess.run(["sync"], check=False)


def copiar_al_aparato(fichero, kindle, nombre_destino=None, origen=None, subcarpeta=None):
    """Copia un fichero ya convertido al Kindle y le prepara la portada."""
    kindle = Path(kindle)
    destino_dir = kindle / "documents"
    if subcarpeta:
        destino_dir = destino_dir / subcarpeta
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / (nombre_destino or Path(fichero).name)
    shutil.copy2(str(fichero), str(destino))
    con_portada = False
    if destino.suffix.lower().lstrip(".") in ("azw3", "mobi", "azw", "prc"):
        con_portada = poner_portada(destino, kindle, origen=origen)
    _sincronizar()
    return destino, con_portada
