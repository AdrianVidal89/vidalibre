"""Las pantallas del registro de lecturas (pestaña «Lecturas»).

El registro vive en el NAS, junto a la biblioteca de casa. La copia del
portátil no tiene uno propio: si lo tuviera, habría dos diarios distintos y
ninguno sería el bueno. Desde allí estas pantallas llevan a la de casa.
"""

from __future__ import annotations

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, url_for)

from . import ficheros, fuentes, lector, puede_escribir
from .lecturas import (DECISIONES, ESTADOS, NOMBRE_RECUERDO, RECUERDOS, RITMOS,
                       SENSACIONES, NOMBRE_SENSACION, NOMBRE_RITMO, a_fecha,
                       estrellas_texto, hoy, misma_obra)

paginas_lecturas = Blueprint("lecturas", __name__)


# --------------------------------------------------------------------- ayudas
def _ajustes():
    return current_app.config["AJUSTES"]


def _catalogo():
    return current_app.config["CATALOGO"]


def registro():
    return current_app.config["LECTURAS"]


def registro_aqui():
    """¿Vive el registro en esta copia? Solo en la biblioteca de casa."""
    return not _ajustes().servidor


def _en_casa(ruta="/lecturas"):
    return (_ajustes().servidor or "") + ruta


def _exigir_registro():
    if not registro_aqui():
        flash("El diario de lecturas vive en la biblioteca de casa: ábrelo allí.", "aviso")
        abort(redirect(_en_casa()))
    if _ajustes().solo_lectura:
        abort(403, "Vidalibre está en modo de solo lectura.")


def _volver(defecto=None):
    destino = request.form.get("volver") or request.args.get("volver")
    if destino and destino.startswith("/") and not destino.startswith("//"):
        return redirect(destino)
    return redirect(defecto or url_for("lecturas.panel"))


def _obra_o_404(id_obra):
    obra = registro().obra(id_obra)
    if not obra:
        abort(404)
    return obra


def _lectura_o_404(id_lectura):
    lectura = registro().lectura(id_lectura)
    if not lectura:
        abort(404)
    return lectura


def url_obra(obra):
    """Una obra de la biblioteca se ve en su ficha de siempre; la de fuera, en la suya."""
    if obra.get("libro"):
        return url_for("paginas.libro", id_libro=obra["libro"])
    return url_for("lecturas.obra", id_obra=obra.get("obra") or obra["id"])


def portada_obra(obra, mini=True):
    if obra.get("libro"):
        from .web import url_portada

        datos = _catalogo().libro(obra["libro"])
        if datos:
            return url_portada(datos, mini=mini)
    return url_for("lecturas.portada", id_obra=obra.get("obra") or obra["id"])


def medir_palabras(obra):
    """Cuántas palabras tiene un libro de la biblioteca, contándolas en su EPUB.

    El lector ya las cuenta (y lo guarda en disco), así que casi siempre es
    instantáneo. Sin EPUB no hay forma fiable y se deja sin medir.
    """
    if obra.get("palabras") or not obra.get("libro"):
        return obra.get("palabras")
    datos = _catalogo().libro(obra["libro"])
    if not datos or "EPUB" not in datos["formatos"]:
        return None
    ruta = ficheros.ruta_fichero(_catalogo().raiz, datos, "EPUB")
    if not ruta or not ruta.is_file():
        return None
    try:
        libro = lector.LibroEpub(ruta, carpeta_medidas=_ajustes().datos / "medidas")
    except Exception:
        return None
    palabras = getattr(libro, "palabras", 0)
    if palabras and palabras > 1000:
        registro().fijar_palabras(obra["id"], palabras)
        return palabras
    return None


def _progreso_de(lecturas_en_curso):
    """{obra: fracción leída} de los libros en curso que están en la biblioteca."""
    ids = [l["libro"] for l in lecturas_en_curso if l.get("libro")]
    posiciones = lector.posiciones_de_libros(_catalogo(), ids) if ids else {}
    return {l["obra"]: posiciones.get(l["libro"], 0) for l in lecturas_en_curso if l.get("libro")}


def _sin_leer():
    """Libros de la biblioteca que no has leído ni has decidido soltar."""
    try:
        total = _catalogo().resumen()["libros"]
    except Exception:
        return 0
    hechos = {l["libro"] for estado in ("leido", "abandonado")
              for l in registro().por_estado(estado) if l.get("libro")}
    return max(0, total - len(hechos))


def contador():
    en_curso = registro().por_estado("leyendo")
    return registro().contador(progreso=_progreso_de(en_curso), sin_leer=_sin_leer())


def _coincidencias():
    """Libros de fuera que ya parecen estar en la biblioteca (los compraste)."""
    salida = []
    for obra in registro().obras_de_fuera():
        consulta = fuentes.limpiar_titulo(obra["titulo"])
        try:
            libros, _ = _catalogo().libros(busqueda=consulta, por_pagina=6)
        except Exception:
            continue
        for libro in libros:
            completo = _catalogo().libro(libro["id"]) or libro
            if misma_obra(obra["titulo"], obra["autores"], completo["titulo"], completo["autores"],
                          obra.get("isbn"), (completo.get("identificadores") or {}).get("isbn")):
                salida.append({"obra": obra, "libro": completo})
                break
    return salida


@paginas_lecturas.app_template_filter("dias_desde")
def _dias_desde(texto):
    dia = a_fecha(texto)
    return (hoy() - dia).days if dia else None


@paginas_lecturas.app_template_global("estrellas")
def _estrellas(valor):
    return estrellas_texto(valor)


@paginas_lecturas.app_context_processor
def _contexto():
    return {
        "registro_aqui": registro_aqui(),
        "url_obra": url_obra,
        "portada_obra": portada_obra,
        "ESTADOS": ESTADOS,
        "NOMBRE_RECUERDO": NOMBRE_RECUERDO,
        "NOMBRE_SENSACION": NOMBRE_SENSACION,
        "NOMBRE_RITMO": NOMBRE_RITMO,
        "DECISIONES": DECISIONES,
    }


def contexto_libro(datos):
    """Lo que necesita el recuadro «Mi lectura» de la ficha de un libro."""
    if not registro_aqui():
        return {"aqui": False, "enlace": _en_casa(f"/libro/{datos['id']}")}
    obra = registro().obra_de_libro(datos["id"])
    lecturas = registro().lecturas_de(obra["id"]) if obra else []
    actual = lecturas[0] if lecturas else None
    leyendo = next((l for l in lecturas if l["estado"] == "leyendo"), None)
    quiero = next((l for l in lecturas if l["estado"] == "quiero"), None)
    externas = [o for o in registro().obras_de_fuera()
                if misma_obra(o["titulo"], o["autores"], datos["titulo"], datos["autores"],
                              o.get("isbn"), (datos.get("identificadores") or {}).get("isbn"))]
    apertura = registro().aperturas().get(datos["id"])
    cuenta = contador() if leyendo else None
    return {
        "aqui": True, "obra": obra, "lecturas": lecturas, "actual": actual,
        "leyendo": leyendo, "quiero": quiero, "externas": externas,
        "apertura": apertura, "contador": cuenta, "hoy": hoy().isoformat(),
    }


# ---------------------------------------------------------------------- panel
@paginas_lecturas.route("/lecturas")
def panel():
    if not registro_aqui():
        return render_template("lecturas_fuera_de_casa.html", seccion="lecturas",
                               enlace=_en_casa())
    reg = registro()
    leyendo = reg.por_estado("leyendo")
    progreso = _progreso_de(leyendo)
    for lectura in leyendo:
        lectura["progreso"] = progreso.get(lectura["obra"])
        empezado = a_fecha(lectura["empezado"])
        lectura["dias"] = (hoy() - empezado).days + 1 if empezado else None

    # Libros que se han abierto en el lector (más del 5 %) sin apuntarlos.
    en_marcha_ids = {l["libro"] for l in leyendo if l.get("libro")}
    hechos = {l["libro"] for e in ("leido", "abandonado") for l in reg.por_estado(e)
              if l.get("libro")}
    aperturas = reg.aperturas()
    propuestas = []
    for apunte in lector.en_marcha(_catalogo(), limite=8):
        if apunte["id"] in en_marcha_ids or apunte["id"] in hechos or apunte["fraccion"] < 0.05:
            continue
        datos = _catalogo().libro(apunte["id"])
        if datos:
            propuestas.append({**datos, "fraccion": apunte["fraccion"],
                               "desde": aperturas.get(apunte["id"])})

    # Lo que ya has leído o soltado no se recomienda, aunque venga en la lista.
    fuera_de_juego = hechos | {l["libro"] for l in reg.por_estado("leyendo") if l.get("libro")}
    recomendaciones = [r for r in reg.recomendaciones()
                       if not (r["libro"] and r["libro"] in fuera_de_juego)]
    return render_template(
        "lecturas.html",
        seccion="lecturas",
        contador=contador(),
        leyendo=leyendo,
        propuestas=propuestas,
        sin_resena=reg.sin_resena(),
        quiero=reg.por_estado("quiero"),
        historial=reg.historial(),
        coincidencias=_coincidencias(),
        para_ti_biblioteca=[r for r in recomendaciones if r["tipo"] == "biblioteca"],
        para_ti_comprar=[r for r in recomendaciones if r["tipo"] == "comprar"],
        fecha_recomendaciones=reg.preferencia("recomendaciones_fecha"),
        pendientes=len(reg.pendientes_de_procesar()),
    )


@paginas_lecturas.route("/lecturas/ajustes", methods=["POST"])
def ajustes():
    _exigir_registro()
    anio = (request.form.get("nacimiento") or "").strip()
    if not anio.isdigit() or not (1900 < int(anio) <= hoy().year):
        flash("Pon el año de nacimiento con cuatro cifras.", "error")
    else:
        registro().fijar_preferencia("nacimiento", anio)
        flash("Apuntado. El contador ya hace cuentas con tu edad.", "ok")
    return _volver()


# ------------------------------------------------------- acciones de un libro
@paginas_lecturas.route("/lecturas/libro/<int:id_libro>/<accion>", methods=["POST"])
def accion_libro(id_libro, accion):
    """Empezar, querer leer o «ya lo leí» desde la ficha de un libro de la biblioteca."""
    _exigir_registro()
    datos = _catalogo().libro(id_libro)
    if not datos:
        abort(404)
    obra = registro().asegurar_obra_de_libro(datos)
    return _accion(obra, accion, url_for("paginas.libro", id_libro=id_libro))


@paginas_lecturas.route("/lecturas/obra/<int:id_obra>/<accion>", methods=["POST"])
def accion_obra(id_obra, accion):
    _exigir_registro()
    obra = _obra_o_404(id_obra)
    return _accion(obra, accion, url_obra(obra))


def _accion(obra, accion, vuelta):
    reg = registro()
    if accion == "empezar":
        reg.empezar(obra["id"], request.form.get("fecha"))
        medir_palabras(obra)
        cuenta = contador()
        if cuenta.get("libros") and not cuenta["calibrando"]:
            flash(f"Empezado. Es uno de los ~{cuenta['libros']} que te quedan: que merezca la pena.",
                  "ok")
        else:
            flash("Empezado. Cuando lo acabes, márcalo aquí mismo.", "ok")
    elif accion == "quiero":
        if reg.quiero(obra["id"]):
            flash("Apuntado en «Quiero leer».", "ok")
    elif accion == "quitar-quiero":
        reg.quitar_quiero(obra["id"])
        flash("Fuera de «Quiero leer».", "ok")
    elif accion == "ya-leido":
        reg.ya_leido(obra["id"], request.form.get("anio"), request.form.get("recuerdo"),
                     request.form.get("decision"))
        recuerdo = request.form.get("recuerdo")
        decision = request.form.get("decision")
        if decision == "soltar":
            flash("Apuntado como leído y soltado. No te lo volverá a sugerir.", "ok")
        elif decision == "releer":
            flash("Apuntado como leído, y a «Quiero leer» para releerlo.", "ok")
        elif recuerdo == "bien":
            lectura = reg.lecturas_de(obra["id"])[0]
            flash("Apuntado como leído. Si quieres, cuéntame qué te pareció.", "ok")
            return redirect(url_for("lecturas.resena", id_lectura=lectura["id"], volver=vuelta))
        else:
            flash("Apuntado como leído.", "ok")
    elif accion == "recuerdo":
        reg.fijar_recuerdo(obra["id"], request.form.get("recuerdo"), request.form.get("decision"))
        flash("Recuerdo actualizado.", "ok")
    elif accion == "borrar" and obra["de_fuera"]:
        reg.borrar_obra_de_fuera(obra["id"])
        flash("Borrado del diario.", "ok")
        return redirect(url_for("lecturas.panel"))
    else:
        abort(404)
    return _volver(vuelta)


# --------------------------------------------------- acciones de una lectura
@paginas_lecturas.route("/lecturas/lectura/<int:id_lectura>/<accion>", methods=["POST"])
def accion_lectura(id_lectura, accion):
    _exigir_registro()
    lectura = _lectura_o_404(id_lectura)
    obra = registro().obra(lectura["obra"])
    vuelta = url_obra(obra)
    if accion == "terminar":
        registro().terminar(id_lectura, request.form.get("fecha"))
        medir_palabras(obra)
        return redirect(url_for("lecturas.resena", id_lectura=id_lectura, recien=1,
                                volver=request.form.get("volver") or vuelta))
    if accion == "abandonar":
        fraccion = request.form.get("fraccion")
        if fraccion in (None, "") and obra.get("libro"):
            fraccion = lector.posiciones_de_libros(_catalogo(), [obra["libro"]]).get(obra["libro"])
        elif fraccion not in (None, ""):
            try:
                fraccion = float(fraccion) / 100
            except ValueError:
                fraccion = None
        registro().abandonar(id_lectura, fraccion, request.form.get("fecha"))
        flash("Abandonado. Saber dejarlo también es leer bien.", "ok")
    elif accion == "borrar":
        registro().borrar_lectura(id_lectura)
        flash("Lectura borrada.", "ok")
    elif accion == "fechas":
        registro().corregir_fechas(id_lectura, request.form.get("empezado"),
                                   request.form.get("terminado"))
        flash("Fechas corregidas.", "ok")
    else:
        abort(404)
    return _volver(vuelta)


@paginas_lecturas.route("/lecturas/lectura/<int:id_lectura>/resena", methods=["GET", "POST"])
def resena(id_lectura):
    _exigir_registro()
    lectura = _lectura_o_404(id_lectura)
    obra = registro().obra(lectura["obra"])
    vuelta = request.values.get("volver") or url_obra(obra)
    if request.method == "POST":
        registro().guardar_resena(
            id_lectura, request.form.get("estrellas"), request.form.getlist("sensacion"),
            request.form.get("ritmo"), request.form.get("texto"),
        )
        _llevar_estrellas_a_calibre(obra, request.form.get("estrellas"))
        flash("Reseña guardada. La leeré cuando me pidas procesar tus lecturas.", "ok")
        return _volver(vuelta)
    return render_template(
        "lectura_resena.html", seccion="lecturas", lectura=lectura, obra=obra,
        volver=vuelta, recien=request.args.get("recien"),
        SENSACIONES=SENSACIONES, RITMOS=RITMOS,
    )


def _llevar_estrellas_a_calibre(obra, estrellas):
    """La valoración también va a la ficha de Calibre: así se ve en Calibre de
    escritorio, en la rejilla y en el Kobo. Si no se puede, no pasa nada."""
    if not obra.get("libro"):
        return
    try:
        valor = int(round(float(estrellas) * 2)) if estrellas else 0
        if puede_escribir(_ajustes(), _catalogo()):
            current_app.config["BIBLIOTECA"].guardar_edicion(obra["libro"], {"valoracion": valor})
    except Exception:
        pass


# ------------------------------------------------------- libros de fuera
@paginas_lecturas.route("/lecturas/obra/<int:id_obra>")
def obra(id_obra):
    obra = _obra_o_404(id_obra)
    if obra.get("libro"):
        return redirect(url_for("paginas.libro", id_libro=obra["libro"]))
    lecturas = registro().lecturas_de(id_obra)
    candidatos = []
    consulta = request.args.get("buscar")
    if consulta is not None:
        try:
            libros, _ = _catalogo().libros(busqueda=consulta.strip() or obra["titulo"], por_pagina=12)
            candidatos = libros
        except Exception:
            candidatos = []
    cuenta = contador() if any(l["estado"] == "leyendo" for l in lecturas) else None
    return render_template(
        "lectura_obra.html", seccion="lecturas", obra=obra, lecturas=lecturas,
        leyendo=next((l for l in lecturas if l["estado"] == "leyendo"), None),
        quiero=next((l for l in lecturas if l["estado"] == "quiero"), None),
        candidatos=candidatos, consulta=consulta, contador=cuenta, hoy=hoy().isoformat(),
        RECUERDOS=RECUERDOS,
    )


@paginas_lecturas.route("/lecturas/obra/<int:id_obra>/portada")
def portada(id_obra):
    obra = _obra_o_404(id_obra)
    ruta = registro().ruta_portada(obra)
    if not ruta:
        return redirect(url_for("static", filename="logo.svg"))
    respuesta = send_file(ruta, mimetype="image/jpeg", max_age=86400)
    return respuesta


@paginas_lecturas.route("/lecturas/obra/<int:id_obra>/asociar", methods=["POST"])
def asociar(id_obra):
    _exigir_registro()
    obra = _obra_o_404(id_obra)
    datos = _catalogo().libro(request.form.get("libro", type=int) or 0)
    if not datos:
        flash("No encuentro ese libro en la biblioteca.", "error")
        return redirect(url_for("lecturas.obra", id_obra=id_obra))
    registro().asociar(obra["id"], datos)
    flash(f"Unido: tus lecturas de «{obra['titulo']}» ya están en el libro de la biblioteca.", "ok")
    return redirect(url_for("paginas.libro", id_libro=datos["id"]))


@paginas_lecturas.route("/lecturas/fuera", methods=["GET", "POST"])
def fuera():
    """Buscar en Open Library un libro que leíste y no tienes, y apuntarlo."""
    if request.method == "POST":
        _exigir_registro()
        formulario = request.form
        ficha = {
            "titulo": formulario.get("titulo"),
            "autores": formulario.getlist("autor"),
            "publicado": formulario.get("publicado", type=int),
            "isbn": formulario.get("isbn", ""),
            "clave": formulario.get("clave", ""),
            "paginas": formulario.get("paginas", type=int),
            "sinopsis": fuentes.sinopsis_de_obra(formulario.get("clave", "")),
        }
        # ¿Ya estaba en la biblioteca? Entonces no es «de fuera».
        try:
            libros, _ = _catalogo().libros(busqueda=fuentes.limpiar_titulo(ficha["titulo"] or ""),
                                           por_pagina=6)
        except Exception:
            libros = []
        for libro in libros:
            completo = _catalogo().libro(libro["id"]) or libro
            if misma_obra(ficha["titulo"], ficha["autores"], completo["titulo"],
                          completo["autores"], ficha["isbn"],
                          (completo.get("identificadores") or {}).get("isbn")):
                flash("Ese libro ya lo tienes en la biblioteca: apúntalo desde su ficha.", "aviso")
                return redirect(url_for("paginas.libro", id_libro=completo["id"]))
        imagen = None
        if formulario.get("portada"):
            try:
                imagen = fuentes.traer_portada(formulario["portada"])
            except fuentes.ErrorFuente:
                imagen = None
        nueva = registro().crear_de_fuera(ficha, imagen)
        if formulario.get("que") == "quiero":
            registro().quiero(nueva["id"])
            flash("Apuntado en «Quiero leer». No está en tu biblioteca: lo verás solo aquí.", "ok")
            return redirect(url_for("lecturas.obra", id_obra=nueva["id"]))
        if formulario.get("que") == "leyendo":
            registro().empezar(nueva["id"])
            flash("Empezado. No está en tu biblioteca: lo verás solo en Lecturas.", "ok")
            return redirect(url_for("lecturas.obra", id_obra=nueva["id"]))
        flash("Apuntado. Ahora dime cuánto lo recuerdas.", "ok")
        return redirect(url_for("lecturas.obra", id_obra=nueva["id"], ya_leido=1))

    consulta = request.args.get("q", "").strip()
    autor = request.args.get("autor", "").strip()
    candidatos, error = [], None
    if consulta:
        try:
            candidatos = fuentes.buscar_ficha(consulta, autor, maximo=8, con_sinopsis=False)
        except fuentes.ErrorFuente as ex:
            error = str(ex)
    return render_template("lecturas_buscar.html", seccion="lecturas", consulta=consulta,
                           autor=autor, candidatos=candidatos, error=error)


# ---------------------------------------------------------------- modo repaso
def _ids_biblioteca():
    with _catalogo().conectar() as cx:
        return [f[0] for f in cx.execute("SELECT id FROM books ORDER BY sort COLLATE NOCASE")]


@paginas_lecturas.route("/lecturas/repaso", methods=["GET", "POST"])
def repaso():
    """Pasar por la biblioteca libro a libro: ¿leído?, ¿lo recuerdas?, estrellas.

    Es la carga inicial del diario sin tener que entrar ficha por ficha. Lo
    que se marca como «no lo he leído» no vuelve a salir.
    """
    _exigir_registro()
    reg = registro()
    if request.method == "POST":
        datos = _catalogo().libro(request.form.get("libro", type=int) or 0)
        if not datos:
            abort(404)
        obra = reg.asegurar_obra_de_libro(datos)
        que = request.form.get("que")
        if que == "no":
            reg.marcar_repasado(obra["id"])
        elif que == "quiero":
            reg.marcar_repasado(obra["id"])
            reg.quiero(obra["id"])
        elif que == "leido":
            reg.marcar_repasado(obra["id"])
            id_lectura = reg.ya_leido(obra["id"], request.form.get("anio"),
                                      request.form.get("recuerdo"), request.form.get("decision"))
            if request.form.get("estrellas"):
                reg.guardar_resena(id_lectura, request.form.get("estrellas"),
                                   request.form.getlist("sensacion"), None,
                                   request.form.get("texto"))
                _llevar_estrellas_a_calibre(obra, request.form.get("estrellas"))
        elif que == "leyendo":
            reg.marcar_repasado(obra["id"])
            reg.empezar(obra["id"])
        return redirect(url_for("lecturas.repaso"))

    hechos = reg.libros_repasados()
    ids = _ids_biblioteca()
    pendientes = [i for i in ids if i not in hechos]
    actual = _catalogo().libro(pendientes[0]) if pendientes else None
    return render_template(
        "lecturas_repaso.html", seccion="lecturas", libro=actual,
        quedan=len(pendientes), total=len(ids), RECUERDOS=RECUERDOS,
        SENSACIONES=SENSACIONES,
    )


# --------------------------------------------------------- recomendaciones
@paginas_lecturas.route("/lecturas/recomendacion/<int:id_recomendacion>/<accion>",
                        methods=["POST"])
def recomendacion(id_recomendacion, accion):
    _exigir_registro()
    reg = registro()
    rec = reg.recomendacion(id_recomendacion)
    if not rec:
        abort(404)
    if accion == "descartar":
        reg.descartar_recomendacion(id_recomendacion)
        flash("Descartada. También me sirve para afinar.", "ok")
        return _volver()
    if accion == "quiero":
        if rec["libro"]:
            datos = _catalogo().libro(rec["libro"])
            if not datos:
                abort(404)
            obra = reg.asegurar_obra_de_libro(datos)
        else:
            imagen = None
            if rec["portada_url"]:
                try:
                    imagen = fuentes.traer_portada(rec["portada_url"])
                except fuentes.ErrorFuente:
                    imagen = None
            obra = reg.crear_de_fuera({
                "titulo": rec["titulo"], "autores": rec["autores"], "publicado": rec["anio"],
                "clave": rec["clave_ol"], "sinopsis": rec["sinopsis"], "paginas": rec["paginas"],
            }, imagen)
        reg.quiero(obra["id"])
        reg.descartar_recomendacion(id_recomendacion)
        flash("A «Quiero leer».", "ok")
        return _volver()
    abort(404)


# ----------------------------------------------------------------------- API
# Lo que usa Claude para leer las reseñas y devolver recomendaciones. Sin
# contraseña, como el resto: la app solo se ve desde casa o por Tailscale.
@paginas_lecturas.route("/api/lecturas")
def api_todo():
    if not registro_aqui():
        abort(404)
    return jsonify({"lecturas": registro().exportar(), "contador": contador(),
                    "recomendaciones": registro().recomendaciones()})


@paginas_lecturas.route("/api/lecturas/pendientes")
def api_pendientes():
    if not registro_aqui():
        abort(404)
    return jsonify({"pendientes": registro().pendientes_de_procesar()})


@paginas_lecturas.route("/api/lecturas/procesadas", methods=["POST"])
def api_procesadas():
    _exigir_registro()
    cuerpo = request.get_json(silent=True) or {}
    hechas = registro().marcar_procesadas(cuerpo.get("lecturas") or [])
    return jsonify({"procesadas": hechas})


@paginas_lecturas.route("/api/lecturas/recomendaciones", methods=["POST"])
def api_recomendaciones():
    """Carga la fila «Para ti». Cada elemento: {libro: id} para uno de la
    biblioteca, o {titulo, autores, anio, portada, clave_ol, sinopsis, paginas}
    para uno de comprar; y siempre un `motivo` corto."""
    _exigir_registro()
    cuerpo = request.get_json(silent=True) or {}
    elementos = cuerpo.get("recomendaciones") or []
    validos = []
    for e in elementos:
        if e.get("libro"):
            datos = _catalogo().libro(int(e["libro"]))
            if not datos:
                continue
            e["libro"] = int(e["libro"])
            e["titulo"] = datos["titulo"]
            e["autores"] = datos["autores"]
        elif not e.get("titulo"):
            continue
        validos.append(e)
    registro().poner_recomendaciones(validos, reemplazar=cuerpo.get("reemplazar", True))
    return jsonify({"cargadas": len(validos)})
