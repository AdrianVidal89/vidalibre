"""Las pantallas de Vidalibre."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, jsonify,
                   redirect, render_template, request, send_file, url_for)

from . import NOMBRE, __version__, dispositivo, ficheros, motor
from .catalogo import ErrorCatalogo
from .motor import Trabajo

paginas = Blueprint("paginas", __name__)

CATEGORIAS = {
    "autores": ("Autores", "autor"),
    "series": ("Series", "serie"),
    "etiquetas": ("Etiquetas", "etiqueta"),
    "editoriales": ("Editoriales", "editorial"),
    "idiomas": ("Idiomas", "idioma"),
}

IDIOMAS = {
    "spa": "Español", "eng": "Inglés", "fra": "Francés", "deu": "Alemán",
    "ita": "Italiano", "por": "Portugués", "cat": "Catalán", "glg": "Gallego",
    "eus": "Euskera", "lat": "Latín", "rus": "Ruso", "jpn": "Japonés",
}


# --------------------------------------------------------------------- ayudas
def ajustes():
    return current_app.config["AJUSTES"]


def catalogo():
    return current_app.config["CATALOGO"]


def biblioteca():
    return current_app.config["BIBLIOTECA"]


def cola():
    return current_app.config["COLA"]


def encolar(trabajo, tarea):
    """Mete un trabajo en la cola.

    La cola corre en un hilo aparte, fuera de la petición web, y allí no existe
    el «contexto de aplicación» de Flask: sin esto, cualquier trabajo se cae
    nada más empezar al pedir la configuración.
    """
    aplicacion = current_app._get_current_object()

    def envuelta(trabajo):
        with aplicacion.app_context():
            tarea(trabajo)

    return cola().encolar(trabajo, envuelta)


def comprobar_escritura():
    if ajustes().solo_lectura:
        abort(403, "Vidalibre está en modo de solo lectura.")


def lista_de(texto, separador=","):
    if not texto:
        return []
    return [t.strip() for t in texto.split(separador) if t.strip()]


def fecha_calibre(texto):
    """De un <input type=date> al formato de fecha que guarda Calibre."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        dia = datetime.strptime(texto[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return dia.strftime("%Y-%m-%d 00:00:00+00:00")


def filtros_de_la_url():
    return {
        "busqueda": request.args.get("q", "").strip() or None,
        "autor": request.args.get("autor") or None,
        "serie": request.args.get("serie") or None,
        "etiqueta": request.args.get("etiqueta") or None,
        "idioma": request.args.get("idioma") or None,
        "editorial": request.args.get("editorial") or None,
        "formato": request.args.get("formato") or None,
        "sin_formato": request.args.get("sin_formato") or None,
    }


@paginas.app_template_filter("tamano")
def _filtro_tamano(valor):
    return ficheros.tamano_legible(valor)


@paginas.app_template_filter("fecha")
def _filtro_fecha(valor, formato="%d/%m/%Y"):
    if not valor:
        return ""
    texto = str(valor)
    for patron in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
                   "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:26] if "." in texto else texto[:19], patron).strftime(formato)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).strftime(formato)
    except ValueError:
        return texto[:10]


@paginas.app_template_filter("idioma")
def _filtro_idioma(codigo):
    return IDIOMAS.get((codigo or "").lower(), (codigo or "").upper())


@paginas.app_template_global("url_sin")
def _url_sin(clave):
    """La misma URL pero sin uno de los filtros (para la × de los chips)."""
    args = request.args.to_dict()
    args.pop(clave, None)
    args.pop("pagina", None)
    return url_for("paginas.inicio", **args)


@paginas.app_template_filter("html_seguro")
def _filtro_html_seguro(texto):
    """Las descripciones de Calibre traen HTML. Se deja el formato y se quita
    todo lo que pueda ejecutar algo."""
    from markupsafe import Markup
    import re as _re

    texto = texto or ""
    texto = _re.sub(r"(?is)<(script|style|iframe|object|embed)[^>]*>.*?</\1>", "", texto)
    texto = _re.sub(r"(?is)</?(script|style|iframe|object|embed|link|meta)[^>]*>", "", texto)
    texto = _re.sub(r'(?is)\son\w+\s*=\s*"[^"]*"', "", texto)
    texto = _re.sub(r"(?is)\son\w+\s*=\s*'[^']*'", "", texto)
    texto = _re.sub(r"(?is)javascript:", "", texto)
    return Markup(texto)


@paginas.context_processor
def _contexto():
    return {
        "ajustes": ajustes(),
        "nombre_app": NOMBRE,
        "version": __version__,
        "categorias": CATEGORIAS,
        "formatos_salida": ficheros.FORMATOS_SALIDA,
        "trabajos_activos": len(cola().activos()),
    }


@paginas.errorhandler(ErrorCatalogo)
def _error_catalogo(ex):
    flash(str(ex), "error")
    return redirect(request.referrer or url_for("paginas.inicio"))


# ----------------------------------------------------------------- biblioteca
@paginas.route("/")
def inicio():
    filtros = filtros_de_la_url()
    orden = request.args.get("orden", "reciente")
    pagina = max(1, request.args.get("pagina", 1, type=int))
    por_pagina = ajustes().por_pagina
    try:
        libros, total = catalogo().libros(
            orden=orden, pagina=pagina, por_pagina=por_pagina, **filtros
        )
    except ErrorCatalogo as ex:
        return render_template("sin_biblioteca.html", error=str(ex)), 503

    paginas_totales = max(1, -(-total // por_pagina))
    etiquetas_filtro = {
        "busqueda": ("Buscando", "q"), "autor": ("Autor", "autor"),
        "serie": ("Serie", "serie"), "etiqueta": ("Etiqueta", "etiqueta"),
        "idioma": ("Idioma", "idioma"), "editorial": ("Editorial", "editorial"),
        "formato": ("Formato", "formato"), "sin_formato": ("Sin", "sin_formato"),
    }
    chips = [
        {"texto": f"{etiquetas_filtro[clave][0]}: {valor}",
         "url": _url_sin(etiquetas_filtro[clave][1])}
        for clave, valor in filtros.items() if valor
    ]
    return render_template(
        "biblioteca.html",
        chips=chips,
        libros=libros,
        total=total,
        pagina=pagina,
        paginas_totales=paginas_totales,
        orden=orden,
        filtros={k: v for k, v in filtros.items() if v},
        resumen=catalogo().resumen(),
        formatos=catalogo().formatos(),
    )


@paginas.route("/estantes/<tipo>")
def estantes(tipo):
    if tipo not in CATEGORIAS:
        abort(404)
    elementos = catalogo().categoria(tipo)
    if tipo == "series":
        elementos.sort(key=lambda e: e["nombre"].lower())
    else:
        elementos.sort(key=lambda e: (-e["libros"], e["nombre"].lower()))
    return render_template(
        "estantes.html",
        tipo=tipo,
        titulo=CATEGORIAS[tipo][0],
        parametro=CATEGORIAS[tipo][1],
        elementos=elementos,
    )


@paginas.route("/libro/<int:id_libro>")
def libro(id_libro):
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    hermanos = []
    if datos["serie"]:
        hermanos, _ = catalogo().libros(serie=datos["serie"], orden="autor", por_pagina=30)
        hermanos = [h for h in hermanos if h["id"] != id_libro]
    return render_template(
        "libro.html",
        libro=datos,
        hermanos=hermanos,
        hay_motor=motor.hay_motor(),
        kindles=dispositivo.detectar() if ajustes().con_dispositivo else [],
        en_disco={
            f["formato"]: bool(
                (ruta := ficheros.ruta_fichero(catalogo().raiz, datos, f["formato"]))
                and ruta.is_file()
            )
            for f in datos["ficheros"]
        },
    )


@paginas.route("/libro/<int:id_libro>/portada")
def portada(id_libro):
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    ruta = ficheros.ruta_portada(catalogo().raiz, datos)
    if not ruta:
        return _portada_generada(datos)
    if request.args.get("mini"):
        mini = _miniatura(datos, ruta)
        if mini:
            ruta = mini
    respuesta = send_file(str(ruta), conditional=True)
    respuesta.headers["Cache-Control"] = "private, max-age=86400"
    return respuesta


def _miniatura(datos, ruta_portada, ancho=320):
    """Miniatura cacheada: el NAS no está para reescalar 48 portadas por página."""
    carpeta = ajustes().miniaturas
    marca = int(ruta_portada.stat().st_mtime)
    destino = carpeta / f"{datos['id']}_{marca}_{ancho}.jpg"
    if destino.is_file():
        return destino
    try:
        from PIL import Image

        carpeta.mkdir(parents=True, exist_ok=True)
        imagen = Image.open(str(ruta_portada))
        if imagen.mode not in ("RGB", "L"):
            imagen = imagen.convert("RGB")
        if imagen.width > ancho:
            alto = int(imagen.height * ancho / imagen.width)
            imagen = imagen.resize((ancho, alto), Image.LANCZOS)
        imagen.save(str(destino), "JPEG", quality=82, optimize=True)
        # Las miniaturas viejas del mismo libro ya no sirven.
        for vieja in carpeta.glob(f"{datos['id']}_*"):
            if vieja != destino:
                vieja.unlink(missing_ok=True)
        return destino
    except Exception:
        return None


def _portada_generada(datos):
    """Portada de pega para los libros que no traen imagen: iniciales y color."""
    titulo = (datos["titulo"] or "?").strip()
    inicial = titulo[0].upper()
    tono = (datos["id"] * 47) % 360
    autor = (datos["autores"] or [""])[0]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 450">
  <rect width="300" height="450" fill="hsl({tono},26%,24%)"/>
  <rect x="0" y="0" width="14" height="450" fill="hsl({tono},30%,16%)"/>
  <text x="160" y="215" font-family="Georgia,serif" font-size="150" fill="hsl({tono},30%,70%)"
        text-anchor="middle">{inicial}</text>
  <text x="160" y="280" font-family="system-ui,sans-serif" font-size="17" fill="hsl({tono},20%,80%)"
        text-anchor="middle">{_corto(titulo, 26)}</text>
  <text x="160" y="308" font-family="system-ui,sans-serif" font-size="14" fill="hsl({tono},15%,65%)"
        text-anchor="middle">{_corto(autor, 30)}</text>
</svg>"""
    return Response(svg, mimetype="image/svg+xml",
                    headers={"Cache-Control": "private, max-age=3600"})


def _corto(texto, limite):
    from xml.sax.saxutils import escape

    texto = (texto or "").strip()
    return escape(texto if len(texto) <= limite else texto[: limite - 1] + "…")


@paginas.route("/libro/<int:id_libro>/descargar/<formato>")
def descargar(id_libro, formato):
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    ruta = ficheros.ruta_fichero(catalogo().raiz, datos, formato)
    if not ruta or not ruta.is_file():
        abort(404, "Ese fichero no está en el disco.")
    autor = (datos["autores"] or ["Autor desconocido"])[0]
    nombre = f"{ficheros.nombre_valido(datos['titulo'], chars=120)} - {autor}.{formato.lower()}"
    return send_file(str(ruta), as_attachment=True, download_name=nombre)


# -------------------------------------------------------------------- edición
@paginas.route("/libro/<int:id_libro>/editar", methods=["GET", "POST"])
def editar(id_libro):
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    if request.method == "GET":
        return render_template("editar.html", libro=datos)

    comprobar_escritura()
    formulario = request.form
    identificadores = {}
    for linea in lista_de(formulario.get("identificadores", ""), "\n"):
        if ":" in linea:
            tipo, valor = linea.split(":", 1)
            identificadores[tipo.strip().lower()] = valor.strip()
    if formulario.get("isbn", "").strip():
        identificadores["isbn"] = formulario["isbn"].strip()

    cambios = {
        "titulo": formulario.get("titulo", "").strip(),
        "autores": lista_de(formulario.get("autores", ""), "&"),
        "serie": formulario.get("serie", "").strip(),
        "indice_serie": formulario.get("indice_serie") or 1,
        "editorial": formulario.get("editorial", "").strip(),
        "etiquetas": lista_de(formulario.get("etiquetas", "")),
        "idiomas": [i.lower() for i in lista_de(formulario.get("idiomas", ""))],
        "descripcion": formulario.get("descripcion", "").strip(),
        "valoracion": int(formulario.get("valoracion") or 0),
        "identificadores": identificadores,
    }
    publicado = fecha_calibre(formulario.get("publicado"))
    if publicado:
        cambios["publicado"] = publicado

    imagen = None
    subida = request.files.get("portada")
    if subida and subida.filename:
        imagen = subida.read()

    biblioteca().guardar_edicion(
        id_libro,
        cambios,
        portada=imagen,
        quitar_portada=bool(formulario.get("quitar_portada")),
        incrustar=bool(formulario.get("incrustar")),
    )
    flash("Ficha guardada.", "ok")
    return redirect(url_for("paginas.libro", id_libro=id_libro))


@paginas.route("/libro/<int:id_libro>/accion/<nombre>", methods=["POST"])
def accion(id_libro, nombre):
    comprobar_escritura()
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)

    if nombre == "portada-del-fichero":
        if biblioteca().refrescar_portada_desde_fichero(id_libro):
            flash("Portada sacada del propio libro.", "ok")
        else:
            flash("Ese libro no trae imagen de cubierta dentro.", "aviso")

    elif nombre == "releer-metadatos":
        leidos = biblioteca().releer_metadatos_del_fichero(id_libro)
        if not leidos:
            flash("No he podido leer los metadatos del fichero.", "aviso")
        else:
            return render_template("editar.html", libro={**datos, **{
                k: v for k, v in leidos.items() if v
            }}, aviso="Estos son los datos que trae el fichero dentro. Revísalos y guarda.")

    elif nombre == "incrustar":
        biblioteca().guardar_edicion(id_libro, {}, incrustar=True)
        flash("Metadatos escritos dentro de los ficheros del libro.", "ok")

    elif nombre == "borrar":
        titulo = biblioteca().borrar_libro(id_libro)
        flash(f"Borrado: {titulo}", "ok")
        return redirect(url_for("paginas.inicio"))

    elif nombre == "borrar-formato":
        formato = request.form.get("formato", "")
        biblioteca().borrar_formato(id_libro, formato)
        flash(f"Quitado el {formato.upper()}.", "ok")

    elif nombre == "anadir-formato":
        subida = request.files.get("fichero")
        if not subida or not subida.filename:
            flash("No has elegido ningún fichero.", "aviso")
        else:
            with tempfile.TemporaryDirectory() as tmp:
                ruta, extension = ficheros.recibir_subida(subida, Path(tmp))
                biblioteca().anadir_formato(id_libro, ruta, extension, mover=True)
            flash(f"Añadido el {extension.upper()} a este libro.", "ok")
    else:
        abort(404)

    return redirect(url_for("paginas.libro", id_libro=id_libro))


# --------------------------------------------------------------------- subida
@paginas.route("/subir", methods=["GET", "POST"])
def subir():
    if request.method == "GET":
        return render_template("subir.html")

    comprobar_escritura()
    subidos = [f for f in request.files.getlist("libros") if f and f.filename]
    if not subidos:
        flash("No has elegido ningún libro.", "aviso")
        return redirect(url_for("paginas.subir"))

    nuevos, fallos, avisos = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        for subida in subidos:
            try:
                ruta, extension = ficheros.recibir_subida(subida, Path(tmp))
            except ValueError as ex:
                fallos.append(f"{subida.filename}: {ex}")
                continue
            try:
                id_libro, mensajes = biblioteca().anadir_libro(ruta, extension, mover=True)
                datos = catalogo().libro(id_libro)
                nuevos.append(datos)
                avisos += [f"{datos['titulo']}: {m}" for m in mensajes]
                repetidos = biblioteca().posibles_duplicados(datos["titulo"], datos["autores"])
                repetidos = [r for r in repetidos if r["id"] != id_libro]
                if repetidos:
                    avisos.append(
                        f"«{datos['titulo']}» se parece a {len(repetidos)} libro(s) que ya tenías."
                    )
            except Exception as ex:
                fallos.append(f"{subida.filename}: {ex}")

    for aviso in avisos:
        flash(aviso, "aviso")
    for fallo in fallos:
        flash(fallo, "error")
    if nuevos:
        flash(f"{len(nuevos)} libro(s) en la biblioteca.", "ok")
        if len(nuevos) == 1:
            return redirect(url_for("paginas.libro", id_libro=nuevos[0]["id"]))
    return redirect(url_for("paginas.inicio", orden="reciente"))


# ----------------------------------------------------------------- conversión
def _destino_de_conversion(formulario):
    destino = formulario.get("destino", "biblioteca")
    carpeta = None
    if destino == "carpeta":
        carpeta = (formulario.get("carpeta") or ajustes().carpeta_descargas or "").strip()
        if not carpeta:
            raise ErrorCatalogo("Dime en qué carpeta dejo los libros convertidos.")
    return destino, carpeta


@paginas.route("/libro/<int:id_libro>/convertir", methods=["POST"])
def convertir(id_libro):
    comprobar_escritura()
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    formato = request.form.get("formato", "epub").lower()
    destino, carpeta = _destino_de_conversion(request.form)
    origen = request.form.get("origen") or None

    trabajo = Trabajo(f"{datos['titulo']} → {formato.upper()}", pasos=1)

    def tarea(trabajo):
        trabajo.paso = 1
        ok, ruta, mensaje = biblioteca().convertir(
            id_libro, formato, trabajo=trabajo,
            guardar=(destino in ("biblioteca", "kindle")),
            carpeta_salida=carpeta, origen_formato=origen,
        )
        if not ok:
            trabajo.estado = "fallado"
            trabajo.anotar(mensaje)
            return
        if destino == "descarga" and ruta:
            guardado = _guardar_para_descargar(trabajo, ruta)
            trabajo.resultados.append({"texto": guardado.name, "descarga": guardado.name})
        else:
            trabajo.resultados.append({"texto": f"{datos['titulo']} → {formato.upper()}",
                                       "libro": id_libro})
        trabajo.anotar("Listo.")

    encolar(trabajo, tarea)
    return _respuesta_trabajo(trabajo, url_for("paginas.libro", id_libro=id_libro))


def _guardar_para_descargar(trabajo, ruta):
    carpeta = ajustes().temporales / trabajo.id
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / Path(ruta).name
    shutil.move(str(ruta), str(destino))
    return destino


def _respuesta_trabajo(trabajo, vuelta):
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"trabajo": trabajo.como_dict(), "vuelta": vuelta})
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


@paginas.route("/convertir", methods=["GET", "POST"])
def convertir_en_bloque():
    if request.method == "GET":
        filtros = filtros_de_la_url()
        libros, total = catalogo().libros(por_pagina=1, **filtros)
        return render_template(
            "convertir.html",
            total=total,
            filtros={k: v for k, v in filtros.items() if v},
            formatos=catalogo().formatos(),
            categorias_autor=catalogo().categoria("autores"),
            categorias_serie=catalogo().categoria("series"),
            categorias_etiqueta=catalogo().categoria("etiquetas"),
        )

    comprobar_escritura()
    formato = request.form.get("formato", "epub").lower()
    destino, carpeta = _destino_de_conversion(request.form)
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    if not ids:
        filtros = {
            "busqueda": request.form.get("q") or None,
            "autor": request.form.get("autor") or None,
            "serie": request.form.get("serie") or None,
            "etiqueta": request.form.get("etiqueta") or None,
            "formato": request.form.get("origen_formato") or None,
        }
        if request.form.get("solo_sin_formato"):
            filtros["sin_formato"] = formato
        ids = catalogo().ids_filtrados(**filtros)
    if not ids:
        flash("Ese filtro no deja ningún libro que convertir.", "aviso")
        return redirect(url_for("paginas.convertir_en_bloque"))

    trabajo = Trabajo(f"{len(ids)} libros → {formato.upper()}", pasos=len(ids))

    def tarea(trabajo):
        bien = fallidos = 0
        for indice, id_libro in enumerate(ids, start=1):
            if trabajo.cancelar:
                break
            trabajo.paso = indice
            datos = catalogo().libro(id_libro)
            if not datos:
                continue
            trabajo.anotar(f"({indice}/{len(ids)}) {datos['titulo']}")
            try:
                ok, ruta, mensaje = biblioteca().convertir(
                    id_libro, formato, trabajo=trabajo,
                    guardar=(destino == "biblioteca"), carpeta_salida=carpeta,
                )
            except Exception as ex:
                ok, mensaje = False, str(ex)
            if ok:
                bien += 1
                trabajo.resultados.append({"texto": datos["titulo"], "libro": id_libro})
            else:
                fallidos += 1
                trabajo.anotar(f"Falló: {datos['titulo']} — {mensaje[:120]}")
        trabajo.anotar(f"Terminado: {bien} bien, {fallidos} con problemas.")
        if bien == 0 and fallidos:
            trabajo.estado = "fallado"

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


# ------------------------------------------------------------------- trabajos
@paginas.route("/trabajos")
def trabajos():
    return render_template(
        "trabajos.html",
        trabajos=cola().listar(),
        destacado=request.args.get("destacado", ""),
    )


@paginas.route("/trabajos.json")
def trabajos_json():
    return jsonify({"trabajos": cola().listar()})


@paginas.route("/trabajos/<id_trabajo>.json")
def trabajo_json(id_trabajo):
    trabajo = cola().trabajo(id_trabajo)
    if not trabajo:
        abort(404)
    return jsonify(trabajo.como_dict())


@paginas.route("/trabajos/<id_trabajo>/cancelar", methods=["POST"])
def cancelar_trabajo(id_trabajo):
    trabajo = cola().trabajo(id_trabajo)
    if trabajo:
        trabajo.cancelar = True
        trabajo.anotar("Cancelando…")
    return redirect(url_for("paginas.trabajos"))


@paginas.route("/trabajos/<id_trabajo>/descargar/<nombre>")
def descargar_resultado(id_trabajo, nombre):
    ruta = ajustes().temporales / id_trabajo / os.path.basename(nombre)
    if not ruta.is_file():
        abort(404, "Ese fichero ya no está: los convertidos sueltos se borran solos.")
    return send_file(str(ruta), as_attachment=True, download_name=ruta.name)


# ----------------------------------------------------------------- dispositivo
@paginas.route("/dispositivo")
def aparato():
    if not ajustes().con_dispositivo:
        return render_template("dispositivo_no.html")
    kindles = dispositivo.detectar()
    datos = [dispositivo.info(k) for k in kindles]
    libros_dentro = dispositivo.libros(kindles[0]) if kindles else []
    return render_template(
        "dispositivo.html",
        kindles=datos,
        libros_dentro=libros_dentro,
        ultimos=catalogo().libros(orden="reciente", por_pagina=24)[0],
    )


@paginas.route("/dispositivo/enviar", methods=["POST"])
def enviar_al_aparato():
    comprobar_escritura()
    if not ajustes().con_dispositivo:
        abort(403)
    kindle = request.form.get("kindle") or ""
    if not dispositivo.es_kindle(Path(kindle)):
        flash("No veo ese Kindle conectado.", "error")
        return redirect(url_for("paginas.aparato"))
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    formato = request.form.get("formato", "azw3").lower()
    if not ids:
        flash("No has elegido ningún libro.", "aviso")
        return redirect(url_for("paginas.aparato"))

    trabajo = Trabajo(f"{len(ids)} libro(s) al Kindle", pasos=len(ids), tipo="kindle")

    def tarea(trabajo):
        for indice, id_libro in enumerate(ids, start=1):
            if trabajo.cancelar:
                break
            trabajo.paso = indice
            datos = catalogo().libro(id_libro)
            if not datos:
                continue
            trabajo.anotar(f"({indice}/{len(ids)}) {datos['titulo']}")
            try:
                _formato, ruta, convertido = biblioteca().asegurar_formato(
                    id_libro, [formato] + [f for f in dispositivo.FORMATOS_USB if f != formato],
                    trabajo=trabajo,
                )
                origen_formato, origen_ruta = biblioteca().mejor_origen(datos)
                destino, con_portada = dispositivo.copiar_al_aparato(
                    ruta, kindle, origen=origen_ruta
                )
                trabajo.resultados.append({
                    "texto": f"{datos['titulo']} ({_formato})"
                             + ("" if con_portada else " — sin portada"),
                    "libro": id_libro,
                })
                if convertido:
                    trabajo.anotar(f"Convertido y guardado también en la biblioteca: {_formato}")
            except Exception as ex:
                trabajo.anotar(f"Falló {datos['titulo']}: {ex}")
        trabajo.anotar("Expulsa el Kindle antes de desenchufarlo.")

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


@paginas.route("/dispositivo/portadas", methods=["POST"])
def portadas_del_aparato():
    comprobar_escritura()
    if not ajustes().con_dispositivo:
        abort(403)
    kindle = request.form.get("kindle") or ""
    if not dispositivo.es_kindle(Path(kindle)):
        flash("No veo ese Kindle conectado.", "error")
        return redirect(url_for("paginas.aparato"))

    trabajo = Trabajo("Reponer portadas del Kindle", tipo="kindle")

    def tarea(trabajo):
        hechas, fallidas = dispositivo.reparar_portadas(kindle, trabajo)
        trabajo.anotar(f"Portadas repuestas: {len(hechas)}. Sin portada: {len(fallidas)}.")
        trabajo.resultados = [{"texto": n} for n in hechas[:100]]

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


# --------------------------------------------------------------------- estado
@paginas.route("/estado")
def estado():
    try:
        resumen = catalogo().resumen()
        bien = True
    except ErrorCatalogo as ex:
        resumen, bien = {"error": str(ex)}, False
    return jsonify({
        "aplicacion": NOMBRE,
        "version": __version__,
        "modo": ajustes().modo,
        "biblioteca": str(ajustes().biblioteca),
        "motor": motor.version_calibre(),
        "trabajos": len(cola().activos()),
        "resumen": resumen,
        "bien": bien,
    }), (200 if bien else 503)


@paginas.route("/acerca")
def acerca():
    return render_template(
        "acerca.html",
        motor_version=motor.version_calibre(),
        resumen=catalogo().resumen(),
        espacio=ficheros.espacio_libre(catalogo().raiz),
    )
