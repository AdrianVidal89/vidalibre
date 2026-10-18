"""Sincronizar por dónde vas leyendo con el Kobo y el Kindle.

La idea es la que tendría que venir de fábrica: lees un rato en el móvil, luego
enchufas el aparato, y el que vaya más adelantado le pasa su posición al otro.
Nunca se retrocede: **gana siempre el más avanzado**, que es lo que uno espera
y lo que evita perder lectura por un despiste.

Cada aparato guarda esto a su manera:

- **Kobo**: todo vive en `.kobo/KoboReader.sqlite`, una base de datos normal y
  corriente. De cada libro guarda el porcentaje leído (`___PercentRead`), si
  está empezado o terminado (`ReadStatus`) y por qué capítulo va
  (`ChapterIDBookmarked`). Se puede **leer y escribir**, así que con el Kobo la
  sincronización va en los dos sentidos.
- **Kindle**: guarda la posición en un fichero suelto dentro de la carpeta
  `.sdr` de cada libro. Los Kindle antiguos usan `.mbp`, que sí se sabe leer
  (el formato lo tiene descifrado Calibre desde hace años y de ahí sale la
  lectura de aquí). Los modernos usan `.azw3r`, que es un formato cerrado sin
  descifrar: de esos **no se puede sacar la posición**, y la aplicación lo dice
  en vez de aparentar que ha sincronizado. En el Kindle, además, **no se
  escribe nunca**: no hay forma documentada de hacerlo sin arriesgarse a dejarle
  un fichero corrupto al aparato.

Antes de tocar nada en el Kobo se guarda una copia de su base de datos.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import struct
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from . import dispositivo, ficheros, lector

# Un Kindle mide la posición en "localizaciones" de 150 bytes. Es la constante
# que usa el propio aparato.
BYTES_POR_LOCALIZACION = 150

EXTENSIONES_MARCADOR = (".mbp", ".pds", ".tan", ".azw3r", ".mbp1")


# --------------------------------------------------------------- aparatos
def es_kobo(ruta: Path) -> bool:
    try:
        return (ruta / ".kobo" / "KoboReader.sqlite").is_file()
    except OSError:
        return False


def detectar():
    """Todos los aparatos conectados ahora mismo, de los dos tipos."""
    encontrados = []
    for ruta in dispositivo.detectar():
        encontrados.append({"tipo": "kindle", "ruta": str(ruta), "nombre": ruta.name})

    usuario = os.environ.get("USER") or "adrian"
    vistos = set()
    for patron in dispositivo.LUGARES:
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
            if es_kobo(candidato):
                encontrados.append({"tipo": "kobo", "ruta": real, "nombre": candidato.name})
    return encontrados


def info(aparato):
    datos = dict(aparato)
    try:
        uso = shutil.disk_usage(aparato["ruta"])
        datos["bytes_libres"] = uso.free
        datos["bytes_totales"] = uso.total
    except OSError:
        pass
    return datos


# ------------------------------------------------------------------ Kobo
def _conectar_kobo(ruta):
    bd = Path(ruta) / ".kobo" / "KoboReader.sqlite"
    cx = sqlite3.connect(str(bd), timeout=20)
    cx.row_factory = sqlite3.Row
    return cx


def leer_kobo(ruta):
    """Los libros de la tarjeta del Kobo con su progreso."""
    salida = []
    try:
        cx = _conectar_kobo(ruta)
    except sqlite3.Error:
        return salida
    try:
        filas = cx.execute(
            "SELECT ContentID, Title, Attribution, ___PercentRead AS porcentaje, "
            "ReadStatus AS estado, ChapterIDBookmarked AS capitulo, DateLastRead AS cuando, "
            "MimeType FROM content WHERE BookID IS NULL AND ContentType = 6"
        ).fetchall()
    except sqlite3.Error:
        return salida
    finally:
        cx.close()

    for fila in filas:
        contenido = fila["ContentID"] or ""
        if not contenido.startswith("file://"):
            continue      # libros comprados en la tienda: no son de la biblioteca
        porcentaje = fila["porcentaje"] or 0
        fraccion = 1.0 if (fila["estado"] or 0) == 2 else max(0.0, min(1.0, porcentaje / 100))
        salida.append({
            "referencia": contenido,
            "fichero": os.path.basename(contenido),
            "titulo": fila["Title"] or "",
            "autor": fila["Attribution"] or "",
            "fraccion": fraccion,
            "capitulo_aparato": fila["capitulo"],
            "cuando": _fecha_kobo(fila["cuando"]),
            "estado": fila["estado"] or 0,
        })
    return salida


def _fecha_kobo(texto):
    if not texto:
        return 0
    for formato in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(texto)[:26], formato).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0


def copia_de_seguridad_kobo(ruta, carpeta_copias):
    """Antes de tocar la base de datos del Kobo, una copia. Ahí dentro está
    toda su biblioteca, no solo el progreso."""
    origen = Path(ruta) / ".kobo" / "KoboReader.sqlite"
    carpeta = Path(carpeta_copias)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"KoboReader-{datetime.now():%Y%m%d-%H%M%S}.sqlite"
    shutil.copy2(str(origen), str(destino))
    for vieja in sorted(carpeta.glob("KoboReader-*.sqlite"))[:-5]:
        vieja.unlink(missing_ok=True)
    return destino


def _capitulo_kobo(cx, referencia, numero_capitulo, ruta_interna):
    """El identificador que usa el Kobo para «por este capítulo voy».

    Primero se mira si el aparato ya tiene capítulos apuntados de ese libro y
    se coge el que toca. Si no los tiene (pasa con los EPUB normales), se
    construye con el patrón que usa el propio Kobo:
    `<libro>#(<número>)<ruta dentro del epub>`.
    """
    try:
        filas = cx.execute(
            "SELECT ContentID FROM content WHERE ContentType = 899 AND BookID = ? "
            "ORDER BY VolumeIndex", (referencia,)
        ).fetchall()
    except sqlite3.Error:
        filas = []
    if filas and 0 <= numero_capitulo < len(filas):
        return filas[numero_capitulo]["ContentID"]
    if ruta_interna:
        return f"{referencia}#({numero_capitulo}){ruta_interna}"
    return None


def escribir_kobo(ruta, cambios):
    """Lleva al Kobo las posiciones que van más adelantadas en la aplicación."""
    if not cambios:
        return 0
    cx = _conectar_kobo(ruta)
    hechos = 0
    try:
        cx.execute("BEGIN IMMEDIATE")
        for cambio in cambios:
            porcentaje = int(round(max(0.0, min(1.0, cambio["fraccion"])) * 100))
            estado = 2 if porcentaje >= 99 else 1
            capitulo = _capitulo_kobo(
                cx, cambio["referencia"], cambio.get("capitulo", 0),
                cambio.get("ruta_interna"),
            )
            campos = ["___PercentRead = ?", "ReadStatus = ?", "FirstTimeReading = 'false'",
                      "DateLastRead = ?"]
            valores = [porcentaje, estado,
                       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]
            if capitulo:
                campos.append("ChapterIDBookmarked = ?")
                valores.append(capitulo)
                # La posición fina dentro del capítulo se deja en blanco: el
                # Kobo abre por el principio del capítulo, que es lo honesto
                # cuando la posición viene de otro aparato.
                campos.append("adobe_location = NULL")
            cx.execute(
                f"UPDATE content SET {', '.join(campos)} "
                "WHERE ContentID = ? AND BookID IS NULL",
                valores + [cambio["referencia"]],
            )
            hechos += cx.total_changes and 1 or 0
        cx.execute("COMMIT")
    except sqlite3.Error:
        cx.execute("ROLLBACK")
        raise
    finally:
        cx.close()
    return hechos


# ---------------------------------------------------------------- Kindle
def leer_kindle(ruta):
    """Lo que se puede sacar del Kindle: los `.mbp` de los modelos antiguos.

    La lectura del `.mbp` está portada de Calibre (GPL-3), que lleva años con
    ese formato descifrado. De los `.azw3r` de los Kindle modernos no se puede
    sacar la posición: se devuelven marcados como «no legible» para poder
    decírselo al usuario en vez de callar.
    """
    documentos = Path(ruta) / "documents"
    salida = []
    if not documentos.is_dir():
        return salida

    for libro in sorted(documentos.rglob("*")):
        if not libro.is_file() or libro.suffix.lower() not in (
                ".azw3", ".azw", ".mobi", ".prc"):
            continue
        carpeta_datos = libro.with_suffix(".sdr")
        marcadores = []
        if carpeta_datos.is_dir():
            marcadores = [m for m in carpeta_datos.iterdir()
                          if m.suffix.lower() in EXTENSIONES_MARCADOR]
        marcadores += [libro.with_suffix(ext) for ext in (".mbp", ".pds", ".tan")
                       if libro.with_suffix(ext).is_file()]

        entrada = {
            "referencia": str(libro),
            "fichero": libro.name,
            "titulo": libro.stem,
            "autor": "",
            "fraccion": None,
            "cuando": 0,
            "legible": False,
        }
        for marcador in marcadores:
            posicion = _posicion_mbp(marcador) if marcador.suffix.lower() in (".mbp", ".mbp1") else None
            if posicion is None:
                continue
            tamano = libro.stat().st_size or 1
            entrada["fraccion"] = max(0.0, min(1.0, posicion[0] / tamano))
            entrada["cuando"] = posicion[1] or marcador.stat().st_mtime
            entrada["localizacion"] = posicion[0] // BYTES_POR_LOCALIZACION + 1
            entrada["legible"] = True
            break
        if not entrada["legible"] and marcadores:
            entrada["motivo"] = (
                "El Kindle guarda aquí la posición en un formato suyo que no está "
                "descifrado (.azw3r). Se puede mandar la posición al revés, pero no leerla."
            )
        salida.append(entrada)
    return salida


def _posicion_mbp(ruta):
    """Devuelve (byte donde se quedó, cuándo) de un marcador .mbp."""
    try:
        datos = Path(ruta).read_bytes()
        if len(datos) < 0x52:
            return None
        (marca,) = struct.unpack(">I", datos[0x24:0x28])
        (inicio,) = struct.unpack(">I", datos[0x4E:0x52])
        posicion_lectura = inicio + 0x0C
        if posicion_lectura + 4 > len(datos):
            return None
        (donde,) = struct.unpack(">I", datos[posicion_lectura:posicion_lectura + 4])
        # La marca de tiempo del Kindle cuenta desde 1970 pero en su propio
        # formato; si sale disparatada se usa la del fichero.
        cuando = marca if 0 < marca < 4102444800 else 0
        return donde, cuando
    except (OSError, struct.error):
        return None


# ------------------------------------------------------------ emparejar
def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", (texto or "").lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def emparejar(catalogo, entradas):
    """Casa lo que hay en el aparato con los libros de la biblioteca.

    Primero por el nombre del fichero, que es exactamente el que puso
    Vidalibre al mandarlo; y si no, por título y autor.
    """
    por_nombre, por_titulo = {}, {}
    with catalogo.conectar() as cx:
        for fila in cx.execute(
            "SELECT d.book AS libro, d.name AS nombre, b.title AS titulo, b.author_sort AS autor "
            "FROM data d JOIN books b ON b.id = d.book"
        ):
            por_nombre.setdefault(_normalizar(fila["nombre"]), fila["libro"])
            clave = _normalizar(fila["titulo"])[:60]
            por_titulo.setdefault(clave, fila["libro"])

    emparejados = []
    for entrada in entradas:
        base = os.path.splitext(entrada["fichero"])[0]
        id_libro = por_nombre.get(_normalizar(base))
        if not id_libro:
            id_libro = por_titulo.get(_normalizar(entrada.get("titulo"))[:60])
        if not id_libro and entrada.get("titulo"):
            objetivo = _normalizar(entrada["titulo"])[:40]
            for clave, valor in por_titulo.items():
                if objetivo and clave.startswith(objetivo):
                    id_libro = valor
                    break
        if id_libro:
            emparejados.append((entrada, id_libro))
    return emparejados


# ----------------------------------------------------------- comparación
def plan_de_sincronizacion(catalogo, aparato, cache_libros=None, margen=0.01):
    """Qué habría que hacer con cada libro. No toca nada: solo lo cuenta.

    `margen` es la diferencia a partir de la cual merece la pena mover algo:
    por debajo de un 1% del libro se considera que van iguales.
    """
    tipo = aparato["tipo"]
    entradas = leer_kobo(aparato["ruta"]) if tipo == "kobo" else leer_kindle(aparato["ruta"])
    emparejados = emparejar(catalogo, entradas)

    plan = {"aparato": aparato, "al_aparato": [], "a_la_app": [], "iguales": [],
            "sin_emparejar": len(entradas) - len(emparejados), "ilegibles": []}

    for entrada, id_libro in emparejados:
        libro = catalogo.libro(id_libro)
        if not libro:
            continue
        guardada = lector.leer_posicion(catalogo, id_libro, "EPUB") or {}
        fraccion_app = float(guardada.get("fraccion") or 0)
        fraccion_aparato = entrada.get("fraccion")

        if fraccion_aparato is None:
            plan["ilegibles"].append({
                "libro": id_libro, "titulo": libro["titulo"],
                "motivo": entrada.get("motivo", "No se ha podido leer la posición."),
                "fraccion_app": fraccion_app,
            })
            continue

        linea = {
            "libro": id_libro,
            "titulo": libro["titulo"],
            "autor": ", ".join(libro["autores"]),
            "referencia": entrada["referencia"],
            "fraccion_app": fraccion_app,
            "fraccion_aparato": fraccion_aparato,
            # Por qué capítulo iba según el propio aparato. Vale mucho más que
            # el porcentaje: ver `posicion_desde_fraccion`.
            "capitulo_aparato": entrada.get("capitulo_aparato"),
        }
        if fraccion_aparato > fraccion_app + margen:
            linea["fraccion"] = fraccion_aparato
            plan["a_la_app"].append(linea)
        elif fraccion_app > fraccion_aparato + margen and tipo == "kobo":
            linea["fraccion"] = fraccion_app
            plan["al_aparato"].append(linea)
        else:
            plan["iguales"].append(linea)

    return plan


# El Kobo guarda el porcentaje leído como un número ENTERO. Eso suena a
# detalle sin importancia y no lo es: en un libro de 243.000 palabras partido
# en 99 capítulos, un 1 % son unas 2.400 palabras, o sea **un capítulo entero**.
# Si el aparato redondea 11,6 a 12, aterrizas un capítulo por delante de donde
# ibas. Pasó justo eso. Por eso el porcentaje es el último recurso.
MEDIO_PUNTO = 0.005


def capitulo_del_marcador(epub, marcador):
    """Por qué capítulo iba el Kobo, según el propio Kobo.

    El aparato guarda en `ChapterIDBookmarked` la ruta del capítulo dentro del
    EPUB, con la forma `…/libro.epub#(12)OEBPS/Text/cap12.xhtml`. Esa ruta es
    **exacta**, mientras que el porcentaje viene redondeado a entero. Siempre
    que se pueda casar, manda esta.
    """
    if not marcador:
        return None
    texto = str(marcador)
    # Quedarse con la ruta interna: lo que va detrás del `#(n)`, o del `#`.
    if "#" in texto:
        texto = texto.split("#", 1)[1]
        if texto.startswith("(") and ")" in texto:
            texto = texto.split(")", 1)[1]
    texto = urllib.parse.unquote(texto).strip().lstrip("/")
    if not texto:
        return None

    # Casar por el final de la ruta: el Kobo a veces la guarda completa y a
    # veces relativa a sitios distintos del EPUB.
    nombre = texto.rsplit("/", 1)[-1].split("?")[0]
    mejor = None
    for numero, capitulo in enumerate(epub.capitulos):
        ruta = (capitulo.get("ruta") or "").lstrip("/")
        if not ruta:
            continue
        if ruta == texto:
            return numero
        if ruta.endswith("/" + texto) or texto.endswith("/" + ruta):
            return numero
        if mejor is None and ruta.rsplit("/", 1)[-1] == nombre:
            mejor = numero
    return mejor


def posicion_desde_fraccion(catalogo, id_libro, fraccion, cache_libros,
                            marcador=None):
    """Convierte «vas por el 37 % del libro» en capítulo y página.

    Con `marcador` (lo que el Kobo apunta como capítulo actual) se usa ese
    capítulo tal cual, que es dato exacto. Sin él se cae al porcentaje, que
    está redondeado a entero: se le resta medio punto para no pasarse de
    capítulo, porque quedarse un poco corto se arregla pasando página y
    pasarse obliga a buscar por dónde ibas.
    """
    libro = catalogo.libro(id_libro)
    if not libro:
        return None, None
    ruta = ficheros.ruta_fichero(catalogo.raiz, libro, "EPUB")
    if not ruta or not ruta.is_file():
        return None, None
    try:
        epub = cache_libros.abrir(ruta)
    except Exception:
        return None, None

    objetivo = max(0.0, min(1.0, fraccion))

    elegido = capitulo_del_marcador(epub, marcador)
    if elegido is not None:
        # El capítulo lo dice el aparato; dentro de él, el porcentaje sirve
        # para no empezar siempre desde la primera línea.
        capitulo = epub.capitulos[elegido]
        inicio = capitulo.get("fraccion", 0)
        ancho = capitulo.get("palabras", 0) / max(1, epub.palabras)
        dentro = (objetivo - inicio) / ancho if ancho > 0 else 0.0
        # Si el porcentaje redondeado cae fuera del capítulo que dice el
        # aparato, no sabemos por dónde iba dentro de él: se empieza por el
        # principio. Dejarlo al final sería saltarse el capítulo entero.
        if not 0.0 <= dentro <= 1.0:
            dentro = 0.0
    else:
        objetivo = max(0.0, objetivo - MEDIO_PUNTO)
        elegido, dentro = 0, 0.0
        for numero, capitulo in enumerate(epub.capitulos):
            inicio = capitulo.get("fraccion", 0)
            palabras = capitulo.get("palabras", 0) / max(1, epub.palabras)
            if objetivo >= inicio:
                elegido = numero
                dentro = (objetivo - inicio) / palabras if palabras > 0 else 0
            else:
                break

    dentro = max(0.0, min(1.0, dentro))
    ruta_interna = epub.capitulos[elegido]["ruta"] if elegido < len(epub.capitulos) else None
    return f"c{elegido}:{dentro:.4f}", {"capitulo": elegido, "ruta_interna": ruta_interna}


def aplicar(catalogo, plan, cache_libros, carpeta_copias, puente=None):
    """Ejecuta el plan: lo más avanzado gana, en los dos sentidos."""
    resultado = {"a_la_app": 0, "al_aparato": 0, "errores": []}

    # 1) Del aparato a la aplicación.
    for linea in plan["a_la_app"]:
        posicion, _extra = posicion_desde_fraccion(
            catalogo, linea["libro"], linea["fraccion"], cache_libros,
            marcador=linea.get("capitulo_aparato"))
        if posicion is None:
            posicion = f"c0:0"
        try:
            if puente:
                puente.guardar_posicion(linea["libro"], "EPUB", posicion, linea["fraccion"])
            else:
                lector.guardar_posicion(catalogo, linea["libro"], "EPUB",
                                        posicion, linea["fraccion"])
            resultado["a_la_app"] += 1
        except Exception as ex:
            resultado["errores"].append(f"{linea['titulo']}: {ex}")

    # 2) De la aplicación al aparato (solo el Kobo deja escribir).
    if plan["al_aparato"] and plan["aparato"]["tipo"] == "kobo":
        copia_de_seguridad_kobo(plan["aparato"]["ruta"], carpeta_copias)
        cambios = []
        for linea in plan["al_aparato"]:
            _posicion, extra = posicion_desde_fraccion(
                catalogo, linea["libro"], linea["fraccion"], cache_libros)
            cambios.append({
                "referencia": linea["referencia"],
                "fraccion": linea["fraccion"],
                "capitulo": (extra or {}).get("capitulo", 0),
                "ruta_interna": (extra or {}).get("ruta_interna"),
            })
        try:
            escribir_kobo(plan["aparato"]["ruta"], cambios)
            resultado["al_aparato"] = len(cambios)
            os.sync()
        except Exception as ex:
            resultado["errores"].append(f"No he podido escribir en el Kobo: {ex}")

    return resultado


# ------------------------------------------------------- mandar libros al Kobo
FORMATOS_KOBO = ["epub", "pdf", "txt", "cbz"]


def copiar_al_kobo(fichero, ruta_aparato, nombre_destino=None, subcarpeta="Vidalibre"):
    """Deja un libro en el Kobo.

    El Kobo no necesita que le preparen nada: en cuanto se desconecta, repasa
    su memoria, encuentra lo nuevo y se lo añade a la biblioteca con su portada.
    Se deja en una carpeta propia para no mezclarlo con lo que baje de su
    tienda.
    """
    destino_dir = Path(ruta_aparato) / subcarpeta if subcarpeta else Path(ruta_aparato)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / (nombre_destino or Path(fichero).name)
    shutil.copy2(str(fichero), str(destino))
    try:
        os.sync()
    except Exception:
        pass
    return destino


def libros_en_el_aparato(aparato, maximo=400):
    """Lo que ya lleva dentro, para enseñarlo."""
    if aparato["tipo"] == "kindle":
        return dispositivo.libros(Path(aparato["ruta"]), maximo=maximo)
    salida = []
    raiz = Path(aparato["ruta"])
    for ruta in sorted(raiz.rglob("*")):
        if len(salida) >= maximo:
            break
        if not ruta.is_file() or ruta.suffix.lower().lstrip(".") not in FORMATOS_KOBO:
            continue
        if ".kobo" in ruta.parts or ruta.name.startswith("."):
            continue
        salida.append({
            "nombre": ruta.stem,
            "formato": ruta.suffix.lower().lstrip("."),
            "bytes": ruta.stat().st_size,
            "ruta": str(ruta),
            "relativa": str(ruta.relative_to(raiz)),
        })
    return salida
