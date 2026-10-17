"""Los ficheros de la biblioteca: carpetas, nombres, portadas y metadata.opf.

Calibre guarda cada libro en `Autor/Título (id)/` con los ficheros dentro
llamados `Título - Autor.ext`, más `cover.jpg` y `metadata.opf`. Vidalibre
respeta ese esquema al milímetro: así la misma carpeta sigue abriéndose con
Calibre de escritorio y con Calibre-Web.

Los límites de longitud (96 caracteres para carpetas, 42 para el nombre del
fichero) y la limpieza de caracteres vienen de Calibre-Web, que a su vez los
copió de Calibre. No son arbitrarios: con nombres más largos la ruta completa
se pasa del límite de algunos sistemas de ficheros y la biblioteca deja de ser
copiable a otro disco.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .catalogo import ordenar_autor, ordenar_titulo

# Extensiones que aceptamos como libro.
FORMATOS_ENTRADA = [
    "epub", "mobi", "azw3", "azw", "pdf", "fb2", "lit", "pdb", "rtf",
    "docx", "doc", "odt", "htmlz", "html", "htm", "txt", "txtz", "cbz",
    "cbr", "cb7", "djvu", "lrf", "snb", "rb", "prc", "md",
]
FORMATOS_SALIDA = ["azw3", "mobi", "epub", "pdf", "docx", "txt", "md"]
# Lo que un Kindle lee copiado por USB.
FORMATOS_KINDLE = ["azw3", "mobi", "prc", "azw", "pdf", "txt"]


def nombre_valido(valor, chars=128):
    """Deja un texto en condiciones de ser nombre de fichero o carpeta.

    Portado de `get_valid_filename` de Calibre-Web, sin la parte de pasar a
    ASCII: la biblioteca de Adrian ya tiene carpetas con acentos y eñes
    ('Alba Padró'), y cambiar de criterio ahora renombraría media biblioteca.
    """
    valor = (valor or "").strip()
    if valor.endswith("."):
        valor = valor[:-1] + "_"
    valor = valor.replace("/", "_").replace(":", "_").replace("\0", "")
    valor = re.sub(r'[*+:\\"/<>?]+', "_", valor, flags=re.U)
    valor = re.sub(r"[|]+", ",", valor, flags=re.U)
    # El recorte es en bytes, no en caracteres: es el límite real del disco.
    valor = valor.encode("utf-8")[:chars].decode("utf-8", errors="ignore").strip()
    return valor or "Sin titulo"


def carpeta_relativa(titulo, autor, id_libro):
    """`Autor/Título (id)`, tal cual lo monta Calibre."""
    return "{}/{} ({})".format(
        nombre_valido(autor or "Autor desconocido", chars=96),
        nombre_valido(titulo or "Sin titulo", chars=96),
        id_libro,
    )


def nombre_base(titulo, autor):
    """`Título - Autor`, el nombre de los ficheros dentro de la carpeta."""
    return "{} - {}".format(
        nombre_valido(titulo or "Sin titulo", chars=42),
        nombre_valido(autor or "Autor desconocido", chars=42),
    )


def tamano_legible(bytes_):
    bytes_ = float(bytes_ or 0)
    for unidad in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024 or unidad == "GB":
            if unidad == "B":
                return f"{int(bytes_)} B"
            return f"{bytes_:.1f} {unidad}".replace(".0 ", " ")
        bytes_ /= 1024


def asegurar_carpeta(ruta: Path):
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def _vaciar_padre(carpeta: Path, raiz: Path):
    """Si la carpeta del autor se queda vacía, se borra (como hace Calibre)."""
    padre = carpeta.parent
    try:
        if padre != raiz and padre.is_dir() and not any(padre.iterdir()):
            padre.rmdir()
    except OSError:
        pass


def mover_libro(raiz: Path, ruta_vieja: str, titulo, autor, id_libro, ficheros):
    """Mueve la carpeta del libro y renombra sus ficheros tras un cambio de
    título o de autor.

    Devuelve `(nueva_ruta_relativa, nuevo_nombre_base)`. No toca la base de
    datos: eso lo hace quien llama, dentro de su transacción.
    """
    raiz = Path(raiz)
    nueva_rel = carpeta_relativa(titulo, autor, id_libro)
    nuevo_base = nombre_base(titulo, autor)
    vieja = raiz / ruta_vieja
    nueva = raiz / nueva_rel

    if nueva_rel != ruta_vieja:
        asegurar_carpeta(nueva.parent)
        if vieja.is_dir():
            if nueva.exists():
                # La carpeta destino ya existe (dos libros que acaban con el
                # mismo nombre): se funde el contenido en vez de fallar.
                for origen in vieja.iterdir():
                    destino = nueva / origen.name
                    if not destino.exists():
                        shutil.move(str(origen), str(destino))
                shutil.rmtree(str(vieja), ignore_errors=True)
            else:
                shutil.move(str(vieja), str(nueva))
            _vaciar_padre(vieja, raiz)
        else:
            asegurar_carpeta(nueva)
    else:
        asegurar_carpeta(nueva)

    for fichero in ficheros:
        viejo = nueva / f"{fichero['nombre']}.{fichero['formato'].lower()}"
        nuevo = nueva / f"{nuevo_base}.{fichero['formato'].lower()}"
        if viejo != nuevo and viejo.is_file():
            try:
                shutil.move(str(viejo), str(nuevo))
            except OSError:
                pass

    return nueva_rel, nuevo_base


def ruta_fichero(raiz: Path, libro, formato):
    """Ruta en disco del fichero de un libro en un formato dado."""
    formato = formato.upper()
    for fichero in libro.get("ficheros", []):
        if fichero["formato"] == formato:
            ruta = Path(raiz) / libro["ruta"] / f"{fichero['nombre']}.{formato.lower()}"
            if ruta.is_file():
                return ruta
            # Tolerancia: algún fichero quedó con la extensión en mayúsculas.
            alternativa = Path(raiz) / libro["ruta"] / f"{fichero['nombre']}.{formato}"
            if alternativa.is_file():
                return alternativa
            return ruta
    return None


def ruta_portada(raiz: Path, libro):
    carpeta = Path(raiz) / libro["ruta"]
    for nombre in ("cover.jpg", "cover.jpeg", "cover.png"):
        ruta = carpeta / nombre
        if ruta.is_file():
            return ruta
    return None


def guardar_portada(raiz: Path, libro, datos: bytes, ancho_max=1400):
    """Guarda la portada como `cover.jpg`, reescalada si viene enorme."""
    carpeta = asegurar_carpeta(Path(raiz) / libro["ruta"])
    destino = carpeta / "cover.jpg"
    try:
        import io

        from PIL import Image

        imagen = Image.open(io.BytesIO(datos))
        if imagen.mode not in ("RGB", "L"):
            imagen = imagen.convert("RGB")
        if imagen.width > ancho_max:
            alto = int(imagen.height * ancho_max / imagen.width)
            imagen = imagen.resize((ancho_max, alto), Image.LANCZOS)
        imagen.save(str(destino), "JPEG", quality=88, optimize=True)
    except Exception:
        # Sin Pillow o con una imagen rara: se guarda tal cual llegó.
        destino.write_bytes(datos)
    # Si había una cover.jpeg antigua, estorba: la portada buena es cover.jpg.
    for sobra in ("cover.jpeg", "cover.png"):
        try:
            (carpeta / sobra).unlink()
        except OSError:
            pass
    return destino


def borrar_portada(raiz: Path, libro):
    carpeta = Path(raiz) / libro["ruta"]
    for nombre in ("cover.jpg", "cover.jpeg", "cover.png"):
        try:
            (carpeta / nombre).unlink()
        except OSError:
            pass


def borrar_fichero_formato(raiz: Path, libro, formato):
    ruta = ruta_fichero(raiz, libro, formato)
    if ruta and ruta.is_file():
        ruta.unlink()
        return True
    return False


def borrar_carpeta_libro(raiz: Path, libro):
    carpeta = Path(raiz) / libro["ruta"]
    raiz = Path(raiz)
    # Red de seguridad: nunca borrar fuera de la biblioteca ni la raíz misma.
    try:
        carpeta.resolve().relative_to(raiz.resolve())
    except ValueError:
        return False
    if carpeta.resolve() == raiz.resolve() or not carpeta.is_dir():
        return False
    shutil.rmtree(str(carpeta), ignore_errors=True)
    _vaciar_padre(carpeta, raiz)
    return True


# --------------------------------------------------------------- metadata.opf
_PLANTILLA_OPF = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
{cuerpo}
    </metadata>
    <guide>
        <reference type="cover" title="Cover" href="cover.jpg"/>
    </guide>
</package>
"""


def escribir_opf(raiz: Path, libro, version_programa="Vidalibre"):
    """Escribe el `metadata.opf` del libro con el mismo formato que Calibre.

    Ese fichero es la copia de seguridad de los metadatos: si un día el
    `metadata.db` se corrompe, Calibre reconstruye la biblioteca leyéndolos.
    Por eso se reescribe en cada edición.
    """
    carpeta = asegurar_carpeta(Path(raiz) / libro["ruta"])
    lineas = []
    af = lambda s: escape(str(s))  # noqa: E731

    lineas.append(f'        <dc:identifier opf:scheme="calibre" id="calibre_id">{libro["id"]}</dc:identifier>')
    if libro.get("uuid"):
        lineas.append(f'        <dc:identifier opf:scheme="uuid" id="uuid_id">{af(libro["uuid"])}</dc:identifier>')
    lineas.append(f'        <dc:title>{af(libro["titulo"])}</dc:title>')
    for autor in libro.get("autores") or []:
        lineas.append(
            '        <dc:creator opf:file-as={} opf:role="aut">{}</dc:creator>'.format(
                quoteattr(ordenar_autor(autor)), af(autor)
            )
        )
    lineas.append(
        '        <dc:contributor opf:file-as="calibre" opf:role="bkp">'
        f"{af(version_programa)}</dc:contributor>"
    )
    if libro.get("publicado"):
        lineas.append(f'        <dc:date>{af(str(libro["publicado"]).replace(" ", "T"))}</dc:date>')
    if libro.get("descripcion"):
        lineas.append(f'        <dc:description>{af(libro["descripcion"])}</dc:description>')
    if libro.get("editorial"):
        lineas.append(f'        <dc:publisher>{af(libro["editorial"])}</dc:publisher>')
    for tipo, valor in (libro.get("identificadores") or {}).items():
        lineas.append(
            '        <dc:identifier opf:scheme={}>{}</dc:identifier>'.format(
                quoteattr(tipo.upper()), af(valor)
            )
        )
    for idioma in libro.get("idiomas") or []:
        lineas.append(f"        <dc:language>{af(idioma)}</dc:language>")
    for etiqueta in libro.get("etiquetas") or []:
        lineas.append(f"        <dc:subject>{af(etiqueta)}</dc:subject>")
    if libro.get("serie"):
        lineas.append(f'        <meta name="calibre:series" content={quoteattr(libro["serie"])}/>')
        indice = libro.get("indice_serie") or 1
        indice = int(indice) if float(indice).is_integer() else indice
        lineas.append(f'        <meta name="calibre:series_index" content="{indice}"/>')
    if libro.get("valoracion"):
        lineas.append(f'        <meta name="calibre:rating" content="{int(libro["valoracion"])}"/>')
    if libro.get("anadido"):
        lineas.append(
            '        <meta name="calibre:timestamp" content={}/>'.format(
                quoteattr(str(libro["anadido"]).replace(" ", "T"))
            )
        )
    lineas.append(
        '        <meta name="calibre:title_sort" content={}/>'.format(
            quoteattr(libro.get("orden_titulo") or ordenar_titulo(libro["titulo"]))
        )
    )

    destino = carpeta / "metadata.opf"
    destino.write_text(_PLANTILLA_OPF.format(cuerpo="\n".join(lineas)), encoding="utf-8")
    return destino


def espacio_libre(ruta):
    try:
        uso = shutil.disk_usage(str(ruta))
        return uso.free
    except OSError:
        return None


def recibir_subida(fichero, carpeta_temporal: Path):
    """Guarda en disco un fichero que llega por el formulario de subida."""
    nombre = os.path.basename(fichero.filename or "libro")
    extension = Path(nombre).suffix.lower().lstrip(".")
    if extension not in FORMATOS_ENTRADA:
        raise ValueError(f"No sé qué hacer con un fichero .{extension}")
    destino = asegurar_carpeta(carpeta_temporal) / nombre_valido(nombre, chars=180)
    fichero.save(str(destino))
    return destino, extension
