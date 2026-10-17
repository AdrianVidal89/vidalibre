"""Las operaciones completas de la biblioteca.

Aquí viven las acciones que tocan a la vez el catálogo, los ficheros del disco
y el motor de Calibre: subir un libro, guardar una edición, convertir, enviar
al Kindle, borrar. La regla es siempre la misma y es la que evita catálogos
roños: **primero los ficheros, después la base de datos**, y si algo falla a
medias se deshace lo que se pueda. Un libro apuntado en el catálogo cuyo
fichero no existe es un fantasma que luego hay que cazar a mano.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import drm, ficheros, motor
from .catalogo import ErrorCatalogo, ahora, ordenar_autor

# De qué formato conviene partir para convertir: el EPUB es el que mejor
# estructura trae; el PDF, el peor (páginas fijas, sin capítulos).
PREFERENCIA_ORIGEN = ["EPUB", "AZW3", "MOBI", "AZW", "FB2", "DOCX", "HTMLZ", "HTML", "RTF", "TXT", "PDF"]


class Biblioteca:
    def __init__(self, catalogo, firma="Vidalibre", puente=None):
        self.catalogo = catalogo
        self.raiz = catalogo.raiz
        self.firma = firma
        # Cuando la biblioteca está en una carpeta de red, esta copia no puede
        # escribir en el catálogo: deja el fichero en su sitio y le pide al NAS
        # que lo apunte. Ver `puente.py`.
        self.puente = puente

    # ------------------------------------------------------------------ ayudas
    def _opf(self, id_libro):
        libro = self.catalogo.libro(id_libro)
        if libro:
            try:
                ficheros.escribir_opf(self.raiz, libro, self.firma)
            except OSError:
                pass
        return libro

    def mejor_origen(self, libro, formato_destino=None):
        """El fichero del que conviene partir para convertir este libro."""
        disponibles = {f["formato"]: f for f in libro.get("ficheros", [])}
        for formato in PREFERENCIA_ORIGEN:
            if formato_destino and formato == formato_destino.upper():
                continue
            if formato in disponibles:
                ruta = ficheros.ruta_fichero(self.raiz, libro, formato)
                if ruta and ruta.is_file():
                    return formato, ruta
        return None, None

    def posibles_duplicados(self, titulo, autores):
        """Busca si ya hay algo muy parecido, para avisar antes de duplicar."""
        encontrados, _ = self.catalogo.libros(busqueda=titulo[:40], por_pagina=6)
        autor_principal = (autores or [""])[0].lower()
        return [
            l for l in encontrados
            if l["titulo"].strip().lower() == titulo.strip().lower()
            or (autor_principal and autor_principal in " ".join(l["autores"]).lower()
                and l["titulo"][:25].lower() == titulo[:25].lower())
        ]

    # ------------------------------------------------------------------- subida
    def anadir_libro(self, ruta_origen, extension, metadatos=None, portada=None,
                     mover=False):
        """Mete un fichero nuevo en la biblioteca como libro nuevo.

        Devuelve (id_libro, avisos).
        """
        ruta_origen = Path(ruta_origen)
        avisos = []
        datos = dict(metadatos or {})
        if not datos.get("titulo") or not datos.get("autores"):
            leidos = motor.leer_metadatos(ruta_origen)
            for clave, valor in leidos.items():
                if not datos.get(clave):
                    datos[clave] = valor
        titulo = (datos.get("titulo") or ruta_origen.stem).strip()
        autores = [a for a in (datos.get("autores") or []) if a.strip()] or ["Autor desconocido"]

        candado = drm.estado(ruta_origen)
        if candado["protegido"]:
            avisos.append(drm.mensaje(candado, "abrir"))

        with self.catalogo.escribir() as cx:
            # El id hace falta para el nombre de la carpeta, y la carpeta para
            # mover el fichero: se crea la fila, se calcula la ruta y se corrige.
            id_libro = self.catalogo.crear_libro(
                cx, titulo, autores, "temporal", publicado=datos.get("publicado")
            )
            ruta_rel = ficheros.carpeta_relativa(titulo, autores[0], id_libro)
            self.catalogo.fijar_ruta(cx, id_libro, ruta_rel)
            self.catalogo.guardar_metadatos(cx, id_libro, {
                "titulo": titulo,
                "autores": autores,
                "serie": datos.get("serie") or "",
                "indice_serie": datos.get("indice_serie") or 1.0,
                "editorial": datos.get("editorial") or "",
                "etiquetas": datos.get("etiquetas") or [],
                "idiomas": datos.get("idiomas") or [],
                "descripcion": datos.get("descripcion") or "",
                "identificadores": datos.get("identificadores") or {},
                "valoracion": datos.get("valoracion") or 0,
                **({"publicado": datos["publicado"]} if datos.get("publicado") else {}),
            })

            carpeta = ficheros.asegurar_carpeta(self.raiz / ruta_rel)
            base = ficheros.nombre_base(titulo, autores[0])
            destino = carpeta / f"{base}.{extension.lower()}"
            try:
                if mover:
                    shutil.move(str(ruta_origen), str(destino))
                else:
                    shutil.copy2(str(ruta_origen), str(destino))
            except OSError as ex:
                raise ErrorCatalogo(f"No he podido guardar el fichero: {ex}") from ex
            self.catalogo.anadir_formato(
                cx, id_libro, extension, base, destino.stat().st_size
            )

            # Portada: la que venga dada, o la que traiga el libro dentro.
            imagen = portada
            if not imagen:
                with tempfile.TemporaryDirectory() as tmp:
                    imagen = motor.extraer_portada(destino, Path(tmp) / "cover.jpg")
            if imagen:
                libro_parcial = {"id": id_libro, "ruta": ruta_rel}
                ficheros.guardar_portada(self.raiz, libro_parcial, imagen)
                self.catalogo.guardar_metadatos(cx, id_libro, {"tiene_portada": True})
            else:
                avisos.append("El libro no traía imagen de cubierta: se ha quedado sin portada.")

        self._opf(id_libro)
        return id_libro, avisos

    def anadir_formato(self, id_libro, ruta_origen, extension, mover=False,
                       sustituir=True):
        """Añade otro formato al mismo libro (un libro, varios formatos)."""
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        extension = extension.lower().lstrip(".")
        if extension.upper() in libro["formatos"] and not sustituir:
            raise ErrorCatalogo(f"El libro ya tiene un {extension.upper()}")
        carpeta = ficheros.asegurar_carpeta(self.raiz / libro["ruta"])
        base = ficheros.nombre_base(libro["titulo"], (libro["autores"] or ["Autor desconocido"])[0])
        destino = carpeta / f"{base}.{extension}"
        if Path(ruta_origen).resolve() != destino.resolve():
            if mover:
                shutil.move(str(ruta_origen), str(destino))
            else:
                shutil.copy2(str(ruta_origen), str(destino))

        if self.puente or not self.catalogo.escritura_posible():
            if not self.puente:
                raise ErrorCatalogo(
                    "El fichero está guardado, pero desde aquí no puedo apuntarlo en el "
                    "catálogo (la biblioteca está en una carpeta de red)."
                )
            self.puente.registrar_formato(id_libro, extension)
            return destino

        with self.catalogo.escribir() as cx:
            self.catalogo.anadir_formato(cx, id_libro, extension, base, destino.stat().st_size)
        self._opf(id_libro)
        return destino

    # ------------------------------------------------------------------ edición
    def guardar_edicion(self, id_libro, datos, portada=None, quitar_portada=False,
                        incrustar=False):
        """Guarda la ficha editada: catálogo, carpeta, portada y metadata.opf."""
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")

        titulo_nuevo = (datos.get("titulo") or libro["titulo"]).strip()
        autores_nuevos = datos.get("autores") or libro["autores"] or ["Autor desconocido"]
        cambia_carpeta = (
            titulo_nuevo != libro["titulo"]
            or (autores_nuevos[:1] or [""])[0] != (libro["autores"][:1] or [""])[0]
        )

        nueva_rel, nuevo_base = libro["ruta"], None
        if cambia_carpeta:
            nueva_rel, nuevo_base = ficheros.mover_libro(
                self.raiz, libro["ruta"], titulo_nuevo, autores_nuevos[0],
                id_libro, libro["ficheros"],
            )

        with self.catalogo.escribir() as cx:
            self.catalogo.guardar_metadatos(cx, id_libro, datos)
            if cambia_carpeta:
                self.catalogo.fijar_ruta(cx, id_libro, nueva_rel)
                for fichero in libro["ficheros"]:
                    self.catalogo.anadir_formato(
                        cx, id_libro, fichero["formato"], nuevo_base, fichero["tamano"]
                    )
            if quitar_portada:
                ficheros.borrar_portada(self.raiz, {**libro, "ruta": nueva_rel})
                self.catalogo.guardar_metadatos(cx, id_libro, {"tiene_portada": False})
            elif portada:
                ficheros.guardar_portada(self.raiz, {**libro, "ruta": nueva_rel}, portada)
                self.catalogo.guardar_metadatos(cx, id_libro, {"tiene_portada": True})

        actualizado = self._opf(id_libro)

        if incrustar and actualizado:
            # Escribir los metadatos dentro de cada fichero del libro.
            cubierta = ficheros.ruta_portada(self.raiz, actualizado)
            for fichero in actualizado["ficheros"]:
                ruta = ficheros.ruta_fichero(self.raiz, actualizado, fichero["formato"])
                if ruta and ruta.is_file() and fichero["formato"] != "PDF":
                    motor.escribir_metadatos(ruta, actualizado, portada=cubierta)
        return actualizado

    def refrescar_portada_desde_fichero(self, id_libro, formato=None):
        """Saca la portada de dentro del libro y la pone como portada de la ficha."""
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        formato, ruta = (formato, ficheros.ruta_fichero(self.raiz, libro, formato)) if formato \
            else self.mejor_origen(libro)
        if not ruta or not ruta.is_file():
            return False
        with tempfile.TemporaryDirectory() as tmp:
            imagen = motor.extraer_portada(ruta, Path(tmp) / "cover.jpg")
        if not imagen:
            return False
        ficheros.guardar_portada(self.raiz, libro, imagen)
        with self.catalogo.escribir() as cx:
            self.catalogo.guardar_metadatos(cx, id_libro, {"tiene_portada": True})
        self._opf(id_libro)
        return True

    def releer_metadatos_del_fichero(self, id_libro, formato=None):
        """Vuelve a leer título, autor, serie... de dentro del fichero del libro."""
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        if formato:
            ruta = ficheros.ruta_fichero(self.raiz, libro, formato)
        else:
            _formato, ruta = self.mejor_origen(libro)
        if not ruta or not ruta.is_file():
            return None
        return motor.leer_metadatos(ruta)

    # ------------------------------------------------------------------- borrado
    def borrar_formato(self, id_libro, formato):
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        if len(libro["ficheros"]) <= 1:
            raise ErrorCatalogo(
                "Es el único fichero del libro. Para quitarlo, borra el libro entero."
            )
        ficheros.borrar_fichero_formato(self.raiz, libro, formato)
        with self.catalogo.escribir() as cx:
            self.catalogo.quitar_formato(cx, id_libro, formato)
        self._opf(id_libro)
        return True

    def borrar_libro(self, id_libro):
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        ficheros.borrar_carpeta_libro(self.raiz, libro)
        with self.catalogo.escribir() as cx:
            self.catalogo.borrar_libro(cx, id_libro)
        return libro["titulo"]

    # --------------------------------------------------------------- conversión
    def convertir(self, id_libro, formato_destino, trabajo=None, guardar=True,
                  carpeta_salida=None, origen_formato=None):
        """Convierte un libro a otro formato.

        Si `guardar` está puesto, el formato nuevo se queda **dentro del libro**
        (un libro con varios formatos). Si se da `carpeta_salida`, además deja
        una copia ahí.
        Devuelve (ok, ruta_resultado, mensaje).
        """
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        formato_destino = formato_destino.lower().lstrip(".")

        if origen_formato:
            formato_origen = origen_formato.upper()
            ruta_origen = ficheros.ruta_fichero(self.raiz, libro, formato_origen)
        else:
            formato_origen, ruta_origen = self.mejor_origen(libro, formato_destino)
        if not ruta_origen or not ruta_origen.is_file():
            return False, None, "No encuentro ningún fichero de este libro en el disco."

        # Un libro con candado no se puede convertir, y conviene decirlo antes
        # de tener a alguien esperando a una conversión que iba a fallar con un
        # error incomprensible de Calibre.
        candado = drm.estado(ruta_origen)
        if candado["protegido"]:
            return False, None, drm.mensaje(candado, "convertir")

        if trabajo:
            trabajo.anotar(f"{libro['titulo']}: {formato_origen} → {formato_destino.upper()}")

        base = ficheros.nombre_base(libro["titulo"], (libro["autores"] or ["x"])[0])
        # El Markdown no se guarda dentro de la biblioteca: no es un formato de
        # libro, es texto para leer con una IA o en Obsidian.
        guardar_en_biblioteca = guardar and formato_destino != "md"

        with tempfile.TemporaryDirectory() as tmp:
            temporal = Path(tmp) / f"{base}.{formato_destino}"
            ok, salida = motor.convertir(
                ruta_origen, temporal, formato_destino,
                metadatos={
                    "titulo": libro["titulo"],
                    "autores": libro["autores"],
                    "editorial": libro["editorial"],
                    "publicado": libro["publicado"],
                    "serie": libro["serie"],
                },
            )
            if trabajo and salida:
                for linea in salida.strip().split("\n")[-6:]:
                    if linea.strip():
                        trabajo.anotar(linea.strip()[:160])
            if not ok:
                return False, None, (salida or "").strip()[-600:] or "La conversión ha fallado."

            destino_final = None
            if guardar_en_biblioteca:
                destino_final = self.anadir_formato(
                    id_libro, temporal, formato_destino, mover=False
                )
            if carpeta_salida:
                carpeta_salida = ficheros.asegurar_carpeta(Path(carpeta_salida))
                copia = carpeta_salida / f"{base}.{formato_destino}"
                shutil.copy2(str(temporal), str(copia))
                destino_final = destino_final or copia
            if not destino_final:
                # Ni se guarda en la biblioteca ni hay carpeta: se deja en un
                # sitio estable para que la web lo pueda ofrecer de descarga.
                guardados = ficheros.asegurar_carpeta(Path(tempfile.gettempdir()) / "vidalibre")
                destino_final = guardados / f"{base}.{formato_destino}"
                shutil.copy2(str(temporal), str(destino_final))

        return True, destino_final, "Convertido."

    def asegurar_formato(self, id_libro, formatos_validos, trabajo=None):
        """Devuelve un fichero del libro en uno de esos formatos, convirtiendo
        si hace falta. Sirve para el envío al Kindle."""
        libro = self.catalogo.libro(id_libro)
        if not libro:
            raise ErrorCatalogo("Ese libro no existe")
        formatos_validos = [f.upper() for f in formatos_validos]
        for formato in formatos_validos:
            if formato in libro["formatos"]:
                ruta = ficheros.ruta_fichero(self.raiz, libro, formato)
                if ruta and ruta.is_file():
                    return formato, ruta, False
        destino = formatos_validos[0]
        ok, ruta, mensaje = self.convertir(id_libro, destino.lower(), trabajo=trabajo)
        if not ok:
            raise ErrorCatalogo(mensaje)
        return destino, ruta, True
