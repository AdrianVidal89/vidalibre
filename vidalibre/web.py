"""Las pantallas de Vidalibre."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from html import unescape
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, jsonify,
                   redirect, render_template, request, send_file, url_for)

from . import (NOMBRE, __version__, dispositivo, drm, ficheros, fuentes,
               lector, motor, puede_escribir, sincronizacion)
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


def puente():
    return current_app.config["PUENTE"]


def catalogo_escribible():
    """¿Puede ESTA copia tocar el catálogo, o hay que pedírselo al NAS?

    Si hay una biblioteca de casa configurada, **siempre** escribe ella, aunque
    desde aquí pareciera posible. No es manía: el catálogo está en una carpeta
    de red y los cerrojos no cruzan bien. Probándolo salió exactamente eso —
    el portátil intentó apuntar algo justo mientras el NAS convertía un libro y
    se encontró la base de datos bloqueada. Con un solo escritor, eso no pasa.
    """
    return puede_escribir(ajustes(), catalogo())


def avisar_de_puente():
    """Mensaje único para lo que no se puede hacer desde el portátil."""
    enlace = ajustes().servidor or "la biblioteca de casa"
    flash(
        "Esto se hace en la biblioteca de casa: desde aquí la carpeta está en red y "
        f"el catálogo no se puede tocar sin riesgo. Ábrela en {enlace}.", "aviso",
    )


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
        "decada": request.args.get("decada") or None,
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


@paginas.app_template_filter("sin_html")
def _filtro_sin_html(texto, limite=420):
    """La descripción en texto pelado, para los sitios donde no cabe el HTML.

    En el escaparate de la portada la sinopsis va recortada a unas líneas: si
    se dejara el HTML de Calibre, el recorte podría partir una etiqueta por la
    mitad y descolocar media página."""
    import re as _re

    texto = _re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", texto or "")
    texto = _re.sub(r"(?s)<[^>]+>", " ", texto)
    texto = unescape(texto)
    texto = _re.sub(r"\s+", " ", texto).strip()
    if limite and len(texto) > limite:
        texto = texto[:limite].rsplit(" ", 1)[0] + "…"
    return texto


@paginas.app_context_processor
def _contexto():
    return {
        "ajustes": ajustes(),
        "nombre_app": NOMBRE,
        "version": __version__,
        "categorias": CATEGORIAS,
        "formatos_salida": ficheros.FORMATOS_SALIDA,
        "trabajos_activos": len(cola().activos()),
        "escribe_catalogo": catalogo_escribible(),
        "servidor_remoto": ajustes().servidor,
    }


@paginas.errorhandler(ErrorCatalogo)
def _error_catalogo(ex):
    flash(str(ex), "error")
    return redirect(request.referrer or url_for("paginas.inicio"))


# -------------------------------------------------------------------- portada
GRUPOS = {
    "nada": "Todos",
    "genero": "Género",
    "autor": "Autor",
    "ano": "Año",
}


def _encabezado():
    """Lo de arriba de la biblioteca: el libro a medias y las dos filas."""
    cat = catalogo()

    # Lo que tienes a medias. La posición ya viene resuelta: al sincronizar con
    # el Kobo gana la más avanzada, así que aquí no hay que volver a decidir.
    empezados = []
    for apunte in lector.en_marcha(cat, limite=12):
        datos = cat.libro(apunte["id"])
        if datos:
            datos["fraccion"] = apunte["fraccion"]
            empezados.append(datos)

    recientes, _ = cat.libros(orden="reciente", por_pagina=18)

    destacado = empezados[0] if empezados else (recientes[0] if recientes else None)
    if destacado:
        destacado = {**(cat.libro(destacado["id"]) or destacado),
                     "fraccion": next((l["fraccion"] for l in empezados
                                       if l["id"] == destacado["id"]), 0.0)}
    return {"destacado": destacado, "empezados": empezados, "recientes": recientes}


def _grupos(agrupar, pagina=1, por_pagina=10, por_grupo=18):
    """La biblioteca partida en estantes: por género, por autor o por año.

    Se pagina **por grupos**, no por libros: con 99 autores, enseñarlos todos
    de golpe serían 99 consultas y una página interminable. Los grupos salen
    de mayor a menor, que es el orden en el que se buscan.
    """
    cat = catalogo()
    if agrupar == "genero":
        crudos = [{"nombre": g["nombre"], "libros": g["libros"],
                   "filtro": {"etiqueta": g["nombre"]}}
                  for g in cat.categoria("etiquetas", minimo=1)]
    elif agrupar == "autor":
        crudos = [{"nombre": a["nombre"], "libros": a["libros"],
                   "filtro": {"autor": a["nombre"]}}
                  for a in cat.categoria("autores", minimo=1)]
    elif agrupar == "ano":
        crudos = [{"nombre": f"Los {d['decada']}",
                   "detalle": f"{d['decada']}–{d['decada'] + 9}",
                   "libros": d["libros"], "filtro": {"decada": d["decada"]}}
                  for d in cat.decadas(minimo=1)]
    else:
        return [], 1, 1

    if agrupar != "ano":
        crudos.sort(key=lambda g: (-g["libros"], g["nombre"].lower()))

    total_grupos = len(crudos)
    paginas_totales = max(1, -(-total_grupos // por_pagina))
    pagina = max(1, min(pagina, paginas_totales))
    trozo = crudos[(pagina - 1) * por_pagina: pagina * por_pagina]

    for grupo in trozo:
        grupo["libros_muestra"], _ = cat.libros(
            orden="titulo", por_pagina=por_grupo, **grupo["filtro"])
        grupo["url"] = url_for("paginas.inicio", todo=1, **grupo["filtro"])
    return trozo, pagina, paginas_totales


def _contexto_catalogo():
    """La parte de abajo: todos los libros, sueltos o repartidos en estantes."""
    agrupar = request.args.get("agrupar", "nada")
    if agrupar not in GRUPOS:
        agrupar = "nada"
    pagina = max(1, request.args.get("pagina", 1, type=int))
    # Alfabético por defecto, no «lo último»: esa fila ya está justo encima y
    # si no, las dos empiezan con los mismos libros y parece un error.
    orden = request.args.get("orden", "titulo")

    if agrupar == "nada":
        por_pagina = ajustes().por_pagina
        libros, total = catalogo().libros(orden=orden, pagina=pagina, por_pagina=por_pagina)
        return {
            "agrupar": agrupar, "grupos_posibles": GRUPOS, "orden": orden,
            "libros": libros, "total": total, "pagina": pagina,
            "paginas_totales": max(1, -(-total // por_pagina)),
            "lecturas": lector.posiciones_de_libros(
                catalogo(), [l["id"] for l in libros]),
            "grupos": [],
        }

    grupos, pagina, paginas_totales = _grupos(agrupar, pagina)
    return {
        "agrupar": agrupar, "grupos_posibles": GRUPOS, "orden": orden,
        "libros": [], "total": catalogo().resumen()["libros"], "pagina": pagina,
        "paginas_totales": paginas_totales, "lecturas": {}, "grupos": grupos,
    }


@paginas.route("/catalogo")
def catalogo_parcial():
    """Solo el trozo de abajo de la biblioteca.

    Al cambiar el agrupamiento se pide esto y se cambia ese trozo en su sitio,
    sin recargar la página entera ni perder el desplazamiento.
    """
    try:
        contexto = _contexto_catalogo()
    except ErrorCatalogo as ex:
        return render_template("sin_biblioteca.html", error=str(ex)), 503
    return render_template("_catalogo.html", **contexto)


# ----------------------------------------------------------------- biblioteca
@paginas.route("/")
def inicio():
    filtros = filtros_de_la_url()
    orden = request.args.get("orden", "reciente")
    pagina = max(1, request.args.get("pagina", 1, type=int))
    hay_filtro = any(filtros.values())

    try:
        if not hay_filtro:
            # La biblioteca de entrada: lo que tienes a medias, lo último que
            # has añadido y debajo todos los libros (sueltos o agrupados).
            return render_template(
                "biblioteca.html",
                seccion="biblioteca",
                chips=[],
                resumen=catalogo().resumen(),
                **_encabezado(),
                **_contexto_catalogo(),
            )
    except ErrorCatalogo as ex:
        return render_template("sin_biblioteca.html", error=str(ex)), 503

    # Con un filtro puesto no hay encabezado que valga: lo que se quiere ver es
    # el resultado de la búsqueda y nada más.
    por_pagina = ajustes().por_pagina
    try:
        libros, total = catalogo().libros(
            orden=orden, pagina=pagina, por_pagina=por_pagina, **filtros
        )
    except ErrorCatalogo as ex:
        return render_template("sin_biblioteca.html", error=str(ex)), 503

    etiquetas_filtro = {
        "busqueda": ("Buscando", "q"), "autor": ("Autor", "autor"),
        "serie": ("Serie", "serie"), "etiqueta": ("Etiqueta", "etiqueta"),
        "idioma": ("Idioma", "idioma"), "editorial": ("Editorial", "editorial"),
        "formato": ("Formato", "formato"), "sin_formato": ("Sin", "sin_formato"),
        "decada": ("Década", "decada"),
    }
    chips = [
        {"texto": f"{etiquetas_filtro[clave][0]}: {valor}",
         "url": _url_sin(etiquetas_filtro[clave][1])}
        for clave, valor in filtros.items() if valor
    ]
    return render_template(
        "resultados.html",
        seccion="biblioteca",
        chips=chips,
        lecturas=lector.posiciones_de_libros(catalogo(), [l["id"] for l in libros]),
        libros=libros,
        total=total,
        pagina=pagina,
        paginas_totales=max(1, -(-total // por_pagina)),
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


def _relacionados(datos, por_fila=18):
    """Las filas de «esto también te suena» de la ficha.

    Tres criterios, de más a menos cercano: la misma serie, el mismo autor y
    el mismo género. Un libro no se repite entre filas, y el propio libro
    nunca sale: es la pega típica de estas secciones.
    """
    cat = catalogo()
    id_libro = datos["id"]
    filas, ya_vistos = [], {id_libro}

    def fila(titulo, enlace, libros):
        nuevos = [l for l in libros if l["id"] not in ya_vistos]
        if not nuevos:
            return
        ya_vistos.update(l["id"] for l in nuevos)
        filas.append({"titulo": titulo, "enlace": enlace, "libros": nuevos})

    if datos["serie"]:
        libros, _ = cat.libros(serie=datos["serie"], orden="autor", por_pagina=por_fila)
        fila(f"Más de «{datos['serie']}»",
             url_for("paginas.inicio", serie=datos["serie"]), libros)

    for autor in datos["autores"][:2]:
        libros, total = cat.libros(autor=autor, orden="titulo", por_pagina=por_fila)
        fila(f"Más de {autor}", url_for("paginas.inicio", autor=autor), libros)

    for etiqueta in datos["etiquetas"][:3]:
        libros, _ = cat.libros(etiqueta=etiqueta, orden="reciente", por_pagina=por_fila)
        fila(f"Más de {etiqueta}", url_for("paginas.inicio", etiqueta=etiqueta), libros)
        if len(filas) >= 4:
            break

    return filas


@paginas.route("/libro/<int:id_libro>")
def libro(id_libro):
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    return render_template(
        "libro.html",
        libro=datos,
        lectura=lector.leer_posicion(catalogo(), id_libro, "EPUB"),
        relacionados=_relacionados(datos),
        hay_motor=motor.hay_motor(),
        kindles=dispositivo.detectar() if ajustes().con_dispositivo else [],
        en_disco={
            f["formato"]: bool(
                (ruta := ficheros.ruta_fichero(catalogo().raiz, datos, f["formato"]))
                and ruta.is_file()
            )
            for f in datos["ficheros"]
        },
        candados={
            f["formato"]: drm.estado(ficheros.ruta_fichero(catalogo().raiz, datos, f["formato"]))
            for f in datos["ficheros"]
        },
    )


@paginas.app_template_global("portada_url")
def url_portada(libro, mini=False):
    """La dirección de la portada de un libro, con marca de versión.

    La marca (`v`) es lo que hace que al cambiar una portada se vea el cambio.
    Sin ella el navegador se queda con la imagen vieja en su memoria durante
    horas: la dirección es la misma, así que ni se molesta en volver a pedirla.
    Pasó justo eso al completar una ficha desde internet — la portada nueva ya
    estaba guardada en el disco y la pantalla seguía enseñando la anterior.
    """
    marca = str(libro.get("modificado") or libro.get("anadido") or "")
    marca = re.sub(r"[^0-9]", "", marca)[:14] or "0"
    return url_for("paginas.portada", id_libro=libro["id"],
                   mini=1 if mini else None, v=marca)


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
    if request.args.get("v"):
        # Con marca de versión la dirección cambia sola cuando cambia la
        # portada, así que se puede guardar para siempre sin riesgo.
        respuesta.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    else:
        # Sin marca (un enlace viejo, o alguien escribiéndola a mano) hay que
        # preguntar siempre: es barato, porque el ETag evita volver a bajarla.
        respuesta.headers["Cache-Control"] = "private, no-cache"
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
        # `draft` le dice al descodificador de JPEG que saque la imagen ya
        # reducida en vez de montarla entera en memoria para luego encogerla:
        # una portada de 1600x2400 pasa de ocupar 11 MB a menos de 1 MB. Con
        # 146 portadas en pantalla, es la diferencia que se nota en el NAS.
        imagen.draft("RGB", (ancho * 2, ancho * 3))
        if imagen.mode not in ("RGB", "L"):
            imagen = imagen.convert("RGB")
        if imagen.width > ancho:
            alto = max(1, int(imagen.height * ancho / imagen.width))
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
    if not catalogo_escribible():
        avisar_de_puente()
        return redirect(url_for("paginas.libro", id_libro=id_libro))
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
        "indice_serie": formulario.get("indice_serie", ""),
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
    # Añadir un formato sí sabe apañárselas por su cuenta (deja el fichero y
    # se lo dice al NAS); lo demás toca el catálogo y no se puede desde aquí.
    if not catalogo_escribible() and nombre != "anadir-formato":
        avisar_de_puente()
        return redirect(url_for("paginas.libro", id_libro=id_libro))

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

    # Desde el portátil no se puede escribir en el catálogo: el libro se manda
    # a la biblioteca de casa, que lo coloca y lo apunta.
    if not catalogo_escribible():
        if not puente():
            avisar_de_puente()
            return redirect(url_for("paginas.subir"))
        bien = 0
        with tempfile.TemporaryDirectory() as tmp:
            for subida in subidos:
                try:
                    ruta, _extension = ficheros.recibir_subida(subida, Path(tmp))
                    puente().subir_libro(ruta)
                    bien += 1
                except Exception as ex:
                    flash(f"{subida.filename}: {ex}", "error")
        if bien:
            flash(f"{bien} libro(s) enviados a la biblioteca de casa.", "ok")
        return redirect(url_for("paginas.inicio", orden="reciente"))

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


# ------------------------------------------------------------ libros nuevos
@paginas.route("/descubrir")
def descubrir():
    """Buscar libros libres y traérselos."""
    consulta = request.args.get("q", "").strip()
    idioma = request.args.get("idioma", "es")
    resultados, errores = [], []
    novedades = []
    if consulta:
        resultados, errores = fuentes.buscar(consulta, idioma, ajustes().datos / "fuentes")
    else:
        try:
            novedades = fuentes.novedades_standard(ajustes().datos / "fuentes")
        except fuentes.ErrorFuente as ex:
            errores.append(str(ex))
    for error in errores:
        flash(error, "aviso")
    return render_template(
        "descubrir.html",
        consulta=consulta,
        idioma=idioma,
        resultados=resultados,
        novedades=novedades,
    )


@paginas.route("/descubrir/anadir", methods=["POST"])
def anadir_de_fuente():
    comprobar_escritura()
    libro = {
        "fuente": request.form.get("fuente", ""),
        "id": request.form.get("id", ""),
        "titulo": request.form.get("titulo", "") or "Libro",
        "autores": [a for a in request.form.getlist("autor") if a],
        "portada": request.form.get("portada", ""),
        "formatos": ({"epub": request.form["epub"]} if request.form.get("epub") else {}),
    }
    trabajo = Trabajo(f"Traer «{libro['titulo']}»", tipo="descarga")

    def tarea(trabajo):
        trabajo.anotar("Buscando el fichero…")
        try:
            formatos = fuentes.formatos_de(libro)
        except fuentes.ErrorFuente as ex:
            trabajo.estado = "fallado"
            trabajo.anotar(str(ex))
            return
        url = formatos.get("epub") or next(iter(formatos.values()), None)
        if not url:
            trabajo.estado = "fallado"
            trabajo.anotar("Ese libro no tiene ningún fichero descargable.")
            return

        trabajo.anotar("Descargando…")
        carpeta = ajustes().temporales / trabajo.id
        try:
            fichero = fuentes.descargar(url, carpeta, f"{libro['titulo']}.epub")
        except fuentes.ErrorFuente as ex:
            trabajo.estado = "fallado"
            trabajo.anotar(str(ex))
            return

        trabajo.anotar("Metiéndolo en la biblioteca…")
        try:
            if not catalogo_escribible():
                if not puente():
                    raise ErrorCatalogo("Desde aquí no puedo escribir en la biblioteca.")
                puente().subir_libro(fichero)
                trabajo.resultados.append({"texto": f"{libro['titulo']} (en la biblioteca de casa)"})
            else:
                extension = fichero.suffix.lstrip(".").lower()
                id_libro, avisos = biblioteca().anadir_libro(
                    fichero, extension, metadatos={
                        "titulo": libro["titulo"], "autores": libro["autores"],
                    }, mover=True,
                )
                for aviso in avisos:
                    trabajo.anotar(aviso)
                datos = catalogo().libro(id_libro)
                trabajo.resultados.append({"texto": datos["titulo"], "libro": id_libro})
        except Exception as ex:
            trabajo.estado = "fallado"
            trabajo.anotar(f"No he podido guardarlo: {ex}")
            return
        finally:
            shutil.rmtree(carpeta, ignore_errors=True)
        trabajo.anotar("Listo.")

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


@paginas.route("/libro/<int:id_libro>/ficha-online")
def ficha_online(id_libro):
    """Candidatos de Open Library para completar la ficha de un libro."""
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    consulta = request.args.get("q") or datos["titulo"]
    autor = request.args.get("autor", (datos["autores"] or [""])[0])
    candidatos, error = [], None
    try:
        candidatos = fuentes.buscar_ficha(consulta, autor)
    except fuentes.ErrorFuente as ex:
        error = str(ex)
    return render_template("ficha_online.html", libro=datos, candidatos=candidatos,
                           consulta=consulta, autor=autor, error=error)


@paginas.route("/libro/<int:id_libro>/ficha-online", methods=["POST"])
def aplicar_ficha_online(id_libro):
    comprobar_escritura()
    if not catalogo_escribible():
        avisar_de_puente()
        return redirect(url_for("paginas.libro", id_libro=id_libro))
    datos = catalogo().libro(id_libro)
    if not datos:
        abort(404)
    formulario = request.form
    cambios, traido = {}, []
    if formulario.get("usar_titulo") and formulario.get("titulo"):
        cambios["titulo"] = formulario["titulo"]
        traido.append("el título")
    if formulario.get("usar_autores") and formulario.getlist("autor"):
        cambios["autores"] = formulario.getlist("autor")
        traido.append("los autores")
    if formulario.get("usar_publicado") and formulario.get("publicado"):
        cambios["publicado"] = fecha_calibre(f"{formulario['publicado']}-01-01")
        traido.append("el año")
    if formulario.get("usar_editorial") and formulario.get("editorial"):
        cambios["editorial"] = formulario["editorial"]
        traido.append("la editorial")
    if formulario.get("usar_sinopsis") and formulario.get("sinopsis"):
        cambios["descripcion"] = formulario["sinopsis"].strip()
        traido.append("la sinopsis")
    if formulario.get("usar_etiquetas") and formulario.getlist("etiqueta"):
        cambios["etiquetas"] = sorted(set(datos["etiquetas"] + formulario.getlist("etiqueta")))
        traido.append("los géneros")
    if formulario.get("usar_isbn") and formulario.get("isbn"):
        cambios["identificadores"] = {**datos["identificadores"], "isbn": formulario["isbn"]}
        traido.append("el ISBN")

    imagen = None
    if formulario.get("usar_portada") and formulario.get("portada"):
        try:
            imagen = fuentes.traer_portada(formulario["portada"])
            traido.append("la portada")
        except fuentes.ErrorFuente as ex:
            flash(str(ex), "atencion")

    if not cambios and not imagen:
        flash("No has marcado nada que traer: marca al menos una casilla y vuelve a darle.",
              "atencion")
        return redirect(url_for("paginas.ficha_online", id_libro=id_libro,
                                q=request.args.get("q"), autor=request.args.get("autor")))

    biblioteca().guardar_edicion(id_libro, cambios, portada=imagen)
    flash("Traído de Open Library: " + ", ".join(traido) + ".", "ok")
    return redirect(url_for("paginas.libro", id_libro=id_libro))


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
    aparatos = [sincronizacion.info(a) for a in sincronizacion.detectar()]
    for datos in aparatos:
        datos["libros"] = len(sincronizacion.libros_en_el_aparato(datos))
    return render_template(
        "dispositivo.html",
        aparatos=aparatos,
        # Enchufados pero sin montar: sin esto la pantalla sale vacía y parece
        # que el lector no está, cuando lo que pasa es que falta un paso.
        apagados=dispositivo.sin_montar(),
        contenido={a["ruta"]: sincronizacion.libros_en_el_aparato(a) for a in aparatos},
        ultimos=catalogo().libros(orden="reciente", por_pagina=24)[0],
    )


@paginas.route("/dispositivo/montar", methods=["POST"])
def montar_aparato():
    """Monta un lector que está enchufado pero que el escritorio no ha abierto."""
    if not ajustes().con_dispositivo:
        abort(404)
    try:
        ruta = dispositivo.montar(request.form.get("bloque", ""))
    except (ValueError, RuntimeError) as ex:
        flash(str(ex), "error")
    else:
        flash(f"Lector conectado. Está en {ruta}.", "ok")
    return redirect(url_for("paginas.aparato"))


def _aparato_de(ruta):
    for aparato in sincronizacion.detectar():
        if aparato["ruta"] == ruta:
            return aparato
    return None


@paginas.route("/dispositivo/sincronizar")
def ver_sincronizacion():
    """Enseña qué pasaría, antes de tocar nada."""
    if not ajustes().con_dispositivo:
        abort(403)
    aparato = _aparato_de(request.args.get("ruta", ""))
    if not aparato:
        flash("No veo ese aparato conectado.", "error")
        return redirect(url_for("paginas.aparato"))
    plan = sincronizacion.plan_de_sincronizacion(catalogo(), aparato)
    return render_template("sincronizar.html", plan=plan, aparato=aparato)


@paginas.route("/dispositivo/sincronizar", methods=["POST"])
def hacer_sincronizacion():
    comprobar_escritura()
    if not ajustes().con_dispositivo:
        abort(403)
    aparato = _aparato_de(request.form.get("ruta", ""))
    if not aparato:
        flash("No veo ese aparato conectado.", "error")
        return redirect(url_for("paginas.aparato"))

    trabajo = Trabajo(f"Sincronizar la lectura con {aparato['nombre']}", tipo="sincronizacion")

    def tarea(trabajo):
        from .lector_web import _LIBROS, _preparar_cache

        _preparar_cache()
        trabajo.anotar("Mirando por dónde va cada libro…")
        plan = sincronizacion.plan_de_sincronizacion(catalogo(), aparato)
        trabajo.pasos = max(1, len(plan["a_la_app"]) + len(plan["al_aparato"]))
        resultado = sincronizacion.aplicar(
            catalogo(), plan, _LIBROS, ajustes().datos / "copias-aparatos",
            puente=puente() if not catalogo_escribible() else None,
        )
        trabajo.paso = trabajo.pasos
        for linea in plan["a_la_app"]:
            trabajo.resultados.append({
                "texto": f"{linea['titulo']}: del aparato a la app "
                         f"({linea['fraccion_aparato'] * 100:.0f}%)",
                "libro": linea["libro"],
            })
        for linea in plan["al_aparato"]:
            trabajo.resultados.append({
                "texto": f"{linea['titulo']}: de la app al aparato "
                         f"({linea['fraccion_app'] * 100:.0f}%)",
                "libro": linea["libro"],
            })
        trabajo.anotar(
            f"Listo: {resultado['a_la_app']} al día en la app, "
            f"{resultado['al_aparato']} en el aparato, {len(plan['iguales'])} ya iguales."
        )
        for error in resultado["errores"]:
            trabajo.anotar(error)
        if resultado["errores"] and not (resultado["a_la_app"] or resultado["al_aparato"]):
            trabajo.estado = "fallado"

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


@paginas.route("/dispositivo/enviar", methods=["POST"])
def enviar_al_aparato():
    comprobar_escritura()
    if not ajustes().con_dispositivo:
        abort(403)
    ruta_aparato = request.form.get("kindle") or request.form.get("ruta") or ""
    aparato = _aparato_de(ruta_aparato)
    if not aparato:
        flash("No veo ese aparato conectado.", "error")
        return redirect(url_for("paginas.aparato"))
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    formato = request.form.get("formato", "azw3").lower()
    if not ids:
        flash("No has elegido ningún libro.", "aviso")
        return redirect(url_for("paginas.aparato"))
    kindle = ruta_aparato

    trabajo = Trabajo(f"{len(ids)} libro(s) a {aparato['nombre']}",
                      pasos=len(ids), tipo="aparato")

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
                # El formato pedido es el formato pedido: si se eligió MOBI es
                # porque el aparato es de los viejos, y esos no leen AZW3.
                # Antes se aprovechaba cualquier formato que el Kindle
                # entendiera y se mandaba un AZW3 a un Kindle que no podía
                # abrirlo.
                _formato, ruta, convertido = biblioteca().asegurar_formato(
                    id_libro, [formato], trabajo=trabajo,
                )
                origen_formato, origen_ruta = biblioteca().mejor_origen(datos)
                if aparato["tipo"] == "kobo":
                    sincronizacion.copiar_al_kobo(ruta, ruta_aparato)
                    con_portada = True   # el Kobo se saca la portada él solo
                else:
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
        trabajo.anotar("Expulsa el aparato antes de desenchufarlo.")

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

    trabajo = Trabajo("Reponer portadas del Kindle", tipo="aparato")

    def tarea(trabajo):
        hechas, fallidas = dispositivo.reparar_portadas(kindle, trabajo)
        trabajo.anotar(f"Portadas repuestas: {len(hechas)}. Sin portada: {len(fallidas)}.")
        trabajo.resultados = [{"texto": n} for n in hechas[:100]]

    encolar(trabajo, tarea)
    return redirect(url_for("paginas.trabajos", destacado=trabajo.id))


# ------------------------------------------------------------------- la API
# Dos puertas para la copia del portátil, que no puede escribir en el catálogo
# por estar la biblioteca en una carpeta de red. Ver `puente.py`.
@paginas.route("/api/posicion", methods=["POST"])
def api_posicion():
    from . import lector

    comprobar_escritura()
    cuerpo = request.get_json(silent=True) or {}
    id_libro = int(cuerpo.get("libro") or 0)
    if not catalogo().libro(id_libro):
        abort(404)
    lector.guardar_posicion(
        catalogo(), id_libro, (cuerpo.get("formato") or "EPUB"),
        str(cuerpo.get("posicion") or "")[:400], cuerpo.get("fraccion") or 0,
    )
    return jsonify({"guardado": True})


@paginas.route("/api/registrar-formato", methods=["POST"])
def api_registrar_formato():
    """Apunta en el catálogo un fichero que ya está en la carpeta del libro.

    Lo usa el portátil después de convertir: el fichero lo deja él en la
    carpeta compartida (eso sí funciona por red), y el catálogo lo escribe
    quien manda, que es el NAS.
    """
    comprobar_escritura()
    cuerpo = request.get_json(silent=True) or {}
    id_libro = int(cuerpo.get("libro") or 0)
    formato = (cuerpo.get("formato") or "").lower().lstrip(".")
    datos = catalogo().libro(id_libro)
    if not datos or not formato:
        abort(404)

    carpeta = catalogo().raiz / datos["ruta"]
    base = ficheros.nombre_base(datos["titulo"], (datos["autores"] or ["x"])[0])
    fichero = carpeta / f"{base}.{formato}"
    if not fichero.is_file():
        # Puede que el nombre venga de una edición anterior: se busca por
        # extensión dentro de la carpeta del libro.
        candidatos = sorted(carpeta.glob(f"*.{formato}")) if carpeta.is_dir() else []
        if not candidatos:
            return jsonify({"registrado": False,
                            "error": "No encuentro ese fichero en la carpeta del libro."}), 404
        fichero = candidatos[0]

    with catalogo().escribir() as cx:
        catalogo().anadir_formato(cx, id_libro, formato, fichero.stem, fichero.stat().st_size)
    actualizado = catalogo().libro(id_libro)
    try:
        ficheros.escribir_opf(catalogo().raiz, actualizado, f"{NOMBRE} ({__version__})")
    except OSError:
        pass
    return jsonify({"registrado": True, "formato": formato.upper()})


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
        "escribe_catalogo": catalogo_escribible() if bien else False,
        "servidor": ajustes().servidor or None,
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
