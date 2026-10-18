"""El lector: abrir un EPUB y servirlo capítulo a capítulo.

Por qué está hecho así y no con una biblioteca de JavaScript (epub.js y
compañía, que es lo que usa Calibre-Web): esos lectores se descargan el libro
**entero** al navegador, lo descomprimen en memoria y lo pintan. Con un libro
de 12 MB eso es medio móvil ocupado y un arranque de varios segundos por el
WiFi. Aquí el EPUB se abre en el NAS, que lee **solo el capítulo que toca**
dentro del zip (un EPUB es un zip) y manda ese trozo de HTML ya preparado. El
iPhone recibe unas decenas de kilobytes por capítulo y el NAS no carga nunca el
libro completo: las dos quejas de memoria, resueltas por el mismo lado.

Lo que hace este módulo:
  - leer la estructura del libro (qué capítulos hay y en qué orden, el índice),
  - sacar un capítulo convertido en HTML seguro, con las imágenes y las hojas
    de estilo apuntando a una dirección que las sirve desde dentro del zip,
  - buscar texto en todo el libro,
  - y guardar por dónde se iba, en la tabla que Calibre ya trae para eso.

Nada de esto toca el fichero del libro: se abre en modo lectura y se cierra.
"""

from __future__ import annotations

import html
import os
import posixpath
import re
import threading
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree

NS_CONTENEDOR = "{urn:oasis:names:tc:opendocument:xmlns:container}"
NS_OPF = "{http://www.idpf.org/2007/opf}"
NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_NCX = "{http://www.daisy.org/z3986/2005/ncx/}"
NS_XHTML = "{http://www.w3.org/1999/xhtml}"

PALABRAS_POR_MINUTO = 230

TIPOS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".css": "text/css", ".js": "text/javascript", ".xhtml": "application/xhtml+xml",
    ".html": "text/html", ".htm": "text/html", ".ttf": "font/ttf",
    ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".ncx": "application/x-dtbncx+xml",
}

# Lo que se quita del HTML del libro antes de enseñarlo. Un EPUB puede traer
# JavaScript dentro, y aquí se sirve desde el mismo sitio que la aplicación.
_RE_PELIGROSO = re.compile(
    r"(?is)<(script|iframe|object|embed|form|noscript)[^>]*>.*?</\1>|"
    r"<(script|iframe|object|embed|form|noscript|base)[^>]*/?>")
_RE_EVENTOS = re.compile(r"""(?is)\son\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""")
_RE_CUERPO = re.compile(r"(?is)<body[^>]*>(.*)</body>")
_RE_ETIQUETA_CUERPO = re.compile(r"(?is)<body([^>]*)>")
_RE_HOJAS = re.compile(r"""(?is)<link[^>]+rel\s*=\s*["']?stylesheet["']?[^>]*>""")
_RE_HREF = re.compile(r"""(?is)\shref\s*=\s*["']([^"']+)["']""")
_RE_ESTILO_INTERNO = re.compile(r"(?is)<style[^>]*>(.*?)</style>")
_RE_ATRIBUTO_RUTA = re.compile(
    r"""(?is)\s(src|href|xlink:href|poster|data-src)\s*=\s*["']([^"']+)["']""")
_RE_URL_CSS = re.compile(r"""(?is)url\(\s*["']?([^"')]+)["']?\s*\)""")
_RE_ETIQUETAS = re.compile(r"(?s)<[^>]+>")
_RE_ESPACIOS = re.compile(r"\s+")


class ErrorLector(Exception):
    pass


def _contar_palabras(fragmento_html):
    """Cuenta palabras rápido: quitar etiquetas y partir por espacios.

    Deliberadamente no se descodifican las entidades ni se normalizan los
    espacios, que es lo caro: para saber cuántos minutos queda de capítulo no
    hace falta precisión de notario, y hacerlo bien multiplicaba por tres el
    tiempo de abrir un libro.
    """
    return len(_RE_ETIQUETAS.sub(" ", fragmento_html or "").split())


def _texto_plano(fragmento_html):
    texto = _RE_PELIGROSO.sub(" ", fragmento_html or "")
    texto = _RE_ESTILO_INTERNO.sub(" ", texto)
    texto = _RE_ETIQUETAS.sub(" ", texto)
    return _RE_ESPACIOS.sub(" ", html.unescape(texto)).strip()


class LibroEpub:
    """Un EPUB abierto: su índice, sus capítulos y sus ficheros internos."""

    def __init__(self, ruta, carpeta_medidas=None):
        self.ruta = Path(ruta)
        if not self.ruta.is_file():
            raise ErrorLector("El fichero del libro no está en el disco.")
        datos = self.ruta.stat()
        self.marca = datos.st_mtime
        self.tamano = datos.st_size
        self.carpeta_medidas = Path(carpeta_medidas) if carpeta_medidas else None
        with zipfile.ZipFile(self.ruta) as zip_libro:
            self._cargar(zip_libro)

    # ------------------------------------------------------------- estructura
    def _cargar(self, zip_libro):
        try:
            contenedor = ElementTree.fromstring(zip_libro.read("META-INF/container.xml"))
        except (KeyError, ElementTree.ParseError) as ex:
            raise ErrorLector("Esto no parece un EPUB válido.") from ex

        raiz = contenedor.find(f".//{NS_CONTENEDOR}rootfile")
        if raiz is None or not raiz.get("full-path"):
            raise ErrorLector("El EPUB no dice dónde tiene su índice interno.")
        self.ruta_opf = raiz.get("full-path")
        self.base = posixpath.dirname(self.ruta_opf)

        opf = ElementTree.fromstring(zip_libro.read(self.ruta_opf))
        metadatos = opf.find(f"{NS_OPF}metadata")
        self.titulo = self._primero(metadatos, f"{NS_DC}title") or self.ruta.stem
        self.autor = self._primero(metadatos, f"{NS_DC}creator") or ""
        self.idioma = (self._primero(metadatos, f"{NS_DC}language") or "es")[:5]

        # Manifiesto: cada pieza del libro con su identificador.
        self.manifiesto = {}
        for elemento in opf.findall(f"{NS_OPF}manifest/{NS_OPF}item"):
            self.manifiesto[elemento.get("id")] = {
                "ruta": self._absoluta(elemento.get("href", "")),
                "tipo": elemento.get("media-type", ""),
                "propiedades": elemento.get("properties", ""),
            }

        # Lomo: el orden de lectura.
        lomo = opf.find(f"{NS_OPF}spine")
        self.capitulos = []
        for elemento in (lomo.findall(f"{NS_OPF}itemref") if lomo is not None else []):
            pieza = self.manifiesto.get(elemento.get("idref"))
            if not pieza or "html" not in pieza["tipo"]:
                continue
            self.capitulos.append({"ruta": pieza["ruta"], "id": elemento.get("idref")})

        if not self.capitulos:
            raise ErrorLector("El EPUB no tiene capítulos que enseñar.")

        self.indice = self._leer_indice(zip_libro, opf, lomo)
        self._medir(zip_libro)

    @staticmethod
    def _primero(nodo, etiqueta):
        if nodo is None:
            return ""
        encontrado = nodo.find(etiqueta)
        return (encontrado.text or "").strip() if encontrado is not None else ""

    def _absoluta(self, href):
        href = (href or "").split("#")[0]
        href = html.unescape(href)
        from urllib.parse import unquote
        href = unquote(href)
        return posixpath.normpath(posixpath.join(self.base, href)).lstrip("/")

    def _indice_de_ruta(self, ruta):
        for numero, capitulo in enumerate(self.capitulos):
            if capitulo["ruta"] == ruta:
                return numero
        return None

    def _leer_indice(self, zip_libro, opf, lomo):
        """El índice del libro: EPUB 3 lo trae en un `nav`, EPUB 2 en un `ncx`."""
        entradas = []

        # EPUB 3
        for identificador, pieza in self.manifiesto.items():
            if "nav" in (pieza["propiedades"] or ""):
                try:
                    documento = zip_libro.read(pieza["ruta"]).decode("utf-8", "replace")
                except KeyError:
                    continue
                base_nav = posixpath.dirname(pieza["ruta"])
                bloque = re.search(
                    r"""(?is)<nav[^>]*epub:type\s*=\s*["'][^"']*toc[^"']*["'][^>]*>(.*?)</nav>""",
                    documento)
                if bloque:
                    for href, texto in re.findall(
                            r"""(?is)<a[^>]+href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
                            bloque.group(1)):
                        entradas.append(self._entrada_indice(href, texto, base_nav))
                if entradas:
                    return [e for e in entradas if e]

        # EPUB 2 (ncx)
        ruta_ncx = None
        if lomo is not None and lomo.get("toc"):
            pieza = self.manifiesto.get(lomo.get("toc"))
            ruta_ncx = pieza["ruta"] if pieza else None
        if not ruta_ncx:
            for pieza in self.manifiesto.values():
                if pieza["tipo"] == "application/x-dtbncx+xml":
                    ruta_ncx = pieza["ruta"]
                    break
        if ruta_ncx:
            try:
                ncx = ElementTree.fromstring(zip_libro.read(ruta_ncx))
            except (KeyError, ElementTree.ParseError):
                return []
            base_ncx = posixpath.dirname(ruta_ncx)
            for punto in ncx.iter(f"{NS_NCX}navPoint"):
                etiqueta = punto.find(f"{NS_NCX}navLabel/{NS_NCX}text")
                contenido = punto.find(f"{NS_NCX}content")
                if contenido is None:
                    continue
                entradas.append(self._entrada_indice(
                    contenido.get("src", ""), (etiqueta.text if etiqueta is not None else ""),
                    base_ncx))
        return [e for e in entradas if e]

    def _entrada_indice(self, href, texto, base):
        href = html.unescape(href or "")
        ancla = href.split("#", 1)[1] if "#" in href else ""
        from urllib.parse import unquote
        destino = posixpath.normpath(
            posixpath.join(base, unquote(href.split("#")[0]))).lstrip("/")
        numero = self._indice_de_ruta(destino)
        if numero is None:
            return None
        titulo = _RE_ESPACIOS.sub(" ", html.unescape(_RE_ETIQUETAS.sub("", texto or ""))).strip()
        return {"titulo": titulo or f"Capítulo {numero + 1}", "capitulo": numero, "ancla": ancla}

    def _fichero_medidas(self):
        if not self.carpeta_medidas:
            return None
        import hashlib

        clave = hashlib.sha1(
            f"{self.ruta}|{int(self.marca)}|{self.tamano}".encode("utf-8")
        ).hexdigest()[:20]
        return self.carpeta_medidas / f"{clave}.json"

    def _medir(self, zip_libro):
        """Cuenta las palabras de cada capítulo: es lo que permite decir «te
        quedan 12 minutos» y calcular el porcentaje del libro de verdad, no por
        número de capítulo (hay capítulos de dos páginas y de cincuenta).

        Es lo único caro de abrir un libro (hay que leer todo el texto), así
        que el resultado se guarda en disco. La segunda vez que se abre ese
        libro, abrirlo es instantáneo. Si el fichero cambia, cambia la clave y
        se vuelve a medir solo.
        """
        import json

        fichero = self._fichero_medidas()
        cuentas = None
        if fichero and fichero.is_file():
            try:
                guardado = json.loads(fichero.read_text(encoding="utf-8"))
                if len(guardado) == len(self.capitulos):
                    cuentas = guardado
            except (OSError, ValueError):
                cuentas = None

        if cuentas is None:
            cuentas = []
            for capitulo in self.capitulos:
                try:
                    crudo = zip_libro.read(capitulo["ruta"]).decode("utf-8", "replace")
                except KeyError:
                    cuentas.append(0)
                    continue
                cuerpo = _RE_CUERPO.search(crudo)
                cuentas.append(_contar_palabras(cuerpo.group(1) if cuerpo else crudo))
            if fichero:
                try:
                    fichero.parent.mkdir(parents=True, exist_ok=True)
                    fichero.write_text(json.dumps(cuentas), encoding="utf-8")
                except OSError:
                    pass

        total = 0
        for capitulo, palabras in zip(self.capitulos, cuentas):
            capitulo["palabras"] = palabras
            capitulo["desde"] = total
            total += palabras
        self.palabras = max(1, total)
        for capitulo in self.capitulos:
            capitulo.setdefault("desde", 0)
            capitulo["fraccion"] = capitulo["desde"] / self.palabras

    # --------------------------------------------------------------- capítulos
    def capitulo(self, numero, url_recurso):
        """Devuelve el HTML de un capítulo, ya limpio y con las rutas internas
        apuntando a donde las sirve la aplicación."""
        if not 0 <= numero < len(self.capitulos):
            raise ErrorLector("Ese capítulo no existe.")
        pieza = self.capitulos[numero]
        with zipfile.ZipFile(self.ruta) as zip_libro:
            crudo = zip_libro.read(pieza["ruta"]).decode("utf-8", "replace")
        carpeta = posixpath.dirname(pieza["ruta"])

        hojas = []
        for etiqueta in _RE_HOJAS.findall(crudo):
            href = _RE_HREF.search(etiqueta)
            if href:
                hojas.append(url_recurso(self._resolver(carpeta, href.group(1))))
        estilos_internos = "\n".join(
            self._arreglar_css(bloque, carpeta, url_recurso)
            for bloque in _RE_ESTILO_INTERNO.findall(crudo)
        )

        cuerpo = _RE_CUERPO.search(crudo)
        contenido = cuerpo.group(1) if cuerpo else crudo
        atributos = _RE_ETIQUETA_CUERPO.search(crudo)
        clase_cuerpo = ""
        if atributos:
            clase = re.search(r"""(?is)class\s*=\s*["']([^"']*)["']""", atributos.group(1))
            clase_cuerpo = clase.group(1) if clase else ""

        contenido = _RE_PELIGROSO.sub("", contenido)
        contenido = _RE_EVENTOS.sub("", contenido)
        contenido = self._arreglar_rutas(contenido, carpeta, url_recurso)

        return {
            "numero": numero,
            "total": len(self.capitulos),
            "html": contenido,
            "hojas": hojas,
            "estilos": estilos_internos,
            "clase": clase_cuerpo,
            "palabras": pieza.get("palabras", 0),
            "minutos": max(1, round(pieza.get("palabras", 0) / PALABRAS_POR_MINUTO)),
            "fraccion": pieza.get("fraccion", 0),
            "idioma": self.idioma,
            "titulo": self._titulo_de(numero),
        }

    def _titulo_de(self, numero):
        titulo = ""
        for entrada in self.indice:
            if entrada["capitulo"] <= numero:
                titulo = entrada["titulo"]
            else:
                break
        return titulo

    def _resolver(self, carpeta, href):
        from urllib.parse import unquote
        href = unquote(html.unescape(href or "")).split("#")[0]
        return posixpath.normpath(posixpath.join(carpeta, href)).lstrip("/")

    def _arreglar_rutas(self, fragmento, carpeta, url_recurso):
        """Cambia las rutas internas del EPUB por direcciones que sirve la
        aplicación, y los enlaces entre capítulos por saltos del lector."""
        def cambiar(coincidencia):
            atributo, valor = coincidencia.group(1), coincidencia.group(2)
            if valor.startswith(("http://", "https://", "mailto:", "data:", "#")):
                if valor.startswith("#"):
                    return coincidencia.group(0)
                # Un enlace a fuera: que no se lo lleve por delante al lector.
                if atributo == "href":
                    return f' {atributo}="{valor}" target="_blank" rel="noopener"'
                return coincidencia.group(0)
            destino = self._resolver(carpeta, valor)
            ancla = valor.split("#", 1)[1] if "#" in valor else ""
            if atributo == "href":
                numero = self._indice_de_ruta(destino)
                if numero is not None:
                    return f' href="#" data-capitulo="{numero}" data-ancla="{html.escape(ancla)}"'
                return f' href="{url_recurso(destino)}" target="_blank" rel="noopener"'
            return f' {atributo}="{url_recurso(destino)}"'

        fragmento = _RE_ATRIBUTO_RUTA.sub(cambiar, fragmento)
        return _RE_URL_CSS.sub(
            lambda m: f"url({url_recurso(self._resolver(carpeta, m.group(1)))})"
            if not m.group(1).startswith(("http", "data:")) else m.group(0),
            fragmento,
        )

    def _arreglar_css(self, css, carpeta, url_recurso):
        return _RE_URL_CSS.sub(
            lambda m: f"url({url_recurso(self._resolver(carpeta, m.group(1)))})"
            if not m.group(1).startswith(("http", "data:")) else m.group(0),
            css or "",
        )

    def recurso(self, ruta):
        """Un fichero de dentro del EPUB (imagen, hoja de estilo, tipografía)."""
        ruta = posixpath.normpath(ruta).lstrip("/")
        if ruta.startswith(".."):
            raise ErrorLector("Ruta no válida.")
        with zipfile.ZipFile(self.ruta) as zip_libro:
            try:
                datos = zip_libro.read(ruta)
            except KeyError:
                raise ErrorLector("Ese fichero no está dentro del libro.") from None
        tipo = TIPOS.get(os.path.splitext(ruta)[1].lower(), "application/octet-stream")
        if tipo == "text/css":
            texto = datos.decode("utf-8", "replace")
            carpeta = posixpath.dirname(ruta)
            datos = self._arreglar_css(
                texto, carpeta, lambda r: f"__RECURSO__{r}").encode("utf-8")
        return datos, tipo

    def buscar(self, texto, maximo=60):
        """Busca en todo el libro. Lee de capítulo en capítulo: en ningún
        momento tiene el libro entero en memoria."""
        aguja = (texto or "").strip().lower()
        if len(aguja) < 3:
            return []
        resultados = []
        with zipfile.ZipFile(self.ruta) as zip_libro:
            for numero, capitulo in enumerate(self.capitulos):
                try:
                    crudo = zip_libro.read(capitulo["ruta"]).decode("utf-8", "replace")
                except KeyError:
                    continue
                cuerpo = _RE_CUERPO.search(crudo)
                plano = _texto_plano(cuerpo.group(1) if cuerpo else crudo)
                minusculas = plano.lower()
                desde = 0
                while len(resultados) < maximo:
                    posicion = minusculas.find(aguja, desde)
                    if posicion < 0:
                        break
                    inicio = max(0, posicion - 70)
                    fin = min(len(plano), posicion + len(aguja) + 90)
                    resultados.append({
                        "capitulo": numero,
                        "titulo": self._titulo_de(numero),
                        "fragmento": ("…" if inicio else "") + plano[inicio:fin] + ("…" if fin < len(plano) else ""),
                        "posicion": posicion / max(1, len(plano)),
                    })
                    desde = posicion + len(aguja)
                if len(resultados) >= maximo:
                    break
        return resultados


# ------------------------------------------------------------------- memoria
class CacheLibros:
    """Guarda abiertos los dos o tres últimos libros leídos.

    Abrir un EPUB cuesta leer su índice y contar sus palabras; hacerlo en cada
    página sería absurdo. Pero tampoco se guardan muchos: cada uno ocupa unos
    pocos kilobytes de estructura, no el libro.
    """

    def __init__(self, maximo=3, carpeta_medidas=None):
        self.maximo = maximo
        self.carpeta_medidas = carpeta_medidas
        self._libros = {}
        self._lock = threading.Lock()

    def abrir(self, ruta):
        ruta = str(ruta)
        marca = Path(ruta).stat().st_mtime if Path(ruta).is_file() else 0
        with self._lock:
            guardado = self._libros.get(ruta)
            if guardado and guardado[0].marca == marca:
                self._libros[ruta] = (guardado[0], time.time())
                return guardado[0]
        libro = LibroEpub(ruta, carpeta_medidas=self.carpeta_medidas)
        with self._lock:
            self._libros[ruta] = (libro, time.time())
            if len(self._libros) > self.maximo:
                viejo = min(self._libros.items(), key=lambda par: par[1][1])[0]
                self._libros.pop(viejo, None)
        return libro

    def olvidar(self, ruta=None):
        with self._lock:
            if ruta is None:
                self._libros.clear()
            else:
                self._libros.pop(str(ruta), None)


# ------------------------------------------------------- por dónde iba la lectura
def guardar_posicion(catalogo, id_libro, formato, posicion, fraccion,
                     usuario="vidalibre", aparato="web"):
    """Guarda el punto de lectura en la tabla que Calibre ya trae para eso
    (`last_read_positions`), así que no hace falta base de datos aparte."""
    with catalogo.escribir() as cx:
        cx.execute(
            "INSERT INTO last_read_positions (book, format, user, device, cfi, epoch, pos_frac) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user, device, book, format) DO UPDATE SET "
            "cfi = excluded.cfi, epoch = excluded.epoch, pos_frac = excluded.pos_frac",
            (id_libro, formato.upper(), usuario, aparato, posicion, time.time(),
             max(0.0, min(1.0, float(fraccion or 0)))),
        )


def leer_posicion(catalogo, id_libro, formato, usuario="vidalibre", aparato="web"):
    with catalogo.conectar() as cx:
        fila = cx.execute(
            "SELECT cfi, pos_frac, epoch FROM last_read_positions "
            "WHERE book = ? AND UPPER(format) = ? AND user = ? AND device = ?",
            (id_libro, formato.upper(), usuario, aparato),
        ).fetchone()
    if not fila:
        return None
    return {"posicion": fila["cfi"], "fraccion": fila["pos_frac"], "cuando": fila["epoch"]}


def en_marcha(catalogo, limite=12, usuario="vidalibre", aparato="web"):
    """Los libros empezados y sin terminar, el último leído primero.

    Es lo que alimenta el «continuar leyendo» de la portada. Se dejan fuera los
    que apenas se han abierto (por debajo del 1 %) y los ya acabados: ni unos ni
    otros son algo que se quiera retomar.
    """
    with catalogo.conectar() as cx:
        filas = cx.execute(
            "SELECT book, MAX(pos_frac) AS fraccion, MAX(epoch) AS cuando "
            "FROM last_read_positions WHERE user = ? AND device = ? "
            "GROUP BY book HAVING fraccion > 0.01 AND fraccion < 0.985 "
            "ORDER BY cuando DESC LIMIT ?",
            (usuario, aparato, int(limite)),
        ).fetchall()
    return [{"id": f["book"], "fraccion": f["fraccion"], "cuando": f["cuando"]} for f in filas]


def posiciones_de_libros(catalogo, ids, usuario="vidalibre", aparato="web"):
    """El porcentaje leído de varios libros de golpe, para pintarlo en la rejilla."""
    if not ids:
        return {}
    huecos = ",".join("?" * len(ids))
    with catalogo.conectar() as cx:
        return {
            fila["book"]: fila["pos_frac"]
            for fila in cx.execute(
                f"SELECT book, MAX(pos_frac) AS pos_frac FROM last_read_positions "
                f"WHERE user = ? AND device = ? AND book IN ({huecos}) GROUP BY book",
                [usuario, aparato] + list(ids),
            )
        }
