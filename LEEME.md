# Vidalibre

La biblioteca de libros de casa: subir, ordenar, editar, convertir y pasar al
Kindle. Trabaja sobre **la misma biblioteca de Calibre de siempre**, sin copiar
ni mover nada, así que todo lo que hay dentro se sigue abriendo con Calibre.

Sustituye a dos cosas a la vez: a **Calibre-Web** (la biblioteca en el
navegador) y al **conversor de libros del escritorio** (`epub2mobi`), cuyo
motor va entero aquí dentro.

![Vidalibre](vidalibre/estatico/icono-512.png)

## Qué hace

- **Leer, con un lector propio estilo Kindle**: páginas que pasan con un toque
  o deslizando, letra, interlineado, márgenes y color a elegir (claro, sepia,
  noche y negro), índice, búsqueda dentro del libro, cuánto queda del capítulo
  y memoria de por dónde ibas, compartida entre el móvil, la tablet y el
  ordenador.
- **Biblioteca**: arriba, el libro que tienes **a medias**, la fila de
  *continuar leyendo* y lo último que entró. Debajo, **todos los libros**, que
  se pueden **agrupar por género, autor o año** con un toque: al pulsar se
  rehace solo esa parte, sin recargar la página ni perder el sitio. Buscador
  que mira título, autor, serie, etiquetas, editorial y hasta la descripción,
  y estantes por autor, serie, etiqueta, editorial e idioma.
- **Ficha de libro** con la sinopsis, los datos en fichas y filas de **obras
  relacionadas**: de la misma serie, del mismo autor y del mismo género.
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
- **Kindle y Kobo por USB** (solo en el ordenador): ver el aparato, mandarle
  libros convirtiéndolos si hace falta, prepararle al Kindle la miniatura de
  portada, reponer las portadas grises y **sincronizar por dónde vas leyendo**.
  Si el lector está enchufado pero el escritorio no lo ha abierto, se detecta
  y se ofrece **conectarlo desde la propia aplicación**. La pestaña **Mi
  lector** sale siempre: en la copia del NAS, para explicar que eso se hace en
  el ordenador y llevarte allí de un clic.
- **Traer libros nuevos**: búsqueda en Project Gutenberg y Standard Ebooks, y
  relleno de fichas, **sinopsis** y portadas desde Open Library.
- **Aviso de DRM**: si un libro viene con candado, se dice claro y pronto.
- **Diario de lecturas**: qué has leído, reseñas con estrellas y sensaciones,
  libros que leíste y no tienes, y un contador de cuántos libros te quedan en
  la vida a tu ritmo. Ver *El diario de lecturas*.
- **OPDS**: KOReader, Moon+ Reader o Panels se conectan a `/opds` y navegan la
  biblioteca y se descargan los libros solos.

## El diario de lecturas

La pestaña **Lecturas** lleva la cuenta de lo que lees, al estilo de Goodreads y
StoryGraph pero en casa y sin red social:

- **Estados**: *quiero leer*, *leyendo*, *leído* y **abandonado** (Goodreads no
  lo tiene y es su queja más repetida). Cada relectura es una lectura aparte,
  con sus fechas y su reseña.
- **Reseña**: estrellas de media en media, **cómo te hizo sentir** (me removió,
  me dejó pensando, evasión pura, angustioso, aburrido, reconfortante), ritmo y
  texto libre. Las estrellas se copian también a la valoración de Calibre.
- **Cuánto lo recuerdas**, para lo leído hace años: bien, borroso u olvidado. Si
  es borroso u olvidado se decide **releerlo** (pasa a *quiero leer*) o
  **soltarlo** (queda en el historial, sin reseña y sin pesar en las
  recomendaciones).
- **Modo repaso**: recorre la biblioteca libro a libro para la carga inicial.
- **Libros de fuera**: los que leíste y no tienes se buscan en Open Library (la
  ficha, nunca el libro), llevan la marca *No está en tu biblioteca* y no salen
  en la biblioteca, el buscador ni el OPDS. Si luego lo subes, Vidalibre lo
  reconoce (por ISBN o título y autor) y propone **unirlos**.
- **Propone, no apunta**: si abres un libro en el lector y pasas del 5 % sin
  haberlo marcado, pregunta «¿lo has empezado?» con el día en que lo abriste.

### El contador

*A este ritmo podrás leer N libros más en tu vida.* Las cuentas:

- El ritmo mezcla dos fuentes. **Lo medido**: lecturas con fechas reales del
  último año. **Lo recordado**: lo apuntado como leído en tal año; N libros de
  tal longitud en un año son tantas palabras al día. Cada año recordado pesa
  menos cuanto más lejos queda (este año y el pasado 100 %, hace dos 70 %, tres
  35 %, cuatro 15 %, cinco o más nada), porque la memoria se deja libros y un
  año medio olvidado parecería un año de leer poco. Un año sin nada apuntado no
  cuenta como cero: no entra. Un año de fechas reales pesa el doble que uno
  recordado. En la pantalla, «De dónde sale tu ritmo» enseña el desglose.
- El ritmo va en **palabras al día**, contadas en el EPUB (el lector ya las
  cuenta); para un libro de fuera, páginas × 275. Ventana: el último año.
- Los años que quedan salen de la **tabla de mortalidad del INE 2024** (hombres,
  España) para tu edad, interpolada; se ajusta sola según cumples años.
- Libros = ritmo × días que quedan ÷ lo que mide de media un libro tuyo.
- Hasta tener 3 libros de base (con fecha, o de los dos últimos años) dice
  **calibrando**.

### Dónde se guarda

En `lecturas.db`, en la carpeta de datos, **aparte del catálogo**: así caben los
libros de fuera sin que Calibre los vea, y el catálogo sigue intacto. El
despliegue le hace copia igual que a `metadata.db`. Solo existe en la biblioteca
de casa; la copia del portátil lleva allí.

### Para que Claude procese las reseñas

Las reseñas nacen *pendientes de procesar*. Sin contraseña, como el resto:

| | |
|---|---|
| `GET /api/lecturas` | todo el diario, el contador y las recomendaciones |
| `GET /api/lecturas/pendientes` | reseñas sin procesar |
| `POST /api/lecturas/procesadas` | `{"lecturas": [ids]}` las marca como leídas |
| `POST /api/lecturas/recomendaciones` | carga la fila «Para ti»: `{"recomendaciones": [{"libro": 12, "motivo": "…"}, {"titulo": "…", "autores": ["…"], "anio": 1999, "portada": "url", "clave_ol": "/works/…", "motivo": "…"}]}` (sustituye a las anteriores salvo `"reemplazar": false`) |

## El lector

No usa ninguna de las bibliotecas de JavaScript que se llevan para esto
(epub.js y compañía). Esas se descargan el libro **entero** al navegador, lo
descomprimen en memoria y lo pintan: con un libro de 12 MB eso es medio móvil
ocupado y varios segundos de espera por WiFi. Aquí el EPUB se abre en el
servidor, que manda **solo el capítulo que toca** ya preparado; el móvil recibe
unas decenas de kilobytes cada vez.

La paginación la hace el navegador repartiendo el texto en columnas del ancho
de la pantalla, y pasar página es correr esa cinta de columnas. La altura de
la página se ajusta a un número entero de líneas: sin eso, la última línea de
cada página sale cortada por la mitad, que es lo que delata a los lectores
hechos con prisa.

Por dónde vas se guarda en `last_read_positions`, una tabla que el propio
Calibre ya trae para eso: no hay base de datos aparte, y empiezas en el móvil
y sigues en el ordenador.

## Sincronizar la lectura con el Kobo y el Kindle

Enchufas el aparato y **gana el más avanzado**: si el Kobo va por delante, se
actualiza la aplicación; si vas más adelantado en el móvil, se actualiza el
Kobo. Nunca se retrocede. Antes de aplicar nada se enseña la lista de lo que va
a cambiar.

Cada aparato guarda esto a su manera y eso marca lo que se puede hacer:

| | Leer su posición | Escribirle la nuestra |
|---|---|---|
| **Kobo** | sí (`.kobo/KoboReader.sqlite`) | sí, con copia de seguridad previa de su base de datos |
| **Kindle antiguo** (`.mbp`) | sí | no |
| **Kindle moderno** (`.azw3r`) | no: formato cerrado sin descifrar | no |

Cuando no se puede leer la posición, se dice; no se aparenta haber
sincronizado. Para escribirle al Kindle no hay forma documentada de hacerlo sin
arriesgarse a dejarle un fichero corrupto, así que no se intenta.

El porcentaje del aparato se convierte en capítulo y página usando el recuento
de palabras que ya hace el lector, así que el salto cae donde tiene que caer.

**Un lector enchufado no es un lector montado.** El escritorio no siempre abre
el aparato solo (en KDE el automontaje viene apagado de fábrica), y mientras no
esté montado no hay ninguna carpeta que mirar: para el programa es como si no
estuviera. La pantalla «Mi lector» mira también los discos USB **sin montar**
con etiqueta de Kobo o de Kindle, lo dice, y trae un botón que los monta
(`udisksctl`, lo mismo que hace el escritorio al pinchar en su aviso).

## De dónde se pueden traer libros

Dos catálogos, los dos legales:

- **Project Gutenberg**: 75.000 libros de dominio público, con bastante en
  español. Se busca por su canal OPDS oficial.
- **Standard Ebooks**: los mismos clásicos reeditados a mano, con una
  maquetación muy cuidada. Publican en abierto su lista de novedades y es lo
  que se ofrece.

Y **Open Library** (Internet Archive) para completar fichas: portada, año,
editorial, ISBN y etiquetas de un libro que ya tengas. Eso son datos, no libros.

No hay más fuentes y no es un olvido: las bibliotecas que reparten libros con
derechos en vigor quedan fuera.

## El DRM

Vidalibre **no quita el DRM**. En España saltarse esa protección es ilegal
aunque el libro sea tuyo, así que ese módulo no existe aquí.

Lo que sí hace es **reconocerlo y decirlo**: al subir, al convertir y al abrir
un libro se mira si trae candado (EPUB con el contenido cifrado, MOBI/AZW3 de
Amazon, PDF protegido, un `.acsm` que no es un libro sino un vale de descarga)
y se avisa con una frase clara en vez del error incomprensible de Calibre a
mitad de faena. Importante: un `encryption.xml` dentro de un EPUB **no** es
siempre DRM —muchas editoriales lo usan solo para ofuscar las tipografías, que
es legítimo—, y eso se distingue antes de dar la voz de alarma.

## Las dos copias

El mismo código corre en dos sitios, con el mismo catálogo:

| | Dónde | Para qué | Kindle |
|---|---|---|---|
| **NAS** | contenedor en el NAS, puerto 8084 | la biblioteca de casa, siempre encendida | no (un servidor no ve los USB del portátil) |
| **Ordenador** | `vidalibre` o el icono del menú | lo mismo **y** el Kindle enchufado | sí |

**Quién escribe el catálogo.** Solo el NAS. La copia del ordenador ve la
biblioteca como una carpeta de red y por ahí SQLite no sabe echar sus cerrojos:
se probó y falló en caliente (el portátil quiso apuntar algo justo mientras el
NAS convertía un libro, y se encontró la base de datos bloqueada). Así que el
ordenador lee directamente de la carpeta —que es rápido y no molesta— y cuando
hay algo que apuntar se lo pide al NAS por la misma web que usa el navegador
(`puente.py`). Se configura con `VIDALIBRE_SERVIDOR`; si no se pone, la copia
del ordenador avisa de que no puede cambiar fichas.

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

Para ponerlo al día desde GitHub (es lo que hace el botón del actualizador del
NAS, en el 8091): `sh extras/actualizar.sh`, que vive fuera de la carpeta que
reemplaza y conserva el `.env` y la carpeta `datos/`.

**Memoria.** En reposo son unos 38 MB; una conversión sube a unos 175 MB (lo
que pide Calibre) y vuelve a bajar. El techo del contenedor está en 512 MB.
Para que eso se cumpla hay cuatro cosas puestas a propósito, y conviene no
quitarlas: `MALLOC_ARENA_MAX=2` (sin ella la biblioteca de C reserva por hilo y
la aplicación aparenta el doble), cuatro hilos de servidor y no más,
descodificar las portadas ya reducidas en vez de encogerlas después, y
devolverle al sistema la memoria al terminar cada conversión.

La imagen lleva Calibre dentro, así que **la primera construcción tarda** y
ocupa. `deploy.sh` guarda una copia del catálogo (`metadata.db`) antes de cada
despliegue, en `datos/copias/`, y conserva las diez últimas.

## Cómo está hecho

```
vidalibre/
  catalogo.py     el metadata.db de Calibre: leer y escribir
  ficheros.py     carpetas, nombres, portadas y metadata.opf
  motor.py        ebook-convert y ebook-meta + la cola de trabajos
  dispositivo.py  el Kindle y el Kobo por USB, montados o por montar
  sincronizacion.py  progreso compartido con el Kobo y el Kindle
  fuentes.py      Gutenberg, Standard Ebooks y Open Library
  drm.py          reconocer libros con candado
  lector.py       abrir un EPUB y servirlo capítulo a capítulo
  puente.py       los cambios que el portátil le pide al NAS
  servicio.py     las operaciones completas (subir, editar, convertir, enviar)
  web.py          las pantallas
  lector_web.py   las pantallas del lector
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

## El aspecto

Vidalibre usa el mismo aspecto que el resto de las aplicaciones de casa: base
neutra y oscura, **ámbar solo en los acentos**, claro u oscuro siguiendo al
sistema (con interruptor manual en la cabecera), tarjetas grandes y navegación
abajo en el móvil. Es una sola hoja de estilo escrita a mano
(`estatico/estilo.css`): la primera mitad es la base común y la segunda, lo
propio de los libros. Nada de frameworks.

Tres reglas que se notan y conviene no romper:

- Las portadas se piden siempre con **marca de versión** (`?v=…`, sacada de la
  fecha de modificación del libro). Sin eso el navegador se queda con la
  portada vieja durante horas y parece que cambiarla no hace nada.
- En las rejillas la portada se recorta a **2:3** para que las filas queden
  parejas; en la ficha y en el escaparate se enseña entera.
- Las animaciones van todas por debajo de 300 ms, solo sobre `transform` y
  `opacity`, y los `:hover` viven dentro de `@media (hover: hover)`: si no, en
  el móvil se quedan pegados al tocar.

## Si algo va mal

| Qué pasa | Qué mirar |
|---|---|
| «No encuentro la biblioteca» | que `BIBLIOTECA` apunte a la carpeta con `metadata.db` dentro |
| Las conversiones fallan todas | ¿hay Calibre? `/estado` dice la versión del motor |
| Un libro sale con el botón de descarga rayado | el catálogo lo apunta pero el fichero no está en disco |
| Va lento al listar | las miniaturas se cachean en `datos/miniaturas`; la primera vuelta es la lenta |
| Un libro tarda en abrirse la primera vez | se cuentan sus palabras para saber el porcentaje y los minutos; queda guardado en `datos/medidas` y la segunda vez es instantáneo |
| «Esto se hace en la biblioteca de casa» | estás en la copia del ordenador, que no escribe el catálogo: ese cambio se hace en el NAS |
| Enchufo el lector y no sale | tiene que estar la copia **del ordenador** (`vidalibre`, puerto 8484): el NAS no ve los cables USB. Si sale «hay un lector enchufado sin abrir», pulsa *Conectar el lector* |
| Cambio la portada y sigo viendo la vieja | no debería pasar ya: la dirección de la portada lleva marca de versión. Si pasa, recarga sin caché |

## Licencia y de dónde sale

GPL-3.0. Vidalibre reutiliza trabajo de dos programas libres:

- **[Calibre](https://calibre-ebook.com/)** (Kovid Goyal, GPL-3.0): el motor de
  conversión y de lectura de metadatos, y el formato de la biblioteca.
- **[Calibre-Web](https://github.com/janeczku/calibre-web)** (GPL-3.0): de ahí
  vienen los criterios de nombres de fichero y carpetas, el tratamiento del
  catálogo y la estructura del catálogo OPDS.
