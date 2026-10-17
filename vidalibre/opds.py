"""OPDS: el catálogo que leen las aplicaciones de lectura.

OPDS es un catálogo en formato Atom: KOReader, Moon+ Reader, Panels o Marvin se
conectan a una dirección y navegan la biblioteca y se descargan los libros
solos, sin pasar por el navegador.

Dos tipos de página, y confundirlos es el fallo clásico que hace que la app de
lectura no muestre nada:
  - **navegación**: lista de secciones (autores, series...). 
  - **adquisición**: lista de libros con sus enlaces de descarga.
Cada una lleva su propio tipo de contenido, y es lo que mira el lector.
"""

from __future__ import annotations

from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

from flask import Blueprint, Response, current_app, request, url_for

from . import NOMBRE, __version__, ficheros

catalogo_opds = Blueprint("opds", __name__, url_prefix="/opds")

TIPO_NAVEGACION = "application/atom+xml;profile=opds-catalog;kind=navigation"
TIPO_ADQUISICION = "application/atom+xml;profile=opds-catalog;kind=acquisition"
TIPOS_FICHERO = {
    "EPUB": "application/epub+zip",
    "MOBI": "application/x-mobipocket-ebook",
    "AZW3": "application/vnd.amazon.ebook",
    "AZW": "application/vnd.amazon.ebook",
    "PDF": "application/pdf",
    "TXT": "text/plain",
    "FB2": "application/x-fictionbook+xml",
    "CBZ": "application/x-cbz",
    "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _catalogo():
    return current_app.config["CATALOGO"]


def _ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _enlace(rel, href, tipo, titulo=None):
    extra = f" title={quoteattr(titulo)}" if titulo else ""
    return f'  <link rel="{rel}" href={quoteattr(href)} type={quoteattr(tipo)}{extra}/>'


def _feed(id_feed, titulo, entradas, tipo=TIPO_NAVEGACION, enlaces=None):
    cabecera = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:dc="http://purl.org/dc/terms/" '
        'xmlns:opds="http://opds-spec.org/2010/catalog">',
        f"  <id>{escape(id_feed)}</id>",
        f"  <title>{escape(titulo)}</title>",
        f"  <updated>{_ahora()}</updated>",
        f"  <author><name>{NOMBRE} {__version__}</name></author>",
        _enlace("self", request.url, tipo),
        _enlace("start", url_for("opds.raiz", _external=True), TIPO_NAVEGACION),
        _enlace("search", url_for("opds.buscador", _external=True),
                "application/opensearchdescription+xml", "Buscar en la biblioteca"),
    ]
    cabecera += enlaces or []
    return Response(
        "\n".join(cabecera + entradas + ["</feed>"]),
        mimetype=tipo,
        headers={"Cache-Control": "no-cache"},
    )


def _entrada_navegacion(titulo, subtitulo, href, tipo=TIPO_NAVEGACION):
    return (
        "  <entry>\n"
        f"    <title>{escape(titulo)}</title>\n"
        f"    <id>{escape(href)}</id>\n"
        f"    <updated>{_ahora()}</updated>\n"
        f"    <content type=\"text\">{escape(subtitulo or '')}</content>\n"
        f"    <link rel=\"subsection\" href={quoteattr(href)} type={quoteattr(tipo)}/>\n"
        "  </entry>"
    )


def _entrada_libro(libro):
    partes = [
        "  <entry>",
        f"    <title>{escape(libro['titulo'])}</title>",
        f"    <id>urn:uuid:{escape(libro.get('uuid') or str(libro['id']))}</id>",
        f"    <updated>{_ahora()}</updated>",
    ]
    for autor in libro["autores"]:
        partes.append(f"    <author><name>{escape(autor)}</name></author>")
    if libro.get("editorial"):
        partes.append(f"    <dc:publisher>{escape(libro['editorial'])}</dc:publisher>")
    for idioma in libro.get("idiomas") or []:
        partes.append(f"    <dc:language>{escape(idioma)}</dc:language>")
    for etiqueta in libro.get("etiquetas") or []:
        partes.append(f"    <category term={quoteattr(etiqueta)} label={quoteattr(etiqueta)}/>")
    resumen = libro.get("descripcion") or ""
    if libro.get("serie"):
        indice = libro.get("indice_serie") or 1
        resumen = f"{libro['serie']} nº {indice:g}\n\n{resumen}"
    if resumen:
        partes.append(f"    <summary type=\"text\">{escape(resumen[:1200])}</summary>")

    portada = url_for("paginas.portada", id_libro=libro["id"], _external=True)
    partes.append(_enlace("http://opds-spec.org/image", portada, "image/jpeg").strip())
    partes.append(
        _enlace("http://opds-spec.org/image/thumbnail", portada + "?mini=1", "image/jpeg").strip()
    )
    for fichero in libro.get("ficheros", []):
        formato = fichero["formato"]
        partes.append(
            '    <link rel="http://opds-spec.org/acquisition" '
            f"href={quoteattr(url_for('paginas.descargar', id_libro=libro['id'], formato=formato.lower(), _external=True))} "
            f"type={quoteattr(TIPOS_FICHERO.get(formato, 'application/octet-stream'))} "
            f'length="{fichero["tamano"]}"/>'
        )
    partes.append("  </entry>")
    return "\n".join(partes)


def _libros_feed(titulo, id_feed, **filtros):
    pagina = max(1, request.args.get("pagina", 1, type=int))
    por_pagina = 60
    catalogo = _catalogo()
    libros, total = catalogo.libros(pagina=pagina, por_pagina=por_pagina, **filtros)
    completos = [catalogo.libro(l["id"]) for l in libros]
    enlaces = []
    if pagina * por_pagina < total:
        enlaces.append(_enlace("next", request.base_url + f"?pagina={pagina + 1}", TIPO_ADQUISICION))
    return _feed(
        id_feed, f"{titulo} ({total})",
        [_entrada_libro(l) for l in completos if l],
        tipo=TIPO_ADQUISICION, enlaces=enlaces,
    )


# ------------------------------------------------------------------- secciones
@catalogo_opds.route("")
@catalogo_opds.route("/")
def raiz():
    resumen = _catalogo().resumen()
    entradas = [
        _entrada_navegacion("Novedades", "Lo último que ha entrado",
                            url_for("opds.novedades", _external=True), TIPO_ADQUISICION),
        _entrada_navegacion("Todos los libros", f"{resumen['libros']} libros",
                            url_for("opds.todos", _external=True), TIPO_ADQUISICION),
        _entrada_navegacion("Autores", f"{resumen['autores']} autores",
                            url_for("opds.autores", _external=True)),
        _entrada_navegacion("Series", f"{resumen['series']} series",
                            url_for("opds.series", _external=True)),
        _entrada_navegacion("Etiquetas", f"{resumen['etiquetas']} etiquetas",
                            url_for("opds.etiquetas", _external=True)),
    ]
    return _feed("vidalibre:raiz", f"{NOMBRE} — biblioteca", entradas)


@catalogo_opds.route("/novedades")
def novedades():
    return _libros_feed("Novedades", "vidalibre:novedades", orden="reciente")


@catalogo_opds.route("/todos")
def todos():
    return _libros_feed("Todos los libros", "vidalibre:todos", orden="titulo")


def _seccion_categoria(tipo, ruta, titulo):
    elementos = _catalogo().categoria(tipo)
    elementos.sort(key=lambda e: e["nombre"].lower())
    entradas = [
        _entrada_navegacion(
            e["nombre"], f"{e['libros']} libro(s)",
            url_for(ruta, nombre=e["nombre"], _external=True), TIPO_ADQUISICION,
        )
        for e in elementos
    ]
    return _feed(f"vidalibre:{tipo}", titulo, entradas)


@catalogo_opds.route("/autores")
def autores():
    return _seccion_categoria("autores", "opds.autor", "Autores")


@catalogo_opds.route("/autor/<path:nombre>")
def autor(nombre):
    return _libros_feed(nombre, f"vidalibre:autor:{nombre}", autor=nombre, orden="titulo")


@catalogo_opds.route("/series")
def series():
    return _seccion_categoria("series", "opds.serie", "Series")


@catalogo_opds.route("/serie/<path:nombre>")
def serie(nombre):
    return _libros_feed(nombre, f"vidalibre:serie:{nombre}", serie=nombre, orden="autor")


@catalogo_opds.route("/etiquetas")
def etiquetas():
    return _seccion_categoria("etiquetas", "opds.etiqueta", "Etiquetas")


@catalogo_opds.route("/etiqueta/<path:nombre>")
def etiqueta(nombre):
    return _libros_feed(nombre, f"vidalibre:etiqueta:{nombre}", etiqueta=nombre, orden="titulo")


@catalogo_opds.route("/buscar")
def buscador():
    """Descripción OpenSearch: es lo que hace que salga la lupa en la app."""
    plantilla = url_for("opds.resultados", _external=True) + "?q={searchTerms}"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>{escape(NOMBRE)}</ShortName>
  <Description>Buscar en la biblioteca</Description>
  <InputEncoding>UTF-8</InputEncoding>
  <OutputEncoding>UTF-8</OutputEncoding>
  <Url type={quoteattr(TIPO_ADQUISICION)} template={quoteattr(plantilla)}/>
</OpenSearchDescription>"""
    return Response(xml, mimetype="application/opensearchdescription+xml")


@catalogo_opds.route("/resultados")
def resultados():
    consulta = request.args.get("q", "").strip()
    return _libros_feed(f"Búsqueda: {consulta}", f"vidalibre:buscar:{consulta}",
                        busqueda=consulta or None, orden="titulo")
