"""Buscar libros libres y traérselos a la biblioteca.

De dónde se baja y por qué solo de aquí:

- **Project Gutenberg** (gutenberg.org): 75.000 libros de dominio público, con
  bastante en español. Es el catálogo serio de libre acceso desde 1971 y tiene
  un canal OPDS oficial para buscar, que es el que se usa aquí.
- **Standard Ebooks** (standardebooks.org): los mismos clásicos de dominio
  público pero reeditados a mano, con una tipografía y una maquetación muy por
  encima de la media. Solo publican en abierto su lista de novedades, así que
  eso es lo que se ofrece: lo último que han sacado.

No hay más fuentes a propósito. Las bibliotecas que reparten libros con
derechos de autor en vigor quedan fuera: esto es una biblioteca personal, no un
descargador de material pirateado.

Para completar fichas (portada, año, descripción, ISBN) se usa **Open Library**,
el catálogo abierto de Internet Archive. Eso son datos, no libros.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

from . import __version__

AGENTE = f"Vidalibre/{__version__} (biblioteca personal)"
TIEMPO = 30

NS = {"a": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/terms/"}

GUTENBERG_BUSCAR = "https://www.gutenberg.org/ebooks/search.opds/?query="
GUTENBERG_LIBRO = "https://www.gutenberg.org/ebooks/{id}.opds"
GUTENBERG_PORTADA = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.cover.medium.jpg"
STANDARD_NOVEDADES = "https://standardebooks.org/feeds/atom/new-releases"
OPENLIBRARY_BUSCAR = "https://openlibrary.org/search.json"
OPENLIBRARY_PORTADA = "https://covers.openlibrary.org/b/id/{id}-L.jpg"
OPENLIBRARY_OBRA = "https://openlibrary.org{clave}.json"

IDIOMAS_GUTENBERG = {
    "": "", "es": "l.es", "en": "l.en", "fr": "l.fr", "de": "l.de",
    "it": "l.it", "pt": "l.pt",
}


class ErrorFuente(Exception):
    pass


def _traer(url, binario=False, limite=25 * 1024 * 1024):
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(peticion, timeout=TIEMPO) as respuesta:
            datos = respuesta.read(limite + 1)
    except Exception as ex:
        raise ErrorFuente(f"No he podido conectar con {urllib.parse.urlparse(url).netloc}: {ex}") from ex
    if len(datos) > limite:
        raise ErrorFuente("El fichero es enorme; me he parado por seguridad.")
    return datos if binario else datos.decode("utf-8", "replace")


# ------------------------------------------------------------- Gutenberg
def _id_gutenberg(texto):
    m = re.search(r"/ebooks/(\d+)", texto or "")
    return m.group(1) if m else None


def buscar_gutenberg(consulta, idioma="", maximo=30):
    consulta = (consulta or "").strip()
    if not consulta:
        return []
    filtro = IDIOMAS_GUTENBERG.get(idioma or "", "")
    termino = f"{consulta} {filtro}".strip()
    xml = _traer(GUTENBERG_BUSCAR + urllib.parse.quote(termino))
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as ex:
        raise ErrorFuente("Gutenberg ha contestado algo que no entiendo.") from ex

    resultados = []
    for entrada in raiz.findall("a:entry", NS):
        identificador = _id_gutenberg((entrada.findtext("a:id", "", NS) or ""))
        if not identificador:
            for enlace in entrada.findall("a:link", NS):
                identificador = _id_gutenberg(enlace.get("href", ""))
                if identificador:
                    break
        if not identificador:
            continue
        titulo = (entrada.findtext("a:title", "", NS) or "").strip()
        autor = (entrada.findtext("a:content", "", NS) or "").strip()
        idioma_libro = ""
        if titulo.endswith(")") and "(" in titulo:
            idioma_libro = titulo[titulo.rfind("(") + 1:-1]
            titulo = titulo[:titulo.rfind("(")].strip()
        resultados.append({
            "fuente": "gutenberg",
            "fuente_nombre": "Project Gutenberg",
            "id": identificador,
            "titulo": titulo,
            # Ojo con separar por " y ": en español está dentro de los nombres
            # ("José Ortega y Gasset"). Solo comas y puntos y coma.
            "autores": [a.strip() for a in re.split(r"[,;]", autor) if a.strip()][:3] if autor else [],
            "idioma": idioma_libro,
            "descripcion": "",
            "portada": GUTENBERG_PORTADA.format(id=identificador),
            "enlace": f"https://www.gutenberg.org/ebooks/{identificador}",
        })
        if len(resultados) >= maximo:
            break
    return resultados


def enlaces_gutenberg(identificador):
    """Los ficheros descargables de un libro concreto."""
    xml = _traer(GUTENBERG_LIBRO.format(id=identificador))
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as ex:
        raise ErrorFuente("Gutenberg ha contestado algo que no entiendo.") from ex
    formatos = {}
    for entrada in raiz.findall("a:entry", NS):
        for enlace in entrada.findall("a:link", NS):
            if "acquisition" not in (enlace.get("rel") or ""):
                continue
            tipo = (enlace.get("type") or "").lower()
            href = enlace.get("href") or ""
            if not href.startswith("http"):
                href = "https://www.gutenberg.org" + href
            if "epub" in tipo:
                # Hay dos EPUB: con imágenes y sin ellas. Mejor con imágenes.
                if "images" in href or "epub" not in formatos:
                    formatos["epub"] = href
            elif "mobipocket" in tipo or "x-kindle" in tipo:
                formatos.setdefault("mobi", href)
            elif "plain" in tipo:
                formatos.setdefault("txt", href)
            elif "pdf" in tipo:
                formatos.setdefault("pdf", href)
    if not formatos:
        # Red de seguridad: el patrón de siempre de las descargas directas.
        formatos["epub"] = (
            f"https://www.gutenberg.org/cache/epub/{identificador}/pg{identificador}-images.epub"
        )
    return formatos


# -------------------------------------------------------- Standard Ebooks
def novedades_standard(carpeta_cache=None, horas=12, maximo=60):
    """Lo último de Standard Ebooks, con la lista guardada unas horas."""
    xml = None
    cache = Path(carpeta_cache) / "standardebooks.atom" if carpeta_cache else None
    if cache and cache.is_file() and (time.time() - cache.stat().st_mtime) < horas * 3600:
        xml = cache.read_text(encoding="utf-8", errors="replace")
    if xml is None:
        xml = _traer(STANDARD_NOVEDADES)
        if cache:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(xml, encoding="utf-8")
            except OSError:
                pass
    try:
        raiz = ElementTree.fromstring(xml)
    except ElementTree.ParseError as ex:
        raise ErrorFuente("Standard Ebooks ha contestado algo que no entiendo.") from ex

    resultados = []
    for entrada in raiz.findall("a:entry", NS):
        titulo = (entrada.findtext("a:title", "", NS) or "").strip()
        autores = [a.strip() for a in
                   (n.findtext("a:name", "", NS) for n in entrada.findall("a:author", NS)) if a]
        epub = portada = pagina = ""
        for enlace in entrada.findall("a:link", NS):
            rel, tipo, href = enlace.get("rel"), enlace.get("type") or "", enlace.get("href") or ""
            if rel == "enclosure" and tipo == "application/epub+zip" and "advanced" not in href:
                epub = href
            elif rel == "alternate" and "xhtml" in tipo:
                pagina = href
            elif "image" in tipo and not portada:
                portada = href
        if not epub:
            continue
        resumen = (entrada.findtext("a:summary", "", NS) or
                   entrada.findtext("a:content", "", NS) or "")
        resumen = re.sub(r"<[^>]+>", " ", resumen)
        resultados.append({
            "fuente": "standard",
            "fuente_nombre": "Standard Ebooks",
            "id": pagina or epub,
            "titulo": titulo,
            "autores": autores,
            "idioma": "English",
            "descripcion": re.sub(r"\s+", " ", resumen).strip()[:400],
            "portada": portada,
            "enlace": pagina,
            "formatos": {"epub": epub},
        })
        if len(resultados) >= maximo:
            break
    return resultados


def buscar(consulta, idioma="", carpeta_cache=None, maximo=30):
    """Busca en las dos fuentes y junta los resultados.

    Si pidiendo un idioma salen cuatro cosas, se repite la búsqueda sin filtro:
    el fondo en español de Gutenberg es bastante más pequeño que el inglés, y
    es mejor enseñar lo que hay en otros idiomas que un «no hay nada».
    """
    resultados = []
    errores = []
    try:
        resultados += buscar_gutenberg(consulta, idioma, maximo)
        if idioma and len(resultados) < 4:
            vistos = {r["id"] for r in resultados}
            for otro in buscar_gutenberg(consulta, "", maximo):
                if otro["id"] not in vistos:
                    otro["otro_idioma"] = True
                    resultados.append(otro)
    except ErrorFuente as ex:
        errores.append(str(ex))
    if consulta:
        try:
            aguja = consulta.lower()
            for libro in novedades_standard(carpeta_cache):
                texto = (libro["titulo"] + " " + " ".join(libro["autores"])).lower()
                if aguja in texto:
                    resultados.insert(0, libro)
        except ErrorFuente as ex:
            errores.append(str(ex))
    return resultados, errores


def formatos_de(resultado):
    if resultado.get("formatos"):
        return resultado["formatos"]
    if resultado["fuente"] == "gutenberg":
        return enlaces_gutenberg(resultado["id"])
    return {}


def descargar(url, carpeta, nombre=None):
    """Se trae el fichero a una carpeta temporal."""
    datos = _traer(url, binario=True)
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    if not nombre:
        nombre = Path(urllib.parse.urlparse(url).path).name or "libro.epub"
    if not Path(nombre).suffix:
        nombre += ".epub"
    destino = carpeta / re.sub(r"[^\w\s.\-()]+", "_", nombre)[:120]
    destino.write_bytes(datos)
    return destino


# ------------------------------------------------------------ Open Library
def limpiar_titulo(titulo):
    """Quita del título el ruido que mete la tienda y que despista al buscador.

    Los libros comprados llegan con cosas como «El marciano (Spanish Edition)»
    o «Dune (Edición definitiva) [Nueva traducción]». Open Library busca por
    texto y con todo eso dentro no encuentra nada: probándolo, «El marciano
    (Spanish Edition)» daba cero resultados y «El marciano» a secas lo
    encontraba a la primera.
    """
    texto = re.sub(r"[\(\[][^)\]]*[\)\]]", " ", titulo or "")
    texto = re.split(r"\s+[-–—:]\s+", texto)[0]
    return re.sub(r"\s+", " ", texto).strip(" .,-–—:") or (titulo or "").strip()


def _candidato(documento):
    return {
        "titulo": documento.get("title", ""),
        "autores": documento.get("author_name", [])[:3],
        "publicado": documento.get("first_publish_year"),
        "editorial": (documento.get("publisher") or [""])[0],
        "idiomas": [i[:3] for i in (documento.get("language") or [])][:2],
        "etiquetas": (documento.get("subject") or [])[:8],
        "isbn": (documento.get("isbn") or [""])[0],
        "portada": OPENLIBRARY_PORTADA.format(id=documento["cover_i"])
                   if documento.get("cover_i") else "",
        "clave": documento.get("key", ""),
        "enlace": "https://openlibrary.org" + documento.get("key", ""),
        "sinopsis": "",
    }


def _buscar_openlibrary(consulta, maximo):
    parametros = urllib.parse.urlencode({
        "q": consulta,
        "limit": maximo,
        "fields": "title,author_name,first_publish_year,cover_i,isbn,subject,language,"
                  "publisher,key,first_sentence",
    })
    try:
        datos = json.loads(_traer(f"{OPENLIBRARY_BUSCAR}?{parametros}"))
    except (ErrorFuente, ValueError) as ex:
        raise ErrorFuente(f"Open Library no ha contestado: {ex}") from ex
    return [_candidato(d) for d in datos.get("docs", [])[:maximo]]


def sinopsis_de_obra(clave):
    """El resumen largo de un libro. No viene en la búsqueda: hay que pedirlo.

    La búsqueda de Open Library solo devuelve la primera frase. El resumen de
    verdad está en la ficha de la obra, que es otra petición distinta.
    """
    if not clave or not clave.startswith("/works/"):
        return ""
    try:
        datos = json.loads(_traer(OPENLIBRARY_OBRA.format(clave=clave)))
    except (ErrorFuente, ValueError):
        return ""
    descripcion = datos.get("description")
    if isinstance(descripcion, dict):
        descripcion = descripcion.get("value", "")
    texto = (descripcion or "").strip()
    # Open Library deja al final la coletilla de quién copió el texto.
    texto = re.split(r"\n\s*-{3,}|\n\s*\(\s*source", texto)[0].strip()
    return texto[:4000]


def buscar_ficha(titulo, autor="", maximo=5, con_sinopsis=True):
    """Candidatos de Open Library para completar una ficha.

    Si con el título tal cual no sale nada, se vuelve a probar con el título
    limpio de paréntesis y subtítulos: es lo que rescata los libros comprados,
    que llegan con el nombre lleno de añadidos.
    """
    titulo = (titulo or "").strip()
    autor = (autor or "").strip()
    if not titulo and not autor:
        return []

    intentos, vistos = [], set()
    for candidato_titulo in (titulo, limpiar_titulo(titulo)):
        consulta = " ".join(x for x in (candidato_titulo, autor) if x).strip()
        if consulta and consulta not in vistos:
            vistos.add(consulta)
            intentos.append(consulta)
    # Último recurso: el título limpio a secas, sin el autor.
    suelto = limpiar_titulo(titulo)
    if suelto and suelto not in vistos:
        intentos.append(suelto)

    salida, ultimo_error = [], None
    for consulta in intentos:
        try:
            salida = _buscar_openlibrary(consulta, maximo)
        except ErrorFuente as ex:
            ultimo_error = ex
            continue
        if salida:
            break
    if not salida and ultimo_error:
        raise ultimo_error

    if con_sinopsis:
        for candidato in salida:
            candidato["sinopsis"] = sinopsis_de_obra(candidato["clave"])
    return salida


def traer_portada(url):
    datos = _traer(url, binario=True, limite=12 * 1024 * 1024)
    if len(datos) < 2000:
        raise ErrorFuente("La portada que devuelve el catálogo está vacía.")
    return datos
