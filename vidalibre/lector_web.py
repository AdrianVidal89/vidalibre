"""Las pantallas del lector."""

from __future__ import annotations

import json
import time
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, jsonify, redirect,
                   render_template, request, send_file, url_for)

from . import drm, ficheros, lector, puede_escribir
from .lector import CacheLibros, ErrorLector

paginas_lector = Blueprint("lector", __name__)

# Los libros abiertos se guardan aquí (su índice, no su contenido).
_LIBROS = CacheLibros(maximo=3)


def _preparar_cache():
    if _LIBROS.carpeta_medidas is None:
        _LIBROS.carpeta_medidas = _ajustes().datos / "medidas"

FORMATOS_LEGIBLES = ("EPUB",)


def _ajustes():
    return current_app.config["AJUSTES"]


def _catalogo():
    return current_app.config["CATALOGO"]


def _libro_o_404(id_libro):
    datos = _catalogo().libro(id_libro)
    if not datos:
        abort(404)
    return datos


def _epub_de(datos):
    for formato in FORMATOS_LEGIBLES:
        if formato in datos["formatos"]:
            ruta = ficheros.ruta_fichero(_catalogo().raiz, datos, formato)
            if ruta and ruta.is_file():
                return formato, ruta
    return None, None


def _abrir(datos):
    _formato, ruta = _epub_de(datos)
    if not ruta:
        return None
    _preparar_cache()
    try:
        return _LIBROS.abrir(ruta)
    except ErrorLector:
        return None


@paginas_lector.route("/libro/<int:id_libro>/leer")
def leer(id_libro):
    datos = _libro_o_404(id_libro)
    formato, ruta = _epub_de(datos)

    if not ruta:
        # Sin EPUB no hay lectura cómoda. Un PDF se abre con el visor del
        # propio navegador, que para páginas fijas hace mejor papel que
        # cualquier cosa que montemos encima.
        if "PDF" in datos["formatos"]:
            return render_template("lector_pdf.html", libro=datos)
        return render_template("lector_preparar.html", libro=datos)

    _preparar_cache()
    candado = drm.estado(ruta)
    if candado["protegido"]:
        return render_template("lector_preparar.html", libro=datos,
                               error=drm.mensaje(candado, "abrir"), drm=True)

    try:
        libro = _LIBROS.abrir(ruta)
    except ErrorLector as ex:
        return render_template("lector_preparar.html", libro=datos, error=str(ex))

    posicion = lector.leer_posicion(_catalogo(), id_libro, formato) or {}
    _apuntar_apertura(id_libro)
    return render_template(
        "lector.html",
        libro=datos,
        formato=formato,
        epub=libro,
        indice=libro.indice,
        posicion=posicion,
        inicio=json.dumps({
            "id": id_libro,
            # La dirección de la que cuelga todo lo del lector. En el navegador
            # hay que construirlas con esta base: una ruta relativa desde
            # «/libro/118/leer» se resuelve como «/libro/118/...» y se pierde el
            # «leer» por el camino.
            "base": url_for("lector.leer", id_libro=id_libro),
            "formato": formato,
            "capitulos": len(libro.capitulos),
            "palabras": libro.palabras,
            "titulo": libro.titulo or datos["titulo"],
            "autor": libro.autor or ", ".join(datos["autores"]),
            "idioma": libro.idioma,
            "capitulo_inicial": request.args.get("cap", type=int),
            "posicion": posicion.get("posicion") or "",
            "fraccion": posicion.get("fraccion") or 0,
            "fracciones": [c.get("fraccion", 0) for c in libro.capitulos],
            "palabras_capitulo": [c.get("palabras", 0) for c in libro.capitulos],
            "indice": libro.indice,
        }, ensure_ascii=False),
    )


def _apuntar_apertura(id_libro):
    """El primer día que abres un libro en el lector. Con eso, si pasas del 5 %
    sin haberlo marcado como empezado, Lecturas te pregunta «¿lo has empezado?
    ¿desde el día tal?». No apunta nada por su cuenta: hojear no es empezar."""
    if _ajustes().servidor or _ajustes().solo_lectura:
        return
    try:
        current_app.config["LECTURAS"].apuntar_apertura(id_libro)
    except Exception:
        pass


@paginas_lector.route("/libro/<int:id_libro>/leer/pagina/<int:numero>")
def pagina(id_libro, numero):
    """El capítulo, como documento completo: es lo que se mete en el marco.

    Va en un marco (iframe) del mismo sitio a propósito: así el libro puede
    traer sus propios estilos sin estropear los de la aplicación, y el lector
    sigue pudiendo medirlo y pasarle páginas porque es del mismo origen.
    """
    datos = _libro_o_404(id_libro)
    libro = _abrir(datos)
    if not libro:
        abort(404)

    def url_recurso(ruta):
        return url_for("lector.recurso", id_libro=id_libro, ruta=ruta)

    try:
        capitulo = libro.capitulo(numero, url_recurso)
    except ErrorLector:
        abort(404)

    respuesta = Response(render_template("lector_pagina.html", cap=capitulo, libro=datos),
                         mimetype="text/html")
    respuesta.headers["Cache-Control"] = "private, max-age=600"
    return respuesta


@paginas_lector.route("/libro/<int:id_libro>/leer/recurso/<path:ruta>")
def recurso(id_libro, ruta):
    datos = _libro_o_404(id_libro)
    libro = _abrir(datos)
    if not libro:
        abort(404)
    try:
        contenido, tipo = libro.recurso(ruta)
    except ErrorLector:
        abort(404)
    if tipo == "text/css":
        prefijo = url_for("lector.recurso", id_libro=id_libro, ruta="")
        contenido = contenido.replace(b"__RECURSO__", prefijo.encode("utf-8"))
    respuesta = Response(contenido, mimetype=tipo)
    respuesta.headers["Cache-Control"] = "private, max-age=86400"
    return respuesta


@paginas_lector.route("/libro/<int:id_libro>/leer/posicion", methods=["POST"])
def posicion(id_libro):
    datos = _libro_o_404(id_libro)
    formato, ruta = _epub_de(datos)
    if not ruta or _ajustes().solo_lectura:
        return jsonify({"guardado": False})
    cuerpo = request.get_json(silent=True) or {}
    posicion_texto = str(cuerpo.get("posicion") or "")[:400]
    fraccion = cuerpo.get("fraccion") or 0

    if puede_escribir(_ajustes(), _catalogo()):
        lector.guardar_posicion(_catalogo(), id_libro, formato, posicion_texto, fraccion)
        return jsonify({"guardado": True})

    # Biblioteca en carpeta de red: que lo apunte el NAS.
    puente = current_app.config["PUENTE"]
    if not puente:
        return jsonify({"guardado": False, "motivo": "sin servidor"})
    try:
        puente.guardar_posicion(id_libro, formato, posicion_texto, fraccion)
        return jsonify({"guardado": True, "por": "la biblioteca de casa"})
    except Exception as ex:
        return jsonify({"guardado": False, "motivo": str(ex)})


@paginas_lector.route("/libro/<int:id_libro>/leer/buscar")
def buscar(id_libro):
    datos = _libro_o_404(id_libro)
    libro = _abrir(datos)
    if not libro:
        abort(404)
    return jsonify({"resultados": libro.buscar(request.args.get("q", ""))})


@paginas_lector.route("/libro/<int:id_libro>/leer/preparar", methods=["POST"])
def preparar(id_libro):
    """Convierte a EPUB un libro que no lo tiene, para poder leerlo."""
    from .motor import Trabajo
    from .web import encolar

    datos = _libro_o_404(id_libro)
    if _ajustes().solo_lectura:
        abort(403)
    biblioteca = current_app.config["BIBLIOTECA"]
    trabajo = Trabajo(f"Preparar para leer: {datos['titulo']}", tipo="conversion")

    def tarea(trabajo):
        trabajo.paso = 1
        ok, _ruta, mensaje = biblioteca.convertir(id_libro, "epub", trabajo=trabajo)
        if not ok:
            trabajo.estado = "fallado"
            trabajo.anotar(mensaje)
            return
        trabajo.resultados.append({"texto": f"{datos['titulo']} listo para leer",
                                   "libro": id_libro})

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))
