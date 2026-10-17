"""El motor: todo lo que hace trabajo de verdad sobre un fichero de libro.

Es el mismo motor del conversor de libros del escritorio (`epub2mobi`), pasado
de script de terminal a módulo: por debajo manda las órdenes a `ebook-convert`
y `ebook-meta`, las dos herramientas de Calibre. Aquí están, una por una, las
decisiones que se aprendieron a base de pruebas en el script original y que no
se deben perder:

- **MOBI se genera con `--mobi-file-type=both`**: así el fichero vale para los
  Kindle viejos y para los nuevos.
- **AZW3 no admite esa opción**: pasársela aborta la conversión.
- **Los PDF entran con `--enable-heuristics`**: un PDF son páginas fijas, no un
  libro; sin las heurísticas sale un churro de líneas partidas.
- **Markdown no es un formato de salida de Calibre**: se saca un `.txt` con
  formato Markdown y se renombra, porque `ebook-convert` no conoce `.md`.
- **El Markdown de Calibre viene roto** y hay que limpiarlo: negritas partidas
  en trozos (`**V****U****ELTA**`), signos escapados y cadenas de `> > >` de
  los MOBI mal convertidos. Sin eso, los títulos de capítulo no se pueden ni
  buscar, que es justo para lo que se extrae el texto.
- **Si el libro ya está en el formato pedido no se reconvierte**: se copia.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

CONVERTIR = os.environ.get("VIDALIBRE_EBOOK_CONVERT", "ebook-convert")
METADATOS = os.environ.get("VIDALIBRE_EBOOK_META", "ebook-meta")
# Una conversión de un libro gordo puede pasar del minuto en el NAS.
LIMITE_SEGUNDOS = int(os.environ.get("VIDALIBRE_LIMITE_CONVERSION", "1800"))

NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class ErrorMotor(Exception):
    pass


def hay_motor():
    """¿Están las herramientas de Calibre disponibles?"""
    return bool(shutil.which(CONVERTIR)) and bool(shutil.which(METADATOS))


def version_calibre():
    try:
        salida = subprocess.run([CONVERTIR, "--version"], capture_output=True, text=True, timeout=60)
        m = re.search(r"calibre\s+([0-9.]+)", salida.stdout or "")
        return m.group(1) if m else (salida.stdout or "").strip().split("\n")[0]
    except Exception:
        return None


def _ejecutar(orden, limite=LIMITE_SEGUNDOS):
    """Lanza una orden y devuelve (ok, salida). Nunca lanza por fallo del proceso."""
    try:
        res = subprocess.run(
            [str(o) for o in orden],
            capture_output=True,
            text=True,
            timeout=limite,
            env={**os.environ, "HOME": os.environ.get("HOME", tempfile.gettempdir())},
        )
        return res.returncode == 0, (res.stdout or "") + (res.stderr or "")
    except subprocess.TimeoutExpired:
        return False, f"La conversión pasó de {limite} segundos y se ha cortado."
    except FileNotFoundError:
        return False, f"No encuentro '{orden[0]}'. Hace falta Calibre instalado."
    except Exception as ex:  # pragma: no cover
        return False, str(ex)


# ----------------------------------------------------------------- metadatos
def _texto(nodo):
    return (nodo.text or "").strip() if nodo is not None else ""


def leer_metadatos(ruta):
    """Saca del fichero todo lo que trae dentro: título, autores, portada...

    Se le pide a `ebook-meta` un OPF y se lee: así vale para EPUB, MOBI, AZW3,
    PDF, FB2 y todo lo que entienda Calibre, sin un parser por formato.
    """
    ruta = Path(ruta)
    datos = {
        "titulo": ruta.stem,
        "autores": [],
        "serie": "",
        "indice_serie": None,
        "editorial": "",
        "publicado": None,
        "idiomas": [],
        "etiquetas": [],
        "descripcion": "",
        "identificadores": {},
    }
    with tempfile.TemporaryDirectory() as tmp:
        opf = Path(tmp) / "meta.opf"
        ok, _salida = _ejecutar([METADATOS, str(ruta), f"--to-opf={opf}"], limite=300)
        if not ok or not opf.is_file():
            return datos
        try:
            arbol = ElementTree.parse(str(opf)).getroot()
        except ElementTree.ParseError:
            return datos

    meta = arbol.find("opf:metadata", NS)
    if meta is None:
        return datos

    titulo = _texto(meta.find("dc:title", NS))
    if titulo:
        datos["titulo"] = titulo
    for creador in meta.findall("dc:creator", NS):
        rol = creador.get("{http://www.idpf.org/2007/opf}role")
        if rol in (None, "aut") and _texto(creador):
            datos["autores"].append(_texto(creador))
    datos["editorial"] = _texto(meta.find("dc:publisher", NS))
    datos["descripcion"] = _texto(meta.find("dc:description", NS))
    for idioma in meta.findall("dc:language", NS):
        if _texto(idioma):
            datos["idiomas"].append(_texto(idioma))
    for materia in meta.findall("dc:subject", NS):
        if _texto(materia):
            datos["etiquetas"].append(_texto(materia))
    fecha = _texto(meta.find("dc:date", NS))
    if fecha:
        datos["publicado"] = fecha.replace("T", " ").replace("Z", "")
    for ident in meta.findall("dc:identifier", NS):
        esquema = (ident.get("{http://www.idpf.org/2007/opf}scheme") or "").lower()
        valor = _texto(ident)
        if esquema and valor and esquema not in ("calibre", "uuid"):
            datos["identificadores"][esquema] = valor
    for etiqueta in meta.findall("opf:meta", NS) + meta.findall("meta"):
        nombre = etiqueta.get("name") or ""
        contenido = etiqueta.get("content") or ""
        if nombre == "calibre:series":
            datos["serie"] = contenido
        elif nombre == "calibre:series_index":
            try:
                datos["indice_serie"] = float(contenido)
            except ValueError:
                pass
    return datos


def extraer_portada(ruta, destino):
    """Saca la imagen de cubierta que trae el libro dentro. Devuelve bytes o None."""
    destino = Path(destino)
    ok, _ = _ejecutar([METADATOS, str(ruta), f"--get-cover={destino}"], limite=300)
    if ok and destino.is_file() and destino.stat().st_size > 0:
        return destino.read_bytes()
    return None


def escribir_metadatos(ruta, libro, portada=None):
    """Mete los metadatos de la ficha DENTRO del fichero del libro.

    Para que el Kindle (o cualquier lector) muestre el título y el autor que
    dice la ficha, y no los que traía el fichero de origen.
    """
    orden = [METADATOS, str(ruta)]
    if libro.get("titulo"):
        orden.append(f"--title={libro['titulo']}")
    if libro.get("autores"):
        orden.append("--authors=" + " & ".join(libro["autores"]))
        orden.append("--author-sort=" + (libro.get("orden_autor") or ""))
    if libro.get("editorial"):
        orden.append(f"--publisher={libro['editorial']}")
    if libro.get("descripcion"):
        orden.append(f"--comments={libro['descripcion']}")
    if libro.get("etiquetas"):
        orden.append("--tags=" + ", ".join(libro["etiquetas"]))
    if libro.get("serie"):
        orden.append(f"--series={libro['serie']}")
        orden.append(f"--index={libro.get('indice_serie') or 1}")
    if libro.get("idiomas"):
        orden.append(f"--language={libro['idiomas'][0]}")
    if libro.get("identificadores", {}).get("isbn"):
        orden.append(f"--isbn={libro['identificadores']['isbn']}")
    if portada and Path(portada).is_file():
        orden.append(f"--cover={portada}")
    return _ejecutar(orden, limite=600)


# ---------------------------------------------------------------- conversión
def _limpiar_markdown(texto):
    """Deshace los destrozos que deja Calibre al pasar un libro a Markdown."""
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = texto.replace("****", "")
    texto = re.sub(r"\*\*(\s*)\*\*", r"\1", texto)
    texto = re.sub(r"\\([()\[\].,:;!?<>\"'])", r"\1", texto)
    texto = re.sub(r"(?:>[ \t]*){3,}", " ", texto)
    texto = "\n".join(l.rstrip() for l in texto.split("\n"))
    # Como mucho una línea en blanco seguida (el `cat -s` del script original).
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto


def _cabecera_markdown(metadatos, nombre_origen):
    """Las propiedades de Obsidian al principio del .md."""
    def limpio(valor):
        return str(valor or "").replace('"', "'").strip()

    lineas = ["---", f'titulo: "{limpio(metadatos.get("titulo"))}"']
    if metadatos.get("autores"):
        lineas.append(f'autor: "{limpio(" & ".join(metadatos["autores"]))}"')
    if metadatos.get("editorial"):
        lineas.append(f'editorial: "{limpio(metadatos["editorial"])}"')
    if metadatos.get("publicado"):
        lineas.append(f"publicado: {str(metadatos['publicado'])[:10]}")
    if metadatos.get("serie"):
        lineas.append(f'serie: "{limpio(metadatos["serie"])}"')
    lineas += [
        f'origen: "{nombre_origen}"',
        f"convertido: {date.today().isoformat()}",
        "tipo: libro",
        "---",
        "",
    ]
    return "\n".join(lineas)


def convertir(origen, destino, formato, metadatos=None, registro=None):
    """Convierte un libro. `formato` en minúsculas ('azw3', 'mobi', 'epub', 'md'...).

    Si el origen ya está en ese formato, copia en vez de reconvertir.
    Devuelve (ok, texto_del_proceso).
    """
    origen, destino = Path(origen), Path(destino)
    formato = formato.lower().lstrip(".")
    destino.parent.mkdir(parents=True, exist_ok=True)

    if origen.suffix.lower().lstrip(".") == formato:
        if origen.resolve() != destino.resolve():
            shutil.copy2(str(origen), str(destino))
        return True, "Ya venía en ese formato: copiado sin convertir."

    extra = []
    if origen.suffix.lower() == ".pdf":
        extra.append("--enable-heuristics")

    if formato == "md":
        return _convertir_markdown(origen, destino, metadatos, extra)
    if formato == "mobi":
        extra.append("--mobi-file-type=both")

    ok, salida = _ejecutar([CONVERTIR, str(origen), str(destino)] + extra)
    if ok and not destino.is_file():
        return False, salida + "\nLa conversión dijo que fue bien pero no dejó fichero."
    return ok, salida


def _convertir_markdown(origen, destino, metadatos, extra):
    with tempfile.TemporaryDirectory() as tmp:
        txt = Path(tmp) / "extraccion.txt"
        ok, salida = _ejecutar(
            [CONVERTIR, str(origen), str(txt), "--txt-output-formatting=markdown"] + extra
        )
        if not ok or not txt.is_file():
            return False, salida
        texto = _limpiar_markdown(txt.read_text(encoding="utf-8", errors="replace"))
        meta = metadatos or leer_metadatos(origen)
        destino.write_text(
            _cabecera_markdown(meta, origen.name) + texto, encoding="utf-8"
        )
    return True, salida


# ------------------------------------------------------- cola de trabajos
def soltar_memoria():
    """Devuelve al sistema la memoria que ya no se usa.

    Python suelta la memoria de sus objetos, pero la biblioteca de C del
    sistema se la queda reservada «por si acaso», y en un servidor que corre
    meses eso se nota: la aplicación aparenta ocupar el triple de lo que de
    verdad necesita. `malloc_trim` la devuelve de verdad. Se llama al terminar
    cada trabajo, que es justo cuando se acaba de liberar lo gordo.
    """
    import ctypes
    import gc

    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass    # en otros sistemas no existe; no pasa nada


class Trabajo:
    """Un trabajo de la cola (una conversión, un envío al Kindle, una subida)."""

    def __init__(self, titulo, pasos=1, tipo="conversion"):
        self.id = uuid.uuid4().hex[:12]
        self.tipo = tipo
        self.titulo = titulo
        self.estado = "en cola"          # en cola | en marcha | hecho | fallado | cancelado
        self.pasos = max(1, pasos)
        self.paso = 0
        self.detalle = ""
        self.lineas = []
        self.creado = time.time()
        self.terminado = None
        self.resultados = []
        self.cancelar = False

    def anotar(self, texto):
        self.lineas.append(f"{datetime.now():%H:%M:%S} · {texto}")
        self.detalle = texto
        del self.lineas[:-200]

    @property
    def porcentaje(self):
        if self.estado in ("hecho", "fallado", "cancelado"):
            return 100
        return int(100 * self.paso / self.pasos)

    def como_dict(self):
        return {
            "id": self.id,
            "tipo": self.tipo,
            "titulo": self.titulo,
            "estado": self.estado,
            "paso": self.paso,
            "pasos": self.pasos,
            "porcentaje": self.porcentaje,
            "detalle": self.detalle,
            "lineas": self.lineas[-40:],
            "resultados": self.resultados,
            "segundos": int((self.terminado or time.time()) - self.creado),
        }


class ColaTrabajos:
    """Cola de uno en uno.

    De uno en uno a propósito: el NAS tiene 3,7 GB de memoria compartidos con
    el resto de aplicaciones, y dos conversiones a la vez de un libro gordo se
    la comen. Mejor lento que tirar el NAS.
    """

    def __init__(self, maximo_historial=40):
        self._trabajos = {}
        self._orden = []
        self._cola = []
        self._lock = threading.Lock()
        self._hilo = None
        self.maximo_historial = maximo_historial

    def encolar(self, trabajo, funcion):
        with self._lock:
            self._trabajos[trabajo.id] = trabajo
            self._orden.append(trabajo.id)
            self._cola.append((trabajo, funcion))
            sobra = self._orden[: -self.maximo_historial] if len(self._orden) > self.maximo_historial else []
            for viejo in sobra:
                if self._trabajos.get(viejo) and self._trabajos[viejo].estado in (
                    "hecho", "fallado", "cancelado"
                ):
                    self._trabajos.pop(viejo, None)
                    self._orden.remove(viejo)
            self._arrancar()
        return trabajo

    def _arrancar(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(target=self._trabajar, daemon=True, name="vidalibre-cola")
        self._hilo.start()

    def _trabajar(self):
        while True:
            with self._lock:
                if not self._cola:
                    return
                trabajo, funcion = self._cola.pop(0)
            if trabajo.cancelar:
                trabajo.estado = "cancelado"
                trabajo.terminado = time.time()
                continue
            trabajo.estado = "en marcha"
            try:
                funcion(trabajo)
                trabajo.estado = "cancelado" if trabajo.cancelar else (
                    "fallado" if trabajo.estado == "fallado" else "hecho"
                )
            except Exception as ex:
                trabajo.estado = "fallado"
                trabajo.anotar(f"Error: {ex}")
            finally:
                trabajo.terminado = time.time()
                soltar_memoria()

    def trabajo(self, id_trabajo):
        return self._trabajos.get(id_trabajo)

    def listar(self, limite=20):
        with self._lock:
            ids = list(reversed(self._orden))[:limite]
            return [self._trabajos[i].como_dict() for i in ids if i in self._trabajos]

    def activos(self):
        return [t for t in self._trabajos.values() if t.estado in ("en cola", "en marcha")]
