"""Acceso al catálogo de Calibre (metadata.db).

Vidalibre no tiene base de datos propia para los libros: lee y escribe el mismo
`metadata.db` que usa Calibre y que ya usaba Calibre-Web. Así la biblioteca
sigue siendo válida para los dos programas y la migración es reversible.

Dos cosas de ese fichero hay que respetar sí o sí:

1. Los disparadores (triggers) de Calibre llaman a funciones SQL que **no son
   de SQLite**, sino que las registra el propio Calibre: `title_sort(x)`,
   `uuid4()` y un `lower()` con acentos. Si se inserta un libro en una conexión
   que no las tenga registradas, el disparador falla y la escritura se cae.
   `conectar()` las registra siempre.
2. Las tablas de autores, series, etiquetas y editoriales son compartidas: al
   quitar el último libro que usaba una etiqueta hay que borrar la etiqueta, o
   el catálogo se llena de entradas huérfanas (Calibre lo hace en su código, no
   con disparadores).
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Artículos que Calibre manda al final del título para ordenar ("El Quijote"
# ordena por "Quijote, El"). El de Calibre no trae los españoles; aquí sí.
ARTICULOS = os.environ.get(
    "VIDALIBRE_ARTICULOS",
    r"^(A|The|An|Der|Die|Das|Den|Ein|Eine|Einen|Dem|Des|Einem|Eines"
    r"|Le|La|Les|L'|Un|Une|El|Los|Las|Una|Unos|Unas|O|As|Os)\s+",
)
_RE_ARTICULOS = re.compile(ARTICULOS, re.IGNORECASE)

# Sufijos que no cuentan como apellido al calcular "Apellido, Nombre".
_RE_SUFIJOS = re.compile(r"^(JR|SR)\.?$|^I{1,3}\.?$|^IV\.?$", re.IGNORECASE)

ORDENES = {
    "reciente": "libros.timestamp DESC, libros.id DESC",
    "antiguo": "libros.timestamp ASC, libros.id ASC",
    "titulo": "libros.sort COLLATE NOCASE ASC",
    "autor": "libros.author_sort COLLATE NOCASE ASC, libros.series_index ASC",
    "publicacion": "libros.pubdate DESC",
    "modificado": "libros.last_modified DESC",
    # Cada saga junta y del primero al último; los libros sin serie, al final.
    "serie": "(SELECT s.sort FROM books_series_link bsl JOIN series s ON s.id = bsl.series "
             "WHERE bsl.book = libros.id LIMIT 1) IS NULL, "
             "(SELECT s.sort FROM books_series_link bsl JOIN series s ON s.id = bsl.series "
             "WHERE bsl.book = libros.id LIMIT 1) COLLATE NOCASE ASC, "
             "libros.series_index ASC, libros.sort COLLATE NOCASE ASC",
}


def ordenar_titulo(titulo: str) -> str:
    """El `sort` de Calibre: 'El nombre del viento' -> 'nombre del viento, El'."""
    m = _RE_ARTICULOS.search(titulo or "")
    if m:
        prep = m.group(1)
        titulo = titulo[len(m.group(0)):] + ", " + prep
    return (titulo or "").strip()


def ordenar_autor(nombre: str) -> str:
    """El `author_sort` de Calibre: 'Andy Weir' -> 'Weir, Andy'.

    Portado de Calibre-Web (`get_sorted_author`): si ya viene con coma se
    respeta, y los sufijos tipo 'Jr.' no se toman por apellido.
    """
    nombre = (nombre or "").strip()
    if not nombre or "," in nombre:
        return nombre
    partes = nombre.split()
    if len(partes) == 1:
        return partes[0]
    if _RE_SUFIJOS.match(partes[-1]):
        if len(partes) > 2:
            return partes[-2] + ", " + " ".join(partes[:-2]) + " " + partes[-1]
        return partes[0]
    return partes[-1] + ", " + " ".join(partes[:-1])


def _minusculas(valor):
    try:
        return valor.lower()
    except AttributeError:
        return valor


def ahora() -> str:
    """Marca de tiempo en el formato exacto que escribe Calibre."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")


class ErrorCatalogo(Exception):
    pass


class Catalogo:
    """La biblioteca de Calibre: una carpeta con su `metadata.db` dentro."""

    def __init__(self, raiz):
        self.raiz = Path(raiz)
        self.bd = self.raiz / "metadata.db"
        self._escritura = None

    # ---------------------------------------------------------------- conexión
    @contextmanager
    def conectar(self):
        if not self.bd.is_file():
            raise ErrorCatalogo(f"No encuentro el catálogo de Calibre en {self.bd}")
        cx = sqlite3.connect(str(self.bd), timeout=30, isolation_level=None)
        cx.row_factory = sqlite3.Row
        cx.create_function("title_sort", 1, ordenar_titulo)
        cx.create_function("uuid4", 0, lambda: __import__("uuid").uuid4().__str__())
        cx.create_function("lower", 1, _minusculas)
        try:
            yield cx
        finally:
            cx.close()

    @contextmanager
    def escribir(self):
        """Conexión en transacción: o entra todo, o no entra nada."""
        with self.conectar() as cx:
            cx.execute("BEGIN IMMEDIATE")
            try:
                yield cx
            except Exception:
                cx.execute("ROLLBACK")
                raise
            cx.execute("COMMIT")

    def escritura_posible(self, volver_a_probar=False):
        """¿Se puede escribir en el catálogo desde aquí?

        No basta con mirar los permisos del fichero: sobre una carpeta de red
        (SMB) los permisos dicen que sí y luego SQLite no puede echar sus
        cerrojos. La única comprobación que vale es intentar una escritura de
        verdad y deshacerla.
        """
        if self._escritura is not None and not volver_a_probar:
            return self._escritura
        try:
            with self.conectar() as cx:
                cx.execute("BEGIN IMMEDIATE")
                try:
                    cx.execute(
                        "UPDATE books SET last_modified = last_modified "
                        "WHERE id = (SELECT MIN(id) FROM books)"
                    )
                finally:
                    cx.execute("ROLLBACK")
            self._escritura = True
        except Exception:
            self._escritura = False
        return self._escritura

    def comprobar(self) -> dict:
        with self.conectar() as cx:
            total = cx.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            return {"ruta": str(self.raiz), "libros": total}

    # ------------------------------------------------------------------ lectura
    _CAMPOS = """
        libros.id, libros.title AS titulo, libros.sort AS orden_titulo,
        libros.timestamp AS anadido, libros.pubdate AS publicado,
        libros.series_index AS indice_serie, libros.author_sort AS orden_autor,
        libros.path AS ruta, libros.uuid, libros.has_cover AS tiene_portada,
        libros.last_modified AS modificado,
        (SELECT GROUP_CONCAT(a.name, ' & ') FROM books_authors_link bal
            JOIN authors a ON a.id = bal.author
            WHERE bal.book = libros.id) AS autores,
        (SELECT s.name FROM books_series_link bsl JOIN series s ON s.id = bsl.series
            WHERE bsl.book = libros.id LIMIT 1) AS serie,
        (SELECT p.name FROM books_publishers_link bpl JOIN publishers p ON p.id = bpl.publisher
            WHERE bpl.book = libros.id LIMIT 1) AS editorial,
        (SELECT GROUP_CONCAT(t.name, ', ') FROM books_tags_link btl
            JOIN tags t ON t.id = btl.tag WHERE btl.book = libros.id) AS etiquetas,
        (SELECT GROUP_CONCAT(l.lang_code, ', ') FROM books_languages_link bll
            JOIN languages l ON l.id = bll.lang_code
            WHERE bll.book = libros.id ORDER BY bll.item_order) AS idiomas,
        (SELECT r.rating FROM books_ratings_link brl JOIN ratings r ON r.id = brl.rating
            WHERE brl.book = libros.id LIMIT 1) AS valoracion,
        (SELECT GROUP_CONCAT(UPPER(d.format), ',') FROM data d
            WHERE d.book = libros.id) AS formatos
    """

    def _filtros(self, busqueda=None, autor=None, serie=None, etiqueta=None,
                 idioma=None, editorial=None, formato=None, sin_formato=None,
                 decada=None):
        donde, args = [], []
        if busqueda:
            patron = f"%{busqueda.strip()}%"
            donde.append("""(
                libros.title LIKE ?
                OR libros.author_sort LIKE ?
                OR EXISTS (SELECT 1 FROM books_authors_link bal JOIN authors a ON a.id=bal.author
                           WHERE bal.book=libros.id AND a.name LIKE ?)
                OR EXISTS (SELECT 1 FROM books_series_link bsl JOIN series s ON s.id=bsl.series
                           WHERE bsl.book=libros.id AND s.name LIKE ?)
                OR EXISTS (SELECT 1 FROM books_tags_link btl JOIN tags t ON t.id=btl.tag
                           WHERE btl.book=libros.id AND t.name LIKE ?)
                OR EXISTS (SELECT 1 FROM books_publishers_link bpl JOIN publishers p ON p.id=bpl.publisher
                           WHERE bpl.book=libros.id AND p.name LIKE ?)
                OR EXISTS (SELECT 1 FROM comments c WHERE c.book=libros.id AND c.text LIKE ?)
                OR EXISTS (SELECT 1 FROM identifiers i WHERE i.book=libros.id AND i.val LIKE ?)
            )""")
            args += [patron] * 8
        if autor:
            donde.append("EXISTS (SELECT 1 FROM books_authors_link bal JOIN authors a ON a.id=bal.author "
                         "WHERE bal.book=libros.id AND a.name = ?)")
            args.append(autor)
        if serie:
            donde.append("EXISTS (SELECT 1 FROM books_series_link bsl JOIN series s ON s.id=bsl.series "
                         "WHERE bsl.book=libros.id AND s.name = ?)")
            args.append(serie)
        if etiqueta:
            donde.append("EXISTS (SELECT 1 FROM books_tags_link btl JOIN tags t ON t.id=btl.tag "
                         "WHERE btl.book=libros.id AND t.name = ?)")
            args.append(etiqueta)
        if editorial:
            donde.append("EXISTS (SELECT 1 FROM books_publishers_link bpl JOIN publishers p ON p.id=bpl.publisher "
                         "WHERE bpl.book=libros.id AND p.name = ?)")
            args.append(editorial)
        if idioma:
            donde.append("EXISTS (SELECT 1 FROM books_languages_link bll JOIN languages l ON l.id=bll.lang_code "
                         "WHERE bll.book=libros.id AND l.lang_code = ?)")
            args.append(idioma)
        if formato:
            donde.append("EXISTS (SELECT 1 FROM data d WHERE d.book=libros.id AND UPPER(d.format) = ?)")
            args.append(formato.upper())
        if sin_formato:
            donde.append("NOT EXISTS (SELECT 1 FROM data d WHERE d.book=libros.id AND UPPER(d.format) = ?)")
            args.append(sin_formato.upper())
        if decada:
            try:
                inicio = (int(decada) // 10) * 10
            except (TypeError, ValueError):
                inicio = None
            if inicio is not None:
                donde.append("CAST(substr(libros.pubdate, 1, 4) AS INTEGER) BETWEEN ? AND ?")
                args += [inicio, inicio + 9]
        return (" WHERE " + " AND ".join(donde)) if donde else "", args

    def libros(self, orden="reciente", pagina=1, por_pagina=48, **filtros):
        donde, args = self._filtros(**filtros)
        orden_sql = ORDENES.get(orden, ORDENES["reciente"])
        desplazamiento = max(0, (pagina - 1) * por_pagina)
        with self.conectar() as cx:
            total = cx.execute(f"SELECT COUNT(*) FROM books libros{donde}", args).fetchone()[0]
            filas = cx.execute(
                f"SELECT {self._CAMPOS} FROM books libros{donde} "
                f"ORDER BY {orden_sql} LIMIT ? OFFSET ?",
                args + [por_pagina, desplazamiento],
            ).fetchall()
        return [self._libro_dict(f) for f in filas], total

    def libro(self, id_libro):
        with self.conectar() as cx:
            fila = cx.execute(
                f"SELECT {self._CAMPOS} FROM books libros WHERE libros.id = ?", (id_libro,)
            ).fetchone()
            if not fila:
                return None
            libro = self._libro_dict(fila)
            com = cx.execute("SELECT text FROM comments WHERE book = ?", (id_libro,)).fetchone()
            libro["descripcion"] = com["text"] if com else ""
            libro["identificadores"] = {
                f["type"]: f["val"]
                for f in cx.execute("SELECT type, val FROM identifiers WHERE book = ?", (id_libro,))
            }
            libro["ficheros"] = [
                {"formato": f["format"].upper(), "nombre": f["name"], "tamano": f["uncompressed_size"]}
                for f in cx.execute(
                    "SELECT format, name, uncompressed_size FROM data WHERE book = ? ORDER BY format",
                    (id_libro,),
                )
            ]
            return libro

    def _libro_dict(self, fila):
        libro = dict(fila)
        libro["autores"] = [a.strip() for a in (libro.get("autores") or "").split("&") if a.strip()]
        libro["etiquetas"] = [t.strip() for t in (libro.get("etiquetas") or "").split(",") if t.strip()]
        libro["idiomas"] = [i.strip() for i in (libro.get("idiomas") or "").split(",") if i.strip()]
        libro["formatos"] = sorted({f.strip() for f in (libro.get("formatos") or "").split(",") if f.strip()})
        libro["carpeta"] = self.raiz / libro["ruta"]
        return libro

    def ids_filtrados(self, orden="reciente", limite=None, **filtros):
        """Los ids que cumplen un filtro, para operaciones en bloque."""
        donde, args = self._filtros(**filtros)
        orden_sql = ORDENES.get(orden, ORDENES["reciente"])
        sql = f"SELECT libros.id FROM books libros{donde} ORDER BY {orden_sql}"
        if limite:
            sql += f" LIMIT {int(limite)}"
        with self.conectar() as cx:
            return [f[0] for f in cx.execute(sql, args)]

    # tabla, tabla de enlace, columna del enlace, columna del nombre, ¿tiene `sort`?
    _CATEGORIAS = {
        "autores": ("authors", "books_authors_link", "author", "name", True),
        "series": ("series", "books_series_link", "series", "name", True),
        "etiquetas": ("tags", "books_tags_link", "tag", "name", False),
        "editoriales": ("publishers", "books_publishers_link", "publisher", "name", True),
        "idiomas": ("languages", "books_languages_link", "lang_code", "lang_code", False),
    }

    def categoria(self, tipo, minimo=1):
        """Lista de autores / series / etiquetas / editoriales / idiomas con su recuento."""
        if tipo not in self._CATEGORIAS:
            raise ErrorCatalogo(f"Categoría desconocida: {tipo}")
        tabla, enlace, columna, nombre, con_orden = self._CATEGORIAS[tipo]
        with self.conectar() as cx:
            return [
                {"nombre": f["nombre"], "orden": f["orden"] or f["nombre"], "libros": f["libros"]}
                for f in cx.execute(
                    f"SELECT t.{nombre} AS nombre, "
                    f"{'t.sort' if con_orden else f't.{nombre}'} AS orden, "
                    f"COUNT(l.book) AS libros "
                    f"FROM {tabla} t JOIN {enlace} l ON l.{columna} = t.id "
                    f"GROUP BY t.id HAVING libros >= ? ORDER BY nombre COLLATE NOCASE",
                    (minimo,),
                )
            ]

    def decadas(self, minimo=1):
        """Los libros agrupados por década de publicación, de la más nueva a la
        más vieja. Es el «por año» de la portada: por años sueltos saldrían
        cincuenta grupos de un libro y no se entiende nada."""
        with self.conectar() as cx:
            filas = cx.execute(
                "SELECT (CAST(substr(pubdate, 1, 4) AS INTEGER) / 10) * 10 AS decada, "
                "COUNT(*) AS libros FROM books "
                "WHERE pubdate IS NOT NULL AND CAST(substr(pubdate, 1, 4) AS INTEGER) > 1000 "
                "GROUP BY decada HAVING libros >= ? ORDER BY decada DESC",
                (minimo,),
            ).fetchall()
        return [{"decada": f["decada"], "libros": f["libros"]} for f in filas]

    def libros_de_decada(self, decada, limite=14):
        """Las portadas de una década, para la pila del mosaico."""
        with self.conectar() as cx:
            filas = cx.execute(
                f"SELECT {self._CAMPOS} FROM books libros "
                "WHERE CAST(substr(libros.pubdate, 1, 4) AS INTEGER) BETWEEN ? AND ? "
                "ORDER BY libros.pubdate DESC LIMIT ?",
                (int(decada), int(decada) + 9, limite),
            ).fetchall()
        return [self._libro_dict(f) for f in filas]

    def formatos(self):
        with self.conectar() as cx:
            return [
                {"nombre": f[0].upper(), "libros": f[1]}
                for f in cx.execute(
                    "SELECT UPPER(format), COUNT(DISTINCT book) FROM data "
                    "GROUP BY UPPER(format) ORDER BY 2 DESC"
                )
            ]

    def resumen(self):
        with self.conectar() as cx:
            r = {
                "libros": cx.execute("SELECT COUNT(*) FROM books").fetchone()[0],
                "autores": cx.execute(
                    "SELECT COUNT(DISTINCT author) FROM books_authors_link").fetchone()[0],
                "series": cx.execute(
                    "SELECT COUNT(DISTINCT series) FROM books_series_link").fetchone()[0],
                "etiquetas": cx.execute(
                    "SELECT COUNT(DISTINCT tag) FROM books_tags_link").fetchone()[0],
                "ficheros": cx.execute("SELECT COUNT(*) FROM data").fetchone()[0],
                "bytes": cx.execute(
                    "SELECT COALESCE(SUM(uncompressed_size),0) FROM data").fetchone()[0],
            }
        return r

    # ----------------------------------------------------------------- escritura
    def _id_de(self, cx, tabla, nombre, con_orden=False):
        """Id de un autor/etiqueta/serie/editorial, creándolo si no existe."""
        nombre = (nombre or "").strip()
        if not nombre:
            return None
        fila = cx.execute(f"SELECT id FROM {tabla} WHERE name = ?", (nombre,)).fetchone()
        if fila:
            return fila[0]
        if con_orden:
            orden = ordenar_autor(nombre) if tabla == "authors" else ordenar_titulo(nombre)
            cur = cx.execute(f"INSERT INTO {tabla} (name, sort) VALUES (?, ?)", (nombre, orden))
        else:
            cur = cx.execute(f"INSERT INTO {tabla} (name) VALUES (?)", (nombre,))
        return cur.lastrowid

    def _id_idioma(self, cx, codigo):
        codigo = (codigo or "").strip().lower()
        if not codigo:
            return None
        fila = cx.execute("SELECT id FROM languages WHERE lang_code = ?", (codigo,)).fetchone()
        if fila:
            return fila[0]
        return cx.execute("INSERT INTO languages (lang_code) VALUES (?)", (codigo,)).lastrowid

    def _enlazar(self, cx, tabla_enlace, columna, id_libro, ids, con_orden=False):
        cx.execute(f"DELETE FROM {tabla_enlace} WHERE book = ?", (id_libro,))
        for posicion, valor in enumerate(ids):
            if valor is None:
                continue
            if con_orden:
                cx.execute(
                    f"INSERT INTO {tabla_enlace} (book, {columna}, item_order) VALUES (?, ?, ?)",
                    (id_libro, valor, posicion),
                )
            else:
                cx.execute(
                    f"INSERT INTO {tabla_enlace} (book, {columna}) VALUES (?, ?)", (id_libro, valor)
                )

    def limpiar_huerfanos(self, cx=None):
        """Borra autores, series, etiquetas y editoriales que ya no usa ningún libro."""
        def _limpiar(cx):
            borrados = 0
            for tabla, enlace, columna in (
                ("authors", "books_authors_link", "author"),
                ("series", "books_series_link", "series"),
                ("tags", "books_tags_link", "tag"),
                ("publishers", "books_publishers_link", "publisher"),
                ("languages", "books_languages_link", "lang_code"),
            ):
                cur = cx.execute(
                    f"DELETE FROM {tabla} WHERE id NOT IN (SELECT {columna} FROM {enlace})"
                )
                borrados += cur.rowcount or 0
            return borrados

        if cx is not None:
            return _limpiar(cx)
        with self.escribir() as cx:
            return _limpiar(cx)

    def crear_libro(self, cx, titulo, autores, ruta_rel, publicado=None, anadido=None):
        """Crea la fila del libro. La carpeta y los ficheros los pone `ficheros.py`."""
        titulo = (titulo or "Desconocido").strip() or "Desconocido"
        autores = [a for a in (autores or []) if a.strip()] or ["Autor desconocido"]
        orden_autores = " & ".join(ordenar_autor(a) for a in autores)
        marca = anadido or ahora()
        cur = cx.execute(
            "INSERT INTO books (title, sort, timestamp, pubdate, series_index, author_sort, "
            "path, has_cover, last_modified) VALUES (?, ?, ?, ?, 1.0, ?, ?, 0, ?)",
            (titulo, ordenar_titulo(titulo), marca, publicado or marca, orden_autores,
             ruta_rel, marca),
        )
        id_libro = cur.lastrowid
        self._enlazar(cx, "books_authors_link", "author",
                      id_libro, [self._id_de(cx, "authors", a, con_orden=True) for a in autores])
        return id_libro

    def fijar_ruta(self, cx, id_libro, ruta_rel):
        cx.execute("UPDATE books SET path = ? WHERE id = ?", (ruta_rel.replace("\\", "/"), id_libro))

    def guardar_metadatos(self, cx, id_libro, datos):
        """Guarda los campos editables. Solo toca lo que venga en `datos`."""
        campos, args = [], []
        if "titulo" in datos:
            titulo = (datos["titulo"] or "").strip() or "Desconocido"
            campos.append("title = ?")
            args.append(titulo)
            # El disparador books_update_trg recalcula `sort`, pero solo si el
            # título cambia; se fija a mano para que no dependa de eso.
            campos.append("sort = ?")
            args.append(datos.get("orden_titulo") or ordenar_titulo(titulo))
        if "publicado" in datos:
            campos.append("pubdate = ?")
            args.append(datos["publicado"])
        if "anadido" in datos:
            campos.append("timestamp = ?")
            args.append(datos["anadido"])
        if "indice_serie" in datos:
            campos.append("series_index = ?")
            # Ojo con `or`: el 0 es falso en Python y Calibre usa justamente el
            # 0 para las precuelas y los volúmenes sueltos de una serie. Con
            # `valor or 1.0` un 0 se convertía en 1 y chocaba con el primer
            # libro. Solo el vacío y el None valen 1.
            indice = datos["indice_serie"]
            try:
                indice = 1.0 if indice in (None, "") else float(indice)
            except (TypeError, ValueError):
                indice = 1.0
            args.append(indice)
        if "autores" in datos:
            autores = [a for a in datos["autores"] if a.strip()] or ["Autor desconocido"]
            campos.append("author_sort = ?")
            args.append(datos.get("orden_autor") or " & ".join(ordenar_autor(a) for a in autores))
        if "tiene_portada" in datos:
            campos.append("has_cover = ?")
            args.append(1 if datos["tiene_portada"] else 0)
        campos.append("last_modified = ?")
        args.append(ahora())
        cx.execute(f"UPDATE books SET {', '.join(campos)} WHERE id = ?", args + [id_libro])

        if "autores" in datos:
            autores = [a for a in datos["autores"] if a.strip()] or ["Autor desconocido"]
            self._enlazar(cx, "books_authors_link", "author", id_libro,
                          [self._id_de(cx, "authors", a, con_orden=True) for a in autores])
        if "serie" in datos:
            serie = (datos["serie"] or "").strip()
            self._enlazar(cx, "books_series_link", "series", id_libro,
                          [self._id_de(cx, "series", serie, con_orden=True)] if serie else [])
        if "editorial" in datos:
            editorial = (datos["editorial"] or "").strip()
            self._enlazar(cx, "books_publishers_link", "publisher", id_libro,
                          [self._id_de(cx, "publishers", editorial, con_orden=True)] if editorial else [])
        if "etiquetas" in datos:
            vistas, ids = set(), []
            for etiqueta in datos["etiquetas"]:
                clave = etiqueta.strip().lower()
                if etiqueta.strip() and clave not in vistas:
                    vistas.add(clave)
                    ids.append(self._id_de(cx, "tags", etiqueta.strip()))
            self._enlazar(cx, "books_tags_link", "tag", id_libro, ids)
        if "idiomas" in datos:
            self._enlazar(cx, "books_languages_link", "lang_code", id_libro,
                          [self._id_idioma(cx, i) for i in datos["idiomas"]], con_orden=True)
        if "valoracion" in datos:
            valor = int(datos["valoracion"] or 0)
            cx.execute("DELETE FROM books_ratings_link WHERE book = ?", (id_libro,))
            if valor > 0:
                fila = cx.execute("SELECT id FROM ratings WHERE rating = ?", (valor,)).fetchone()
                id_valor = fila[0] if fila else cx.execute(
                    "INSERT INTO ratings (rating) VALUES (?)", (valor,)).lastrowid
                cx.execute("INSERT INTO books_ratings_link (book, rating) VALUES (?, ?)",
                           (id_libro, id_valor))
        if "descripcion" in datos:
            texto = (datos["descripcion"] or "").strip()
            cx.execute("DELETE FROM comments WHERE book = ?", (id_libro,))
            if texto:
                cx.execute("INSERT INTO comments (book, text) VALUES (?, ?)", (id_libro, texto))
        if "identificadores" in datos:
            cx.execute("DELETE FROM identifiers WHERE book = ?", (id_libro,))
            for tipo, valor in (datos["identificadores"] or {}).items():
                tipo = (tipo or "").strip().lower()
                valor = (valor or "").strip()
                if tipo and valor:
                    cx.execute(
                        "INSERT OR REPLACE INTO identifiers (book, type, val) VALUES (?, ?, ?)",
                        (id_libro, tipo, valor),
                    )
        self.limpiar_huerfanos(cx)

    def anadir_formato(self, cx, id_libro, formato, nombre, tamano):
        """Apunta un fichero del libro. Un libro puede tener varios formatos."""
        cx.execute(
            "INSERT OR REPLACE INTO data (book, format, uncompressed_size, name) VALUES (?, ?, ?, ?)",
            (id_libro, formato.upper(), int(tamano), nombre),
        )
        cx.execute("UPDATE books SET last_modified = ? WHERE id = ?", (ahora(), id_libro))

    def quitar_formato(self, cx, id_libro, formato):
        cx.execute("DELETE FROM data WHERE book = ? AND UPPER(format) = ?",
                   (id_libro, formato.upper()))
        cx.execute("UPDATE books SET last_modified = ? WHERE id = ?", (ahora(), id_libro))

    def borrar_libro(self, cx, id_libro):
        """Borra la fila (el disparador de Calibre se lleva los enlaces y el resto)."""
        cx.execute("DELETE FROM books WHERE id = ?", (id_libro,))
        self.limpiar_huerfanos(cx)

    def renombrar_categoria(self, tipo, viejo, nuevo):
        """Renombra un autor, serie, etiqueta o editorial en toda la biblioteca."""
        if tipo not in self._CATEGORIAS:
            raise ErrorCatalogo(f"Categoría desconocida: {tipo}")
        tabla, enlace, columna, _nombre, con_orden = self._CATEGORIAS[tipo]
        nuevo = (nuevo or "").strip()
        if not nuevo:
            raise ErrorCatalogo("El nombre nuevo no puede estar vacío")
        with self.escribir() as cx:
            fila = cx.execute(f"SELECT id FROM {tabla} WHERE name = ?", (viejo,)).fetchone()
            if not fila:
                raise ErrorCatalogo(f"No existe: {viejo}")
            id_viejo = fila[0]
            ya = cx.execute(f"SELECT id FROM {tabla} WHERE name = ?", (nuevo,)).fetchone()
            if ya and ya[0] != id_viejo:
                # Ya existía: se funden los dos.
                cx.execute(
                    f"UPDATE OR IGNORE {enlace} SET {columna} = ? WHERE {columna} = ?",
                    (ya[0], id_viejo),
                )
                cx.execute(f"DELETE FROM {enlace} WHERE {columna} = ?", (id_viejo,))
                cx.execute(f"DELETE FROM {tabla} WHERE id = ?", (id_viejo,))
                id_final = ya[0]
            else:
                orden = ordenar_autor(nuevo) if tabla == "authors" else ordenar_titulo(nuevo)
                if tabla == "languages":
                    cx.execute("UPDATE languages SET lang_code = ? WHERE id = ?", (nuevo, id_viejo))
                elif con_orden:
                    cx.execute(f"UPDATE {tabla} SET name = ?, sort = ? WHERE id = ?",
                               (nuevo, orden, id_viejo))
                else:
                    cx.execute(f"UPDATE {tabla} SET name = ? WHERE id = ?", (nuevo, id_viejo))
                id_final = id_viejo
            if tabla == "authors":
                # El author_sort del libro se guarda aparte: hay que rehacerlo.
                for fila in cx.execute(
                    f"SELECT book FROM {enlace} WHERE {columna} = ?", (id_final,)
                ).fetchall():
                    autores = [
                        f[0] for f in cx.execute(
                            "SELECT a.name FROM books_authors_link bal JOIN authors a ON a.id=bal.author "
                            "WHERE bal.book = ? ORDER BY bal.id", (fila[0],)
                        )
                    ]
                    cx.execute("UPDATE books SET author_sort = ?, last_modified = ? WHERE id = ?",
                               (" & ".join(ordenar_autor(a) for a in autores), ahora(), fila[0]))
            ids = [f[0] for f in cx.execute(f"SELECT book FROM {enlace} WHERE {columna} = ?", (id_final,))]
        return ids
