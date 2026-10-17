# Vidalibre

La biblioteca de libros de casa: subir, ordenar, editar, convertir y pasar al
Kindle. Trabaja sobre **la misma biblioteca de Calibre de siempre**, sin copiar
ni mover nada, así que todo lo que hay dentro se sigue abriendo con Calibre.

Sustituye a dos cosas a la vez: a **Calibre-Web** (la biblioteca en el
navegador) y al **conversor de libros del escritorio** (`epub2mobi`), cuyo
motor va entero aquí dentro.

![Vidalibre](vidalibre/estatico/icono-512.png)

## Qué hace

- **Biblioteca**: rejilla de portadas, buscador que mira título, autor, serie,
  etiquetas, editorial y hasta la descripción, y estantes por autor, serie,
  etiqueta, editorial e idioma.
- **Subir libros**: EPUB, MOBI, AZW3, PDF, FB2, DOCX, CBZ… De cada fichero lee
  por dentro el título, el autor, la serie, las etiquetas y la portada, y lo
  coloca en `Autor/Título (id)/` como hace Calibre.
- **Editar propiedades**: título, autores, serie y número, editorial, fecha,
  idiomas, etiquetas, valoración, descripción, ISBN y otros identificadores, y
  la portada. Si cambia el título o el autor, **la carpeta y los ficheros se
  renombran solos**. Opcionalmente graba esos datos *dentro* de los ficheros.
- **Descargar** cualquier formato que tenga el libro.
- **Convertir** entre AZW3, MOBI, EPUB, PDF, DOCX, TXT y Markdown, de uno en
  uno o en bloque con un filtro ("todos los de Sanderson que no tengan EPUB").
  El formato nuevo **se queda dentro del libro**: un libro puede llevar EPUB,
  AZW3 y MOBI a la vez.
- **Kindle por USB** (solo en el ordenador): ver el aparato, mandarle libros
  convirtiéndolos si hace falta, prepararles la miniatura de portada y reponer
  las portadas de los libros que ya tenía dentro y salían en gris.
- **OPDS**: KOReader, Moon+ Reader o Panels se conectan a `/opds` y navegan la
  biblioteca y se descargan los libros solos.

## Las dos copias

El mismo código corre en dos sitios, con el mismo catálogo:

| | Dónde | Para qué | Kindle |
|---|---|---|---|
| **NAS** | contenedor en el NAS, puerto 8084 | la biblioteca de casa, siempre encendida | no (un servidor no ve los USB del portátil) |
| **Ordenador** | `vidalibre` o el icono del menú | lo mismo **y** el Kindle enchufado | sí |

## Instalar en el ordenador

```bash
./instalar.sh
```

Deja el comando `vidalibre` y la entrada «Vidalibre — biblioteca» en el menú.
Necesita **Calibre** instalado (`sudo pacman -S calibre` en Arch/CachyOS): de
ahí salen `ebook-convert` y `ebook-meta`, que son el motor. Los ajustes quedan
en `~/.config/vidalibre.conf` (qué carpeta es la biblioteca y en qué puerto se
abre). Para quitarlo: `./instalar.sh --desinstalar`.

## Instalar en el NAS

```bash
cp .env.example .env     # y ajustar BIBLIOTECA y PUERTO
sh deploy.sh             # construir y levantar
sh deploy.sh estado      # ¿está vivo?
sh deploy.sh logs 80
sh deploy.sh parar
```

La imagen lleva Calibre dentro, así que **la primera construcción tarda** y
ocupa. `deploy.sh` guarda una copia del catálogo (`metadata.db`) antes de cada
despliegue, en `datos/copias/`, y conserva las diez últimas.

## Cómo está hecho

```
vidalibre/
  catalogo.py     el metadata.db de Calibre: leer y escribir
  ficheros.py     carpetas, nombres, portadas y metadata.opf
  motor.py        ebook-convert y ebook-meta + la cola de trabajos
  dispositivo.py  el Kindle por USB
  servicio.py     las operaciones completas (subir, editar, convertir, enviar)
  web.py          las pantallas
  opds.py         el catálogo para las apps de lectura
```

Cuatro decisiones que explican el resto:

1. **No hay base de datos propia.** El catálogo es el `metadata.db` de Calibre,
   tal cual. Se puede volver a Calibre-Web en cualquier momento sin migrar
   nada, y Calibre de escritorio sigue viendo la biblioteca igual.
2. **Los disparadores del catálogo llaman a funciones que pone Calibre**
   (`title_sort()`, `uuid4()`). Cada conexión las registra; sin eso, insertar
   un libro falla.
3. **Primero el disco, después el catálogo.** Un libro apuntado cuyo fichero no
   existe es un fantasma que hay que cazar a mano.
4. **Las conversiones van de una en una**, en una cola. El NAS tiene 3,7 GB de
   memoria para todo y una conversión arranca un Calibre entero.

## Si algo va mal

| Qué pasa | Qué mirar |
|---|---|
| «No encuentro la biblioteca» | que `BIBLIOTECA` apunte a la carpeta con `metadata.db` dentro |
| Las conversiones fallan todas | ¿hay Calibre? `/estado` dice la versión del motor |
| Un libro sale con el botón de descarga rayado | el catálogo lo apunta pero el fichero no está en disco |
| Va lento al listar | las miniaturas se cachean en `datos/miniaturas`; la primera vuelta es la lenta |

## Licencia y de dónde sale

GPL-3.0. Vidalibre reutiliza trabajo de dos programas libres:

- **[Calibre](https://calibre-ebook.com/)** (Kovid Goyal, GPL-3.0): el motor de
  conversión y de lectura de metadatos, y el formato de la biblioteca.
- **[Calibre-Web](https://github.com/janeczku/calibre-web)** (GPL-3.0): de ahí
  vienen los criterios de nombres de fichero y carpetas, el tratamiento del
  catálogo y la estructura del catálogo OPDS.
