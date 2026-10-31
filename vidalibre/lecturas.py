"""El registro de lecturas: qué has leído, cómo te dejó y cuánto te queda.

Va en una base de datos **aparte** (`lecturas.db`, en la carpeta de datos) y no
en el catálogo de Calibre, por dos razones:

1. Aquí caben libros que **no están en la biblioteca** (los que leíste en papel
   o prestados). Si se metieran en el catálogo, Calibre los enseñaría como
   libros tuyos, y no lo son.
2. El catálogo sigue sin tocarse, que es lo que hace que Vidalibre se pueda
   apagar en cualquier momento sin dejar nada a medias. Borrar este fichero
   no estropea la biblioteca; solo pierde el diario de lecturas (por eso el
   despliegue le hace copia igual que al catálogo).

Las piezas:

- **obra**: un libro que has leído o quieres leer. Si está en la biblioteca
  lleva su número de Calibre (`libro`); si no, es «de fuera» y guarda su propia
  ficha (portada, sinopsis, páginas) traída de Open Library.
- **lectura**: cada vez que lees una obra. Un libro releído tiene dos lecturas,
  cada una con sus fechas y su reseña.
- **reseña**: estrellas (de media en media), sensaciones, ritmo y texto libre.
  Nace *pendiente de procesar*: es lo que Claude lee para afinar las
  recomendaciones.
- **recuerdo** (de la obra): bien, borroso u olvidado; y la decisión para los
  dos últimos: releerlo o soltarlo.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from datetime import date, datetime
from pathlib import Path

# --------------------------------------------------------------- vocabulario
ESTADOS = {
    "quiero": "Quiero leerlo",
    "leyendo": "Leyendo",
    "leido": "Leído",
    "abandonado": "Abandonado",
}

SENSACIONES = [
    ("removio", "Me removió"),
    ("pensando", "Me dejó pensando"),
    ("evasion", "Evasión pura"),
    ("angustioso", "Angustioso"),
    ("aburrido", "Aburrido"),
    ("reconfortante", "Reconfortante"),
]
NOMBRE_SENSACION = dict(SENSACIONES)

RITMOS = [("lento", "Lento"), ("medio", "Medio"), ("agil", "Ágil")]
NOMBRE_RITMO = dict(RITMOS)

RECUERDOS = [
    ("bien", "Lo recuerdo bien"),
    ("borroso", "Borroso"),
    ("olvidado", "Olvidado"),
]
NOMBRE_RECUERDO = dict(RECUERDOS)

DECISIONES = {"releer": "Quiero releerlo", "soltar": "Lo suelto"}

# ------------------------------------------------------------ el contador
# Una página de libro de bolsillo en español ronda las 250–300 palabras. Solo
# se usa para los libros de fuera, de los que no hay texto que contar.
PALABRAS_POR_PAGINA = 275
# Lo que mide una novela normal. Solo hasta que haya libros tuyos medidos.
LONGITUD_POR_DEFECTO = 90_000
# Por debajo de esto el ritmo es ruido: un libro corto leído en dos tardes
# daría una cifra disparatada.
MINIMO_PARA_CALIBRAR = 3
# El ritmo se mide sobre el último año como mucho: lo que leías hace tres años
# no dice nada de tu ritmo de ahora.
VENTANA_DIAS = 365
# Una lectura empezada hace más de esto ya no se recuerda con precisión: se
# apunta, pero no entra en el cálculo del ritmo.
MARGEN_FECHA_REAL = 60
PALABRAS_POR_MINUTO = 230
# Lo apuntado como «leído en tal año» también da ritmo: N libros en un año de
# tal longitud son tantas palabras al día. Pero la memoria se borra: del año
# pasado te acuerdas casi de todo, de hace cuatro te dejas la mitad, y un año
# medio olvidado parece un año en que leíste poco. Por eso cada año pesa menos
# cuanto más lejos queda, y de cinco años atrás ya no se usa para el ritmo.
# (Antigüedad 0 = este año, 1 = el pasado…)
PESO_POR_ANTIGUEDAD = {0: 1.0, 1: 1.0, 2: 0.7, 3: 0.35, 4: 0.15}
# Un año de fechas reales pesa el doble que un año recordado: es un dato medido.
PESO_MEDIDO = 2.0
# Un trozo de año más corto que esto no da un ritmo de fiar.
DIAS_MINIMOS_TRAMO = 30

# Esperanza de vida que queda a cada edad, hombres, España, 2024.
# Fuente: INE, Tablas de mortalidad (tabla 27153, función «Esperanza de vida»),
# consultado el 31-oct-2026. El INE no la publica por etnia; sí por sexo y
# edad, que es lo que cambia la cifra. Con la edad la esperanza *total* sube
# (quien llega a los 70 ya ha esquivado lo de antes), así que la tabla entera
# es mejor que un número fijo.
ESPERANZA_HOMBRES_ES = {
    18: 63.8, 19: 62.8, 20: 61.8, 21: 60.9, 22: 59.9, 23: 58.9, 24: 57.9,
    25: 56.9, 26: 55.9, 27: 55.0, 28: 54.0, 29: 53.0, 30: 52.0, 31: 51.1,
    32: 50.1, 33: 49.1, 34: 48.1, 35: 47.1, 36: 46.2, 37: 45.2, 38: 44.2,
    39: 43.3, 40: 42.3, 41: 41.3, 42: 40.4, 43: 39.4, 44: 38.5, 45: 37.5,
    46: 36.6, 47: 35.6, 48: 34.7, 49: 33.7, 50: 32.8, 51: 31.9, 52: 31.0,
    53: 30.0, 54: 29.1, 55: 28.2, 56: 27.4, 57: 26.5, 58: 25.6, 59: 24.8,
    60: 23.9, 61: 23.1, 62: 22.3, 63: 21.5, 64: 20.7, 65: 19.9, 66: 19.1,
    67: 18.3, 68: 17.5, 69: 16.8, 70: 16.0, 71: 15.3, 72: 14.6, 73: 13.9,
    74: 13.2, 75: 12.5, 76: 11.8, 77: 11.2, 78: 10.5, 79: 9.9, 80: 9.3,
    81: 8.7, 82: 8.1, 83: 7.6, 84: 7.0, 85: 6.5, 86: 6.0, 87: 5.6, 88: 5.2,
    89: 4.8, 90: 4.4, 91: 4.1, 92: 3.8, 93: 3.5, 94: 3.2, 95: 3.0, 96: 2.8,
    97: 2.5, 98: 2.3, 99: 2.1,
}


def anos_que_quedan(edad):
    """Años de vida que quedan a una edad (con decimales), interpolando."""
    edades = sorted(ESPERANZA_HOMBRES_ES)
    if edad <= edades[0]:
        return ESPERANZA_HOMBRES_ES[edades[0]] + (edades[0] - edad)
    if edad >= edades[-1]:
        return ESPERANZA_HOMBRES_ES[edades[-1]]
    abajo = int(edad)
    fraccion = edad - abajo
    return (ESPERANZA_HOMBRES_ES[abajo] * (1 - fraccion)
            + ESPERANZA_HOMBRES_ES[abajo + 1] * fraccion)


# ------------------------------------------------------------------- ayudas
def hoy():
    return date.today()


def a_fecha(texto):
    """'2026-10-31' → date. Lo que no sea una fecha completa, None."""
    try:
        return datetime.strptime(str(texto or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def normalizar(texto):
    """Para comparar títulos y autores: sin tildes, sin mayúsculas, sin signos."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def _titulo_base(titulo):
    from .fuentes import limpiar_titulo

    return normalizar(limpiar_titulo(titulo or ""))


def misma_obra(titulo_a, autores_a, titulo_b, autores_b, isbn_a="", isbn_b=""):
    """¿Son el mismo libro? Por ISBN si lo hay; si no, título limpio y algún
    apellido en común. Lo bastante estricto para no proponer disparates."""
    limpio = lambda i: re.sub(r"[^0-9xX]", "", i or "")
    if limpio(isbn_a) and limpio(isbn_a) == limpio(isbn_b):
        return True
    if not titulo_a or _titulo_base(titulo_a) != _titulo_base(titulo_b):
        return False
    apellidos_a = {p for a in autores_a or [] for p in normalizar(a).split() if len(p) > 2}
    apellidos_b = {p for a in autores_b or [] for p in normalizar(a).split() if len(p) > 2}
    if not apellidos_a or not apellidos_b:
        return True
    return bool(apellidos_a & apellidos_b)


def estrellas_texto(valor):
    """3.5 → '★★★½'. Sin valoración, cadena vacía."""
    if not valor:
        return ""
    enteras = int(valor)
    return "★" * enteras + ("½" if valor - enteras >= 0.5 else "")


# ----------------------------------------------------------------- registro
ESQUEMA = """
CREATE TABLE IF NOT EXISTS obras (
    id         INTEGER PRIMARY KEY,
    libro      INTEGER UNIQUE,          -- número de Calibre; NULL = de fuera
    titulo     TEXT NOT NULL,
    autores    TEXT NOT NULL DEFAULT '[]',
    anio       INTEGER,
    sinopsis   TEXT NOT NULL DEFAULT '',
    paginas    INTEGER,
    palabras   INTEGER,
    isbn       TEXT NOT NULL DEFAULT '',
    clave_ol   TEXT NOT NULL DEFAULT '',
    portada    TEXT NOT NULL DEFAULT '', -- fichero, solo las de fuera
    recuerdo   TEXT,                     -- bien | borroso | olvidado
    decision   TEXT,                     -- releer | soltar
    repasado   INTEGER NOT NULL DEFAULT 0,
    creada     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lecturas (
    id         INTEGER PRIMARY KEY,
    obra       INTEGER NOT NULL REFERENCES obras(id) ON DELETE CASCADE,
    estado     TEXT NOT NULL,            -- quiero | leyendo | leido | abandonado
    empezado   TEXT,                     -- AAAA-MM-DD
    terminado  TEXT,                     -- AAAA-MM-DD, o solo AAAA si es del pasado
    fechada    INTEGER NOT NULL DEFAULT 0, -- 1 = fechas reales, cuenta para el ritmo
    relectura  INTEGER NOT NULL DEFAULT 0,
    fraccion   REAL,                     -- hasta dónde llegaste, si se abandona
    creada     TEXT NOT NULL,
    actualizada TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lecturas_obra ON lecturas(obra);
CREATE TABLE IF NOT EXISTS resenas (
    lectura    INTEGER PRIMARY KEY REFERENCES lecturas(id) ON DELETE CASCADE,
    estrellas  REAL,
    sensaciones TEXT NOT NULL DEFAULT '[]',
    ritmo      TEXT,
    texto      TEXT NOT NULL DEFAULT '',
    procesada  INTEGER NOT NULL DEFAULT 0,
    actualizada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aperturas (
    libro      INTEGER PRIMARY KEY,
    primera    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recomendaciones (
    id         INTEGER PRIMARY KEY,
    tipo       TEXT NOT NULL,            -- biblioteca | comprar
    libro      INTEGER,
    titulo     TEXT NOT NULL DEFAULT '',
    autores    TEXT NOT NULL DEFAULT '[]',
    anio       INTEGER,
    portada_url TEXT NOT NULL DEFAULT '',
    clave_ol   TEXT NOT NULL DEFAULT '',
    sinopsis   TEXT NOT NULL DEFAULT '',
    paginas    INTEGER,
    motivo     TEXT NOT NULL DEFAULT '',
    descartada INTEGER NOT NULL DEFAULT 0,
    creada     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferencias (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


class Registro:
    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)
        self.ruta = self.carpeta / "lecturas.db"
        self.portadas = self.carpeta / "portadas-lecturas"
        self._cerrojo = threading.Lock()
        self._listo = False

    # -------------------------------------------------------------- conexión
    def _cx(self):
        if not self._listo:
            with self._cerrojo:
                if not self._listo:
                    self.carpeta.mkdir(parents=True, exist_ok=True)
                    self.portadas.mkdir(parents=True, exist_ok=True)
                    cx = sqlite3.connect(self.ruta, timeout=15)
                    cx.execute("PRAGMA journal_mode=WAL")
                    cx.executescript(ESQUEMA)
                    cx.close()
                    self._listo = True
        cx = sqlite3.connect(self.ruta, timeout=15)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA foreign_keys=ON")
        return cx

    def _hacer(self, consulta, parametros=()):
        cx = self._cx()
        try:
            with cx:
                cursor = cx.execute(consulta, parametros)
                return cursor.lastrowid
        finally:
            cx.close()

    def _filas(self, consulta, parametros=()):
        cx = self._cx()
        try:
            return [dict(f) for f in cx.execute(consulta, parametros).fetchall()]
        finally:
            cx.close()

    def _fila(self, consulta, parametros=()):
        filas = self._filas(consulta, parametros)
        return filas[0] if filas else None

    @staticmethod
    def _ahora():
        return datetime.now().isoformat(timespec="seconds")

    # ----------------------------------------------------------- preferencias
    def preferencia(self, clave, defecto=None):
        fila = self._fila("SELECT valor FROM preferencias WHERE clave = ?", (clave,))
        return fila["valor"] if fila and fila["valor"] is not None else defecto

    def fijar_preferencia(self, clave, valor):
        self._hacer("INSERT INTO preferencias (clave, valor) VALUES (?, ?) "
                    "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                    (clave, None if valor is None else str(valor)))

    # ------------------------------------------------------------------ obras
    @staticmethod
    def _obra(fila):
        if not fila:
            return None
        fila["autores"] = json.loads(fila.get("autores") or "[]")
        fila["de_fuera"] = fila.get("libro") is None
        return fila

    def obra(self, id_obra):
        return self._obra(self._fila("SELECT * FROM obras WHERE id = ?", (id_obra,)))

    def obra_de_libro(self, id_libro):
        return self._obra(self._fila("SELECT * FROM obras WHERE libro = ?", (id_libro,)))

    def asegurar_obra_de_libro(self, datos):
        """La obra de un libro de la biblioteca; si no existe, se crea con su ficha."""
        existente = self.obra_de_libro(datos["id"])
        if existente:
            # El título o el autor pueden haber cambiado al editar la ficha.
            if existente["titulo"] != datos["titulo"] or existente["autores"] != datos["autores"]:
                self._hacer("UPDATE obras SET titulo = ?, autores = ? WHERE id = ?",
                            (datos["titulo"], json.dumps(datos["autores"], ensure_ascii=False),
                             existente["id"]))
                existente = self.obra(existente["id"])
            return existente
        anio = None
        if datos.get("publicado"):
            coincide = re.match(r"(\d{4})", str(datos["publicado"]))
            anio = int(coincide.group(1)) if coincide and int(coincide.group(1)) > 101 else None
        isbn = (datos.get("identificadores") or {}).get("isbn", "")
        id_obra = self._hacer(
            "INSERT INTO obras (libro, titulo, autores, anio, isbn, creada) VALUES (?, ?, ?, ?, ?, ?)",
            (datos["id"], datos["titulo"], json.dumps(datos["autores"], ensure_ascii=False),
             anio, isbn, self._ahora()),
        )
        return self.obra(id_obra)

    def crear_de_fuera(self, ficha, portada=None):
        """Una obra que no está en la biblioteca, con la ficha de Open Library."""
        id_obra = self._hacer(
            "INSERT INTO obras (libro, titulo, autores, anio, sinopsis, paginas, isbn, "
            "clave_ol, creada) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ficha.get("titulo") or "Sin título",
             json.dumps(ficha.get("autores") or [], ensure_ascii=False),
             ficha.get("publicado") or None, ficha.get("sinopsis") or "",
             int(ficha.get("paginas") or 0) or None, ficha.get("isbn") or "",
             ficha.get("clave") or "", self._ahora()),
        )
        if portada:
            nombre = f"{id_obra}.jpg"
            (self.portadas / nombre).write_bytes(portada)
            self._hacer("UPDATE obras SET portada = ? WHERE id = ?", (nombre, id_obra))
        return self.obra(id_obra)

    def obras_de_fuera(self):
        return [self._obra(f) for f in self._filas(
            "SELECT * FROM obras WHERE libro IS NULL ORDER BY titulo COLLATE NOCASE")]

    def ruta_portada(self, obra):
        if obra and obra.get("portada"):
            ruta = self.portadas / obra["portada"]
            if ruta.is_file():
                return ruta
        return None

    def fijar_palabras(self, id_obra, palabras):
        self._hacer("UPDATE obras SET palabras = ? WHERE id = ?", (int(palabras), id_obra))

    def fijar_recuerdo(self, id_obra, recuerdo, decision=None):
        recuerdo = recuerdo if recuerdo in NOMBRE_RECUERDO else None
        decision = decision if (decision in DECISIONES and recuerdo in ("borroso", "olvidado")) else None
        self._hacer("UPDATE obras SET recuerdo = ?, decision = ? WHERE id = ?",
                    (recuerdo, decision, id_obra))
        if decision == "releer":
            self.quiero(id_obra, relectura=True)
        elif decision == "soltar":
            # Soltarlo es soltarlo: si estaba en «quiero releer», sale de ahí.
            self._hacer("DELETE FROM lecturas WHERE obra = ? AND estado = 'quiero'", (id_obra,))

    def marcar_repasado(self, id_obra):
        self._hacer("UPDATE obras SET repasado = 1 WHERE id = ?", (id_obra,))

    def libros_repasados(self):
        """Números de Calibre que ya pasaron por el modo repaso (o tienen lecturas)."""
        return {f["libro"] for f in self._filas(
            "SELECT o.libro FROM obras o WHERE o.libro IS NOT NULL AND "
            "(o.repasado = 1 OR EXISTS (SELECT 1 FROM lecturas l WHERE l.obra = o.id))")}

    def asociar(self, id_externa, datos_libro):
        """Une un libro de fuera con el mismo libro, ya en la biblioteca.

        Si el libro de la biblioteca ya tenía su propia obra (con lecturas), las
        lecturas de fuera se pasan a ella y la de fuera desaparece. El recuerdo
        se conserva si el de la biblioteca no tenía.
        """
        externa = self.obra(id_externa)
        if not externa or not externa["de_fuera"]:
            return None
        destino = self.obra_de_libro(datos_libro["id"])
        cx = self._cx()
        try:
            with cx:
                if destino:
                    cx.execute("UPDATE lecturas SET obra = ? WHERE obra = ?",
                               (destino["id"], id_externa))
                    cx.execute(
                        "UPDATE obras SET recuerdo = COALESCE(recuerdo, ?), "
                        "decision = COALESCE(decision, ?), "
                        "palabras = COALESCE(palabras, ?), paginas = COALESCE(paginas, ?) "
                        "WHERE id = ?",
                        (externa["recuerdo"], externa["decision"], externa["palabras"],
                         externa["paginas"], destino["id"]))
                    cx.execute("DELETE FROM obras WHERE id = ?", (id_externa,))
                    resultado = destino["id"]
                else:
                    cx.execute("UPDATE obras SET libro = ?, titulo = ?, autores = ?, portada = '' "
                               "WHERE id = ?",
                               (datos_libro["id"], datos_libro["titulo"],
                                json.dumps(datos_libro["autores"], ensure_ascii=False),
                                id_externa))
                    resultado = id_externa
        finally:
            cx.close()
        if externa.get("portada"):
            (self.portadas / externa["portada"]).unlink(missing_ok=True)
        return resultado

    def borrar_obra_de_fuera(self, id_obra):
        obra = self.obra(id_obra)
        if not obra or not obra["de_fuera"]:
            return
        self._hacer("DELETE FROM obras WHERE id = ?", (id_obra,))
        if obra.get("portada"):
            (self.portadas / obra["portada"]).unlink(missing_ok=True)

    # --------------------------------------------------------------- lecturas
    @staticmethod
    def _lectura(fila):
        if not fila:
            return None
        fila["sensaciones"] = json.loads(fila.get("sensaciones") or "[]") \
            if "sensaciones" in fila else []
        return fila

    def lectura(self, id_lectura):
        return self._lectura(self._fila(
            "SELECT l.*, r.estrellas, r.sensaciones, r.ritmo, r.texto, r.procesada, "
            "r.lectura AS tiene_resena FROM lecturas l "
            "LEFT JOIN resenas r ON r.lectura = l.id WHERE l.id = ?", (id_lectura,)))

    def lecturas_de(self, id_obra):
        return [self._lectura(f) for f in self._filas(
            "SELECT l.*, r.estrellas, r.sensaciones, r.ritmo, r.texto, r.procesada, "
            "r.lectura AS tiene_resena FROM lecturas l "
            "LEFT JOIN resenas r ON r.lectura = l.id WHERE l.obra = ? "
            "ORDER BY COALESCE(l.terminado, l.empezado, l.creada) DESC, l.id DESC", (id_obra,))]

    def activa(self, id_obra):
        """La lectura en curso o pendiente (quiero / leyendo), si hay."""
        return self._fila("SELECT * FROM lecturas WHERE obra = ? AND estado IN ('quiero', 'leyendo') "
                          "ORDER BY id DESC LIMIT 1", (id_obra,))

    def _ya_leida(self, id_obra):
        return bool(self._fila("SELECT 1 AS si FROM lecturas WHERE obra = ? AND estado = 'leido'",
                               (id_obra,)))

    def quiero(self, id_obra, relectura=None):
        if self.activa(id_obra):
            return None
        if relectura is None:
            relectura = self._ya_leida(id_obra)
        ahora = self._ahora()
        return self._hacer(
            "INSERT INTO lecturas (obra, estado, relectura, creada, actualizada) "
            "VALUES (?, 'quiero', ?, ?, ?)", (id_obra, int(bool(relectura)), ahora, ahora))

    def quitar_quiero(self, id_obra):
        self._hacer("DELETE FROM lecturas WHERE obra = ? AND estado = 'quiero'", (id_obra,))

    def empezar(self, id_obra, fecha=None):
        """Empieza una lectura. Si estaba en «quiero leer», esa misma pasa a leyendo."""
        dia = a_fecha(fecha) or hoy()
        if dia > hoy():
            dia = hoy()
        fechada = int((hoy() - dia).days <= MARGEN_FECHA_REAL)
        ahora = self._ahora()
        activa = self.activa(id_obra)
        if activa and activa["estado"] == "leyendo":
            return activa["id"]
        if activa:
            self._hacer("UPDATE lecturas SET estado = 'leyendo', empezado = ?, fechada = ?, "
                        "actualizada = ? WHERE id = ?",
                        (dia.isoformat(), fechada, ahora, activa["id"]))
            return activa["id"]
        return self._hacer(
            "INSERT INTO lecturas (obra, estado, empezado, fechada, relectura, creada, actualizada) "
            "VALUES (?, 'leyendo', ?, ?, ?, ?, ?)",
            (id_obra, dia.isoformat(), fechada, int(self._ya_leida(id_obra)), ahora, ahora))

    def terminar(self, id_lectura, fecha=None):
        lectura = self.lectura(id_lectura)
        if not lectura:
            return
        dia = a_fecha(fecha) or hoy()
        empezado = a_fecha(lectura.get("empezado"))
        if empezado and dia < empezado:
            dia = empezado
        self._hacer("UPDATE lecturas SET estado = 'leido', terminado = ?, fraccion = 1, "
                    "actualizada = ? WHERE id = ?", (dia.isoformat(), self._ahora(), id_lectura))
        # Si acabas de leerlo, lo recuerdas bien: no tiene sentido preguntarlo.
        self._hacer("UPDATE obras SET recuerdo = 'bien', decision = NULL WHERE id = ?",
                    (lectura["obra"],))

    def abandonar(self, id_lectura, fraccion=None, fecha=None):
        dia = a_fecha(fecha) or hoy()
        try:
            fraccion = max(0.0, min(1.0, float(fraccion))) if fraccion not in (None, "") else None
        except (TypeError, ValueError):
            fraccion = None
        self._hacer("UPDATE lecturas SET estado = 'abandonado', terminado = ?, fraccion = ?, "
                    "actualizada = ? WHERE id = ?",
                    (dia.isoformat(), fraccion, self._ahora(), id_lectura))

    def ya_leido(self, id_obra, anio=None, recuerdo=None, decision=None):
        """Un libro leído en el pasado: sin fechas reales, no cuenta para el ritmo."""
        anio = str(anio or "").strip()
        terminado = anio if re.fullmatch(r"(19|20)\d\d", anio) else None
        ahora = self._ahora()
        # Si estaba en «quiero leer», eso deja de tener sentido (salvo que se
        # decida releerlo, que lo vuelve a poner justo abajo).
        self.quitar_quiero(id_obra)
        id_lectura = self._hacer(
            "INSERT INTO lecturas (obra, estado, terminado, fechada, creada, actualizada) "
            "VALUES (?, 'leido', ?, 0, ?, ?)", (id_obra, terminado, ahora, ahora))
        if recuerdo:
            self.fijar_recuerdo(id_obra, recuerdo, decision)
        return id_lectura

    def borrar_lectura(self, id_lectura):
        self._hacer("DELETE FROM lecturas WHERE id = ?", (id_lectura,))

    def corregir_fechas(self, id_lectura, empezado=None, terminado=None):
        """Arreglar a mano las fechas de una lectura (se te olvidó marcarla)."""
        lectura = self.lectura(id_lectura)
        if not lectura:
            return
        dia_empezado = a_fecha(empezado) or a_fecha(lectura["empezado"])
        if terminado and re.fullmatch(r"(19|20)\d\d", str(terminado).strip()):
            texto_terminado = str(terminado).strip()
        else:
            dia = a_fecha(terminado)
            texto_terminado = dia.isoformat() if dia else lectura["terminado"]
        # Una fecha de inicio de hace meses ya no es un dato fiable de ritmo.
        fechada = int(bool(dia_empezado) and (hoy() - dia_empezado).days <= MARGEN_FECHA_REAL
                      or (bool(lectura["fechada"]) and dia_empezado == a_fecha(lectura["empezado"])))
        self._hacer("UPDATE lecturas SET empezado = ?, terminado = ?, fechada = ?, actualizada = ? "
                    "WHERE id = ?",
                    (dia_empezado.isoformat() if dia_empezado else None, texto_terminado,
                     fechada, self._ahora(), id_lectura))

    # ---------------------------------------------------------------- reseñas
    def guardar_resena(self, id_lectura, estrellas=None, sensaciones=(), ritmo=None, texto=""):
        try:
            estrellas = round(float(estrellas) * 2) / 2 if estrellas not in (None, "", "0") else None
        except (TypeError, ValueError):
            estrellas = None
        if estrellas is not None:
            estrellas = max(0.5, min(5.0, estrellas))
        sensaciones = [s for s in sensaciones if s in NOMBRE_SENSACION]
        ritmo = ritmo if ritmo in NOMBRE_RITMO else None
        self._hacer(
            "INSERT INTO resenas (lectura, estrellas, sensaciones, ritmo, texto, procesada, actualizada) "
            "VALUES (?, ?, ?, ?, ?, 0, ?) ON CONFLICT(lectura) DO UPDATE SET "
            "estrellas = excluded.estrellas, sensaciones = excluded.sensaciones, "
            "ritmo = excluded.ritmo, texto = excluded.texto, procesada = 0, "
            "actualizada = excluded.actualizada",
            (id_lectura, estrellas, json.dumps(sensaciones), ritmo, (texto or "").strip(),
             self._ahora()))

    def pendientes_de_procesar(self):
        return self._exportar("WHERE r.procesada = 0")

    def marcar_procesadas(self, ids_lectura):
        ids = [int(i) for i in ids_lectura]
        if not ids:
            return 0
        huecos = ",".join("?" * len(ids))
        cx = self._cx()
        try:
            with cx:
                return cx.execute(f"UPDATE resenas SET procesada = 1 WHERE lectura IN ({huecos})",
                                  ids).rowcount
        finally:
            cx.close()

    def _exportar(self, condicion="", parametros=()):
        filas = self._filas(
            "SELECT l.id AS lectura, l.estado, l.empezado, l.terminado, l.fechada, l.relectura, "
            "l.fraccion, o.id AS obra, o.libro, o.titulo, o.autores, o.anio, o.recuerdo, "
            "o.decision, o.paginas, o.palabras, r.estrellas, r.sensaciones, r.ritmo, r.texto, "
            "r.procesada, r.actualizada AS resena_actualizada "
            "FROM lecturas l JOIN obras o ON o.id = l.obra "
            "LEFT JOIN resenas r ON r.lectura = l.id " + condicion +
            " ORDER BY COALESCE(l.terminado, l.empezado, l.creada) DESC", parametros)
        for fila in filas:
            fila["autores"] = json.loads(fila["autores"] or "[]")
            fila["sensaciones"] = [NOMBRE_SENSACION.get(s, s)
                                   for s in json.loads(fila["sensaciones"] or "[]")]
            fila["ritmo"] = NOMBRE_RITMO.get(fila["ritmo"], fila["ritmo"])
            fila["recuerdo"] = NOMBRE_RECUERDO.get(fila["recuerdo"], fila["recuerdo"])
            fila["de_fuera"] = fila["libro"] is None
        return filas

    def exportar(self):
        return self._exportar()

    # --------------------------------------------------------------- listados
    def por_estado(self, estado):
        filas = self._filas(
            "SELECT l.*, o.libro, o.titulo, o.autores, o.portada, o.paginas, o.palabras, "
            "o.recuerdo, o.decision, r.estrellas, r.lectura AS tiene_resena "
            "FROM lecturas l JOIN obras o ON o.id = l.obra "
            "LEFT JOIN resenas r ON r.lectura = l.id WHERE l.estado = ? "
            "ORDER BY COALESCE(l.terminado, l.empezado, l.creada) DESC, l.id DESC", (estado,))
        for fila in filas:
            fila["autores"] = json.loads(fila["autores"] or "[]")
            fila["de_fuera"] = fila["libro"] is None
        return filas

    def sin_resena(self):
        """Leídos que esperan reseña. Los soltados no: soltar es no pedir nada."""
        return [l for l in self.por_estado("leido")
                if not l["tiene_resena"] and l["decision"] != "soltar"
                and (l["fechada"] or l["recuerdo"] in (None, "bien"))]

    def estados_de_libros(self, ids):
        """Para pintar en la biblioteca qué has leído: {libro: 'leido'|...}."""
        if not ids:
            return {}
        huecos = ",".join("?" * len(ids))
        filas = self._filas(
            "SELECT o.libro, l.estado, l.id FROM lecturas l JOIN obras o ON o.id = l.obra "
            f"WHERE o.libro IN ({huecos}) ORDER BY l.id", list(ids))
        estados = {}
        for fila in filas:
            # Lo último manda, pero un «leído» no lo tapa un «quiero releer».
            if estados.get(fila["libro"]) == "leido" and fila["estado"] == "quiero":
                continue
            estados[fila["libro"]] = fila["estado"]
        return estados

    # --------------------------------------------------------------- aperturas
    def apuntar_apertura(self, id_libro):
        self._hacer("INSERT OR IGNORE INTO aperturas (libro, primera) VALUES (?, ?)",
                    (id_libro, hoy().isoformat()))

    def aperturas(self):
        return {f["libro"]: f["primera"] for f in self._filas("SELECT * FROM aperturas")}

    # --------------------------------------------------------- recomendaciones
    def recomendaciones(self):
        filas = self._filas("SELECT * FROM recomendaciones WHERE descartada = 0 ORDER BY id")
        for fila in filas:
            fila["autores"] = json.loads(fila["autores"] or "[]")
        return filas

    def recomendacion(self, id_recomendacion):
        fila = self._fila("SELECT * FROM recomendaciones WHERE id = ?", (id_recomendacion,))
        if fila:
            fila["autores"] = json.loads(fila["autores"] or "[]")
        return fila

    def poner_recomendaciones(self, elementos, reemplazar=True):
        ahora = self._ahora()
        cx = self._cx()
        try:
            with cx:
                if reemplazar:
                    cx.execute("DELETE FROM recomendaciones")
                for e in elementos:
                    tipo = "biblioteca" if e.get("libro") else "comprar"
                    cx.execute(
                        "INSERT INTO recomendaciones (tipo, libro, titulo, autores, anio, "
                        "portada_url, clave_ol, sinopsis, paginas, motivo, creada) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (tipo, e.get("libro"), e.get("titulo") or "",
                         json.dumps(e.get("autores") or [], ensure_ascii=False),
                         e.get("anio"), e.get("portada") or "", e.get("clave_ol") or "",
                         e.get("sinopsis") or "", e.get("paginas"),
                         e.get("motivo") or "", ahora))
        finally:
            cx.close()
        self.fijar_preferencia("recomendaciones_fecha", hoy().isoformat())

    def descartar_recomendacion(self, id_recomendacion):
        self._hacer("UPDATE recomendaciones SET descartada = 1 WHERE id = ?", (id_recomendacion,))

    # ------------------------------------------------------------- historial
    def historial(self):
        """Lo leído y abandonado, por años. Lo que no tiene año va aparte."""
        leidos = self.por_estado("leido") + self.por_estado("abandonado")
        resenas = {f["lectura"]: f for f in self._filas("SELECT lectura, sensaciones FROM resenas")}
        anios = {}
        for lectura in leidos:
            clave = (lectura["terminado"] or "")[:4] or "antes"
            anios.setdefault(clave, []).append(lectura)
        salida = []
        orden = sorted([c for c in anios if c != "antes"], reverse=True) + \
            (["antes"] if "antes" in anios else [])
        for clave in orden:
            lista = anios[clave]
            leidos_anio = [l for l in lista if l["estado"] == "leido"]
            estrellas = [l["estrellas"] for l in leidos_anio if l["estrellas"]]
            cuenta = {}
            for l in leidos_anio:
                for s in json.loads((resenas.get(l["id"]) or {}).get("sensaciones") or "[]"):
                    cuenta[s] = cuenta.get(s, 0) + 1
            salida.append({
                "anio": clave,
                "lecturas": lista,
                "leidos": len(leidos_anio),
                "abandonados": len(lista) - len(leidos_anio),
                "media": round(sum(estrellas) / len(estrellas), 1) if estrellas else None,
                "sensaciones": [NOMBRE_SENSACION[s] for s, _ in
                                sorted(cuenta.items(), key=lambda p: -p[1])[:3]
                                if s in NOMBRE_SENSACION],
            })
        return salida

    # ---------------------------------------------------------------- contador
    def contador(self, progreso=None, sin_leer=0, dia=None):
        """«A este ritmo podrás leer N libros más en tu vida.»

        El ritmo (en **palabras al día**, no en libros: un tocho no vale lo
        mismo que una novela corta) sale de dos fuentes que se mezclan:

        - **Lo medido**: las lecturas con fechas reales del último año. Lo que
          llevas del libro en curso también cuenta (`progreso`: {obra:
          fracción}), y lo leído de un abandonado también.
        - **Lo recordado**: los libros apuntados con su año (sin fechas, o con
          fechas de antes del último año). Cada año da su ritmo, palabras ÷
          días, y pesa según lo cerca que queda (`PESO_POR_ANTIGUEDAD`). Un año
          sin nada apuntado no es un año sin leer, es un año sin datos: no
          entra. Si lo medido ya cubre parte de un año, ese año solo cuenta
          por el trozo de antes.

        Los años que quedan salen de la tabla del INE para tu edad.
        """
        dia = dia or hoy()
        progreso = progreso or {}
        nacimiento = self.preferencia("nacimiento")
        filas = self._filas(
            "SELECT l.*, o.palabras, o.paginas FROM lecturas l JOIN obras o ON o.id = l.obra "
            "WHERE l.estado IN ('leido', 'abandonado', 'leyendo')")

        def palabras_de(fila):
            if fila.get("palabras"):
                return fila["palabras"]
            if fila.get("paginas"):
                return fila["paginas"] * PALABRAS_POR_PAGINA
            return None

        # Lo que mide un libro tuyo: todo lo terminado cuya longitud se sabe,
        # sea de cuando sea (una obra releída cuenta una vez).
        largos = {f["obra"]: palabras_de(f) for f in filas if f["estado"] == "leido"}
        conocidas = [p for p in largos.values() if p]
        longitud = round(sum(conocidas) / len(conocidas)) if conocidas else LONGITUD_POR_DEFECTO

        def leidas_de(fila):
            mide = palabras_de(fila) or longitud
            if fila["estado"] == "leido":
                return mide
            if fila["estado"] == "abandonado":
                return mide * (fila["fraccion"] or 0)
            return mide * float(progreso.get(fila["obra"], 0) or 0)

        # ------------------------------------------------------ lo medido
        inicio_ventana = date.fromordinal(dia.toordinal() - VENTANA_DIAS)
        palabras, primer_dia, terminados, medidas = 0, None, 0, set()
        for fila in filas:
            empezado = a_fecha(fila["empezado"])
            terminado = a_fecha(fila["terminado"])
            if not fila["fechada"] or not empezado:
                continue
            if terminado and terminado < inicio_ventana:
                continue
            medidas.add(fila["id"])
            primer_dia = min(primer_dia or empezado, empezado)
            palabras += leidas_de(fila)
            terminados += fila["estado"] == "leido"
        dias = 0
        if primer_dia:
            primer_dia = max(primer_dia, inicio_ventana)
            dias = max(1, (dia - primer_dia).days + 1)
        ritmo_medido = palabras / dias if dias else 0.0

        # ---------------------------------------------------- lo recordado
        # Lo medido tapa su propio trozo de calendario; cada año recordado
        # cuenta solo hasta ahí.
        tope = primer_dia or date.fromordinal(dia.toordinal() + 1)
        por_anio = {}
        for fila in filas:
            if fila["id"] in medidas or fila["estado"] == "leyendo":
                continue
            anio = str(fila["terminado"] or "")[:4]
            if not anio.isdigit():
                continue
            libro = por_anio.setdefault(int(anio), {"palabras": 0.0, "libros": 0})
            libro["palabras"] += leidas_de(fila)
            libro["libros"] += fila["estado"] == "leido"
        anios = []
        for anio, datos in sorted(por_anio.items(), reverse=True):
            peso = PESO_POR_ANTIGUEDAD.get(dia.year - anio, 0)
            desde = date(anio, 1, 1)
            hasta = min(date(anio, 12, 31), tope - date.resolution, dia)
            tramo = (hasta - desde).days + 1
            if not peso or tramo < DIAS_MINIMOS_TRAMO or not datos["palabras"]:
                continue
            anios.append({
                "anio": anio,
                "hasta": hasta if hasta < date(anio, 12, 31) else None,
                "libros": datos["libros"],
                "ritmo": datos["palabras"] / tramo,
                "peso": peso * tramo / 365,
                "fiable": peso >= 0.7,
            })

        # ------------------------------------------------------- la mezcla
        peso_medido = PESO_MEDIDO * dias / 365 if dias else 0.0
        peso_total = peso_medido + sum(a["peso"] for a in anios)
        ritmo = ((ritmo_medido * peso_medido + sum(a["ritmo"] * a["peso"] for a in anios))
                 / peso_total) if peso_total else 0.0
        fuentes = []
        if dias:
            fuentes.append({"que": f"Medido ({dias} día{'s' if dias != 1 else ''} con fechas)",
                            "libros": terminados, "ritmo": round(ritmo_medido),
                            "parte": round(100 * peso_medido / peso_total)})
        for a in anios:
            corte = f" (hasta el {a['hasta'].day}/{a['hasta'].month})" if a["hasta"] else ""
            fuentes.append({"que": f"{a['anio']}{corte}, recordado", "libros": a["libros"],
                            "ritmo": round(a["ritmo"]),
                            "parte": round(100 * a["peso"] / peso_total)})
        # Para dejar de «calibrar» hacen falta 3 libros, medidos o de años
        # que todavía se recuerdan bien.
        base = terminados + sum(a["libros"] for a in anios if a["fiable"])

        resultado = {
            "nacimiento": nacimiento,
            "terminados": terminados,
            "base": base,
            "dias": dias,
            "ritmo": round(ritmo),
            "ritmo_medido": round(ritmo_medido),
            "fuentes": fuentes,
            "minutos_dia": round(ritmo / PALABRAS_POR_MINUTO) if ritmo else 0,
            "longitud": longitud,
            "longitud_medida": bool(conocidas),
            "calibrando": base < MINIMO_PARA_CALIBRAR,
            "faltan": max(0, MINIMO_PARA_CALIBRAR - base),
            "sin_leer": sin_leer,
            "libros": None,
            "anos": None,
            "libros_al_ano": None,
            "con_15_minutos": None,
        }
        if not nacimiento:
            return resultado
        try:
            anio = int(nacimiento)
        except ValueError:
            return resultado
        # Sin día exacto de nacimiento, el 1 de julio: como mucho medio año de error.
        edad = (dia - date(anio, 7, 1)).days / 365.25
        anos = anos_que_quedan(edad)
        dias_vida = anos * 365.25
        resultado["edad"] = int(edad)
        resultado["anos"] = round(anos, 1)
        resultado["dias_vida"] = round(dias_vida)
        if ritmo > 0:
            resultado["libros"] = int(ritmo * dias_vida / longitud)
            resultado["libros_al_ano"] = round(ritmo * 365.25 / longitud, 1)
            extra = 15 * PALABRAS_POR_MINUTO * dias_vida / longitud
            resultado["con_15_minutos"] = int(ritmo * dias_vida / longitud + extra)
        return resultado
