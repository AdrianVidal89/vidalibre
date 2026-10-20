/* El lector de Vidalibre.
 *
 * La idea, copiada de los lectores buenos (epub.js, foliate) y simplificada:
 * el capítulo se pinta en un marco del mismo sitio, el texto se reparte en
 * columnas del ancho de la pantalla y «pasar página» es correr esa cinta de
 * columnas. No se recorta texto ni se calcula a mano dónde parte cada página:
 * eso lo hace el navegador, que para eso sabe.
 *
 * Lo que aporta frente a los de siempre: el libro no se descarga entero al
 * móvil. Llega un capítulo cada vez, ya preparado por el NAS.
 */
(function () {
  "use strict";

  var inicio = JSON.parse(document.body.dataset.inicio || "{}");
  var BASE = (inicio.base || location.pathname).replace(/\/$/, "") + "/";
  var marco = document.getElementById("marco");
  var cargando = document.getElementById("cargando");

  var AJUSTES_POR_DEFECTO = {
    tam: 20, inter: 1.62, margen: 26, fuente: "serif",
    tema: "claro", alineacion: "justify", modo: "paginas"
  };
  var TEMAS = {
    claro: { papel: "#ffffff", tinta: "#1b1b1b", apagado: "#767169", enlace: "#9a5b21" },
    sepia: { papel: "#faf0dc", tinta: "#4a3d2c", apagado: "#8a7a62", enlace: "#8a5118" },
    noche: { papel: "#1e2024", tinta: "#cdc9c2", apagado: "#8b8880", enlace: "#e0a45c" },
    negro: { papel: "#000000", tinta: "#b4b1ab", apagado: "#726f6a", enlace: "#d9a05f" }
  };
  var FUENTES = {
    serif: 'Georgia, "Iowan Old Style", "Times New Roman", serif',
    sans: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    libro: "inherit"
  };

  var ajustes = cargarAjustes();
  var estado = {
    capitulo: 0, pagina: 0, paginas: 1, paso: 0,
    documento: null, cargando: false, ancla: ""
  };

  // ------------------------------------------------------------- ajustes
  function cargarAjustes() {
    var guardado = {};
    try { guardado = JSON.parse(localStorage.getItem("vidalibre:lector") || "{}"); } catch (e) {}
    var fin = {};
    for (var clave in AJUSTES_POR_DEFECTO) {
      fin[clave] = guardado[clave] !== undefined ? guardado[clave] : AJUSTES_POR_DEFECTO[clave];
    }
    return fin;
  }

  function guardarAjustes() {
    try { localStorage.setItem("vidalibre:lector", JSON.stringify(ajustes)); } catch (e) {}
  }

  function aplicarAjustes() {
    var tema = TEMAS[ajustes.tema] || TEMAS.claro;
    document.body.dataset.tema = ajustes.tema;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", tema.papel);

    var doc = estado.documento;
    if (doc) {
      var raiz = doc.documentElement.style;
      raiz.setProperty("--tam", ajustes.tam + "px");
      raiz.setProperty("--inter", ajustes.inter);
      raiz.setProperty("--margen-h", ajustes.margen + "px");
      raiz.setProperty("--hueco", (ajustes.margen * 2) + "px");
      raiz.setProperty("--alineacion", ajustes.alineacion);
      raiz.setProperty("--papel", tema.papel);
      raiz.setProperty("--tinta", tema.tinta);
      raiz.setProperty("--apagado", tema.apagado);
      raiz.setProperty("--enlace", tema.enlace);
      if (ajustes.fuente === "libro") raiz.removeProperty("--fuente");
      else raiz.setProperty("--fuente", FUENTES[ajustes.fuente]);
      doc.body.classList.toggle("desplazar", ajustes.modo === "desplazar");
    }

    document.querySelectorAll("[data-fijar]").forEach(function (boton) {
      var clave = boton.dataset.fijar;
      var valor = boton.dataset.valor;
      boton.classList.toggle("elegido", String(ajustes[clave]) === valor);
    });
    var salida = document.getElementById("valor-tam");
    if (salida) salida.textContent = ajustes.tam;
  }

  // ------------------------------------------------------------ capítulos
  function cargarCapitulo(numero, destino) {
    if (numero < 0 || numero >= inicio.capitulos || estado.cargando) return;
    estado.cargando = true;
    estado.destino = destino || {};
    cargando.hidden = false;
    marco.src = BASE + "pagina/" + numero;
    estado.capitulo = numero;
  }

  marco.addEventListener("load", function () {
    var doc = marco.contentDocument;
    if (!doc || !doc.getElementById("pagina")) return;
    estado.documento = doc;
    estado.cargando = false;
    aplicarAjustes();
    engancharDocumento(doc);
    // Dos vueltas: la segunda cuando ya han cargado las imágenes, que es
    // cuando el número de páginas es el de verdad.
    medir();
    colocar(estado.destino || {});
    // Si se ha llegado pasando de página, cuenta como lectura; si es un
    // salto (índice, búsqueda), apuntarLectura lo descarta por grande.
    apuntarLectura();
    marco.contentWindow.addEventListener("load", function () { medir(true); });
    setTimeout(function () { medir(true); }, 350);
    cargando.hidden = true;
    prefetch(estado.capitulo + 1);
  });

  function prefetch(numero) {
    if (numero < 0 || numero >= inicio.capitulos) return;
    fetch(BASE + "pagina/" + numero, { credentials: "same-origin" }).catch(function () {});
  }

  function medir(conservar) {
    var doc = estado.documento;
    if (!doc) return;
    var pagina = doc.getElementById("pagina");
    var fin = doc.getElementById("fin");
    if (ajustes.modo === "desplazar") {
      estado.paginas = 1;
      pagina.style.width = "";
      pagina.style.columnWidth = "";
      pintarEstado();
      return;
    }
    var anchoVisible = doc.documentElement.clientWidth;
    var altoVisible = doc.documentElement.clientHeight;
    var fraccion = conservar && estado.paginas > 1 ? estado.pagina / estado.paginas : null;

    pagina.style.width = anchoVisible + "px";
    pagina.style.columnWidth = (anchoVisible - ajustes.margen * 2) + "px";
    // Cuánto hay que correr la cinta para cambiar de página: el ancho de una
    // columna MÁS el hueco entre columnas, que juntos suman exactamente el
    // ancho de la pantalla. Sumarle el hueco otra vez (el error que tenía
    // esto) desplaza de más, y a partir de la segunda página el texto aparece
    // partido entre dos columnas.
    estado.paso = anchoVisible;

    // La altura de la página se ajusta a un número entero de líneas. Sin esto,
    // la última línea de cada página aparece cortada por la mitad: es el
    // defecto que delata a los lectores hechos con prisa.
    var estiloCuerpo = marco.contentWindow.getComputedStyle(doc.body);
    var altoLinea = parseFloat(estiloCuerpo.lineHeight) || (ajustes.tam * ajustes.inter);
    var arriba = 30;                       // aire de arriba (y sitio bajo la barra)
    var abajo = Math.max(22 + 26, altoPie() + 10);   // aire + sitio para el pie
    var disponible = altoVisible - arriba - abajo;
    var lineas = Math.max(3, Math.floor(disponible / altoLinea));
    var altoTexto = lineas * altoLinea;
    pagina.style.paddingTop = arriba + "px";
    pagina.style.paddingBottom = abajo + "px";
    pagina.style.height = (altoTexto + arriba + abajo) + "px";
    doc.documentElement.style.setProperty("--alto-pagina", altoTexto + "px");

    // Cuántas páginas tiene el capítulo: se mide con una marca puesta al
    // final del texto, descontando el margen izquierdo. Si por lo que sea no
    // está, se recurre al ancho desbordado.
    var paginas;
    if (fin) {
      paginas = Math.round((fin.offsetLeft - ajustes.margen) / estado.paso) + 1;
    } else {
      paginas = Math.round(pagina.scrollWidth / estado.paso);
    }
    estado.paginas = Math.max(1, paginas);
    if (fraccion !== null) estado.pagina = Math.min(estado.paginas - 1, Math.round(fraccion * estado.paginas));
    irA(Math.min(estado.pagina, estado.paginas - 1), false);
  }

  function colocar(destino) {
    var doc = estado.documento;
    if (!doc) return;
    if (destino.ancla) {
      var objetivo = doc.getElementById(destino.ancla) ||
                     doc.querySelector('[name="' + destino.ancla + '"]');
      if (objetivo) {
        if (ajustes.modo === "desplazar") {
          objetivo.scrollIntoView();
        } else {
          irA(Math.floor(objetivo.getBoundingClientRect().left / estado.paso) + estado.pagina, false);
        }
        return;
      }
    }
    if (typeof destino.pagina === "number") {
      irA(destino.pagina, false);
    } else if (typeof destino.fraccion === "number") {
      if (ajustes.modo === "desplazar") {
        var rollo = desplazador();
        rollo.scrollTop = destino.fraccion * Math.max(0, rollo.scrollHeight - rollo.clientHeight);
        pintarEstado();
        return;
      }
      irA(Math.round(destino.fraccion * (estado.paginas - 1)), false);
    } else if (destino.ultima) {
      irA(estado.paginas - 1, false);
    } else {
      irA(0, false);
    }
  }

  function irA(numero, animar) {
    var doc = estado.documento;
    if (!doc) return;
    var pagina = doc.getElementById("pagina");
    estado.pagina = Math.max(0, Math.min(numero, estado.paginas - 1));
    if (ajustes.modo !== "desplazar") {
      pagina.classList.toggle("animado", animar !== false);
      pagina.style.transform = "translateX(" + (-estado.pagina * estado.paso) + "px)";
      // Por si el navegador ha desplazado el documento a lo alto por su
      // cuenta (le pasa al saltar a un ancla): en modo páginas no se desplaza
      // nunca, se mueve la cinta de columnas.
      if (doc.documentElement.scrollTop || doc.body.scrollTop) {
        doc.documentElement.scrollTop = 0;
        doc.body.scrollTop = 0;
      }
    }
    pintarEstado();
    if (animar !== false) apuntarLectura();
    guardarPosicion();
  }

  function siguiente() {
    if (ajustes.modo === "desplazar") {
      var ventana = marco.contentWindow;
      if (ventana.scrollY + ventana.innerHeight * 1.5 >= estado.documento.body.scrollHeight) {
        return cargarCapitulo(estado.capitulo + 1, {});
      }
      ventana.scrollBy({ top: ventana.innerHeight * 0.9, behavior: "smooth" });
      return;
    }
    if (estado.pagina + 1 < estado.paginas) return irA(estado.pagina + 1, true);
    cargarCapitulo(estado.capitulo + 1, {});
  }

  function anterior() {
    if (ajustes.modo === "desplazar") {
      var ventana = marco.contentWindow;
      if (ventana.scrollY <= 4) return cargarCapitulo(estado.capitulo - 1, { ultima: true });
      ventana.scrollBy({ top: -ventana.innerHeight * 0.9, behavior: "smooth" });
      return;
    }
    if (estado.pagina > 0) return irA(estado.pagina - 1, true);
    cargarCapitulo(estado.capitulo - 1, { ultima: true });
  }

  // ------------------------------------------------------------- progreso
  // En modo desplazar quien se desplaza es el <body> del marco (o el
  // documento, según el navegador): se mira cuál de los dos tiene recorrido.
  function desplazador() {
    var doc = estado.documento;
    var cuerpo = doc.body;
    return cuerpo.scrollHeight > cuerpo.clientHeight + 1 ? cuerpo : (doc.scrollingElement || doc.documentElement);
  }

  // Cuánto del capítulo actual queda atrás, de 0 a 1.
  function fraccionCapitulo() {
    if (!estado.documento) return 0;
    if (ajustes.modo === "desplazar") {
      var rollo = desplazador();
      var recorrido = rollo.scrollHeight - rollo.clientHeight;
      return recorrido > 0 ? Math.max(0, Math.min(1, rollo.scrollTop / recorrido)) : 0;
    }
    return estado.paginas > 1 ? estado.pagina / (estado.paginas - 1) : 0;
  }

  function fraccionLibro() {
    var fracciones = inicio.fracciones || [];
    var palabras = inicio.palabras_capitulo || [];
    var desde = fracciones[estado.capitulo] || 0;
    var trozo = (palabras[estado.capitulo] || 0) / (inicio.palabras || 1);
    return Math.max(0, Math.min(1, desde + trozo * fraccionCapitulo()));
  }

  // Palabras que quedan, del capítulo y del libro entero.
  function palabrasQuedan() {
    var palabras = inicio.palabras_capitulo || [];
    var capitulo = (palabras[estado.capitulo] || 0) * (1 - fraccionCapitulo());
    var libro = capitulo;
    for (var i = estado.capitulo + 1; i < palabras.length; i++) libro += palabras[i] || 0;
    return { capitulo: capitulo, libro: libro };
  }

  // ----------------------------------------------------- velocidad de lectura
  // Se aprende de cómo lees tú: cada vez que avanzas, se apuntan las palabras
  // que has dejado atrás y el tiempo que te ha llevado. Los saltos (índice,
  // búsqueda), las vueltas atrás y los ratos con el móvil olvidado no cuentan.
  // Las lecturas viejas pesan cada vez menos, y hasta que hay datos manda
  // una velocidad media de 230 palabras por minuto.
  var PPM_DE_PARTIDA = 230;
  var MINUTOS_DE_PARTIDA = 3;     // cuánto «pesa» esa media frente a lo medido
  var velocidad = cargarVelocidad();
  var ultimaMarca = null;

  function cargarVelocidad() {
    try {
      var v = JSON.parse(localStorage.getItem("vidalibre:velocidad") || "null");
      if (v && v.palabras >= 0 && v.minutos >= 0) return v;
    } catch (e) {}
    return { palabras: 0, minutos: 0 };
  }

  function palabrasPorMinuto() {
    return (velocidad.palabras + PPM_DE_PARTIDA * MINUTOS_DE_PARTIDA) /
           (velocidad.minutos + MINUTOS_DE_PARTIDA);
  }

  function apuntarLectura() {
    var ahora = Date.now();
    var donde = fraccionLibro() * (inicio.palabras || 0);
    var antes = ultimaMarca;
    var nueva = { palabras: donde, t: ahora };
    if (!antes || document.visibilityState === "hidden") { ultimaMarca = nueva; return; }
    var leidas = donde - antes.palabras;
    var minutos = (ahora - antes.t) / 60000;
    // Muy seguido (desplazando, o pasando páginas a toda prisa): se deja
    // la marca de antes y se mide todo junto en el siguiente movimiento.
    if (leidas >= 0 && minutos < 2 / 60) return;
    ultimaMarca = nueva;
    if (leidas <= 0 || leidas > 1500) return;           // atrás o un salto
    if (minutos > 8) return;                            // se fue a otra cosa
    var ppm = leidas / minutos;
    if (ppm < 40 || ppm > 1200) return;                 // ni leyendo ni hojeando
    velocidad.palabras = velocidad.palabras * 0.98 + leidas;
    velocidad.minutos = velocidad.minutos * 0.98 + minutos;
    try { localStorage.setItem("vidalibre:velocidad", JSON.stringify(velocidad)); } catch (e) {}
  }

  function textoTiempo(palabras) {
    var minutos = Math.round(palabras / palabrasPorMinuto());
    if (minutos < 1) return "menos de 1 min";
    if (minutos < 60) return minutos + " min";
    var horas = Math.floor(minutos / 60);
    var resto = minutos % 60;
    return horas + " h" + (resto ? " " + (resto < 10 ? "0" : "") + resto + " min" : "");
  }

  // ------------------------------------------------------------------- pie
  // La línea fija de abajo, como en el Kindle. Al tocarla alterna entre lo
  // que queda del capítulo y lo que queda del libro.
  var pie = document.getElementById("pie");
  var vistaPie = "capitulo";
  try { vistaPie = localStorage.getItem("vidalibre:pie") === "libro" ? "libro" : "capitulo"; } catch (e) {}

  function altoPie() {
    if (!pie) return 0;
    return Math.ceil(window.innerHeight - pie.getBoundingClientRect().top);
  }

  // Páginas que quedan: en modo páginas se cuentan; en modo desplazar son
  // pantallas, y en el libro entero se estiman con las palabras por página
  // del capítulo que se está leyendo.
  function paginasQuedan(quedan) {
    var capitulo;
    var palabrasPagina;
    var palabras = (inicio.palabras_capitulo || [])[estado.capitulo] || 0;
    if (ajustes.modo === "desplazar") {
      var rollo = desplazador();
      var pantalla = rollo.clientHeight || 1;
      capitulo = Math.ceil(Math.max(0, rollo.scrollHeight - rollo.scrollTop - pantalla) / pantalla);
      palabrasPagina = palabras / Math.max(1, Math.ceil(rollo.scrollHeight / pantalla));
    } else {
      capitulo = estado.paginas - 1 - estado.pagina;
      palabrasPagina = palabras / Math.max(1, estado.paginas);
    }
    var libro = palabrasPagina > 20 ? Math.round(capitulo + (quedan.libro - quedan.capitulo) / palabrasPagina) : null;
    return { capitulo: capitulo, libro: libro };
  }

  function pintarPie() {
    if (!pie || !estado.documento) return;
    var quedan = palabrasQuedan();
    var paginas = paginasQuedan(quedan);
    var texto;
    if (vistaPie === "libro") {
      var ultimo = estado.capitulo >= inicio.capitulos - 1 && paginas.capitulo === 0;
      texto = ultimo
        ? "Última página del libro"
        : Math.round(fraccionLibro() * 100) + "% · quedan " + textoTiempo(quedan.libro) +
          (paginas.libro !== null ? " · ~" + paginas.libro + " págs." : "") + " del libro";
    } else if (paginas.capitulo === 0) {
      texto = "Última página del capítulo";
    } else {
      texto = (paginas.capitulo === 1 ? "Queda 1 pág." : "Quedan " + paginas.capitulo + " págs.") +
              " del capítulo · " + textoTiempo(quedan.capitulo);
    }
    pie.textContent = texto;
  }

  if (pie) pie.addEventListener("click", function () {
    vistaPie = vistaPie === "libro" ? "capitulo" : "libro";
    try { localStorage.setItem("vidalibre:pie", vistaPie); } catch (e) {}
    pintarPie();
  });

  function pintarEstado() {
    var fraccion = fraccionLibro();
    document.getElementById("progreso").style.width = (fraccion * 100).toFixed(1) + "%";
    var quedan = palabrasQuedan();
    document.getElementById("estado-izq").textContent =
      ajustes.modo === "desplazar"
        ? "Capítulo " + (estado.capitulo + 1) + " de " + inicio.capitulos
        : "Página " + (estado.pagina + 1) + " de " + estado.paginas;
    document.getElementById("estado-der").textContent =
      Math.round(fraccion * 100) + "% · " +
      (quedan.capitulo / palabrasPorMinuto() >= 1 ? "quedan " + textoTiempo(quedan.capitulo) + " del capítulo" : "acabas el capítulo");
    var titulo = document.getElementById("titulo-capitulo");
    var entradas = inicio.indice || [];
    var nombre = "";
    for (var i = 0; i < entradas.length; i++) {
      if (entradas[i].capitulo <= estado.capitulo) nombre = entradas[i].titulo; else break;
    }
    titulo.textContent = nombre;
    pintarPie();
  }

  var guardadoPendiente = null;
  function guardarPosicion(ahora) {
    if (guardadoPendiente) clearTimeout(guardadoPendiente);
    var enviar = function () {
      var cuerpo = JSON.stringify({
        posicion: "c" + estado.capitulo + ":" + fraccionCapitulo().toFixed(4),
        fraccion: fraccionLibro()
      });
      if (ahora && navigator.sendBeacon) {
        navigator.sendBeacon(BASE + "posicion", new Blob([cuerpo], { type: "application/json" }));
        return;
      }
      fetch(BASE + "posicion", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: cuerpo
      }).catch(function () {});
    };
    if (ahora) return enviar();
    guardadoPendiente = setTimeout(enviar, 1500);
  }

  // --------------------------------------------------------------- gestos
  function engancharDocumento(doc) {
    var inicioToque = null;

    doc.addEventListener("click", function (evento) {
      var enlace = evento.target.closest ? evento.target.closest("a") : null;
      if (enlace) {
        if (enlace.dataset.capitulo !== undefined) {
          evento.preventDefault();
          cargarCapitulo(parseInt(enlace.dataset.capitulo, 10), { ancla: enlace.dataset.ancla });
          return;
        }
        if ((enlace.getAttribute("href") || "").charAt(0) === "#") {
          evento.preventDefault();
          colocar({ ancla: enlace.getAttribute("href").slice(1) });
          return;
        }
        return; // enlace de fuera: que lo abra el navegador
      }
      if (String(doc.getSelection())) return;   // está seleccionando texto
      var ancho = doc.documentElement.clientWidth;
      var x = evento.clientX;
      if (x < ancho * 0.28) anterior();
      else if (x > ancho * 0.72) siguiente();
      else alternarBarras();
    });

    doc.addEventListener("touchstart", function (evento) {
      if (evento.touches.length !== 1) return;
      inicioToque = { x: evento.touches[0].clientX, y: evento.touches[0].clientY, t: Date.now() };
    }, { passive: true });

    doc.addEventListener("touchend", function (evento) {
      if (!inicioToque) return;
      var toque = evento.changedTouches[0];
      var dx = toque.clientX - inicioToque.x;
      var dy = toque.clientY - inicioToque.y;
      inicioToque = null;
      if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.6 && ajustes.modo !== "desplazar") {
        evento.preventDefault();
        if (dx < 0) siguiente(); else anterior();
      }
    });

    doc.addEventListener("keydown", teclado);
    if (ajustes.modo === "desplazar") {
      var pintando = null;
      doc.addEventListener("scroll", function () {
        guardarPosicion();
        if (pintando) return;
        pintando = setTimeout(function () { pintando = null; pintarEstado(); apuntarLectura(); }, 400);
      }, { passive: true, capture: true });
    }
  }

  function teclado(evento) {
    if (evento.target && /input|textarea/i.test(evento.target.tagName)) return;
    switch (evento.key) {
      case "ArrowRight": case "PageDown": case " ": siguiente(); evento.preventDefault(); break;
      case "ArrowLeft": case "PageUp": anterior(); evento.preventDefault(); break;
      case "Escape": cerrarPaneles(); break;
      case "f": case "F": abrirPanel("buscar"); evento.preventDefault(); break;
      case "t": case "T": abrirPanel("indice"); evento.preventDefault(); break;
    }
  }
  document.addEventListener("keydown", teclado);

  // --------------------------------------------------------------- paneles
  var velo = document.getElementById("velo");

  function abrirPanel(nombre) {
    cerrarPaneles();
    var panel = document.getElementById("panel-" + nombre);
    if (!panel) return;
    panel.hidden = false;
    velo.hidden = false;
    document.body.classList.add("con-panel");
    mostrarBarras(true);
    if (nombre === "buscar") setTimeout(function () {
      document.getElementById("texto-buscar").focus();
    }, 120);
  }

  function cerrarPaneles() {
    document.querySelectorAll(".panel").forEach(function (p) { p.hidden = true; });
    velo.hidden = true;
    document.body.classList.remove("con-panel");
  }

  velo.addEventListener("click", cerrarPaneles);
  document.querySelectorAll("[data-panel]").forEach(function (boton) {
    boton.addEventListener("click", function () { abrirPanel(boton.dataset.panel); });
  });
  document.querySelectorAll("[data-cerrar]").forEach(function (boton) {
    boton.addEventListener("click", cerrarPaneles);
  });
  document.querySelectorAll("[data-ir]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      cerrarPaneles();
      cargarCapitulo(parseInt(boton.dataset.ir, 10), { ancla: boton.dataset.ancla });
    });
  });

  document.querySelectorAll("[data-ajuste]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var paso = parseInt(boton.dataset.paso, 10);
      ajustes.tam = Math.max(13, Math.min(34, ajustes.tam + paso));
      guardarAjustes(); aplicarAjustes(); medir(true);
    });
  });

  document.querySelectorAll("[data-fijar]").forEach(function (boton) {
    boton.addEventListener("click", function () {
      var valor = boton.dataset.valor;
      ajustes[boton.dataset.fijar] = isNaN(parseFloat(valor)) ? valor : parseFloat(valor);
      guardarAjustes(); aplicarAjustes();
      if (boton.dataset.fijar === "modo") cargarCapitulo(estado.capitulo, { fraccion: estado.paginas > 1 ? estado.pagina / (estado.paginas - 1) : 0 });
      else medir(true);
    });
  });

  // --------------------------------------------------------------- buscar
  document.getElementById("forma-buscar").addEventListener("submit", function (evento) {
    evento.preventDefault();
    var texto = document.getElementById("texto-buscar").value.trim();
    var lista = document.getElementById("resultados-busqueda");
    if (texto.length < 3) { lista.innerHTML = "<li class='vacio'>Escribe al menos tres letras.</li>"; return; }
    lista.innerHTML = "<li class='vacio'>Buscando…</li>";
    fetch(BASE + "buscar?q=" + encodeURIComponent(texto), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (datos) {
        if (!datos.resultados.length) { lista.innerHTML = "<li class='vacio'>No aparece en el libro.</li>"; return; }
        lista.innerHTML = "";
        datos.resultados.forEach(function (resultado) {
          var li = document.createElement("li");
          var boton = document.createElement("button");
          boton.innerHTML = "<b>" + (resultado.titulo || ("Capítulo " + (resultado.capitulo + 1))) +
                            "</b><span></span>";
          boton.querySelector("span").textContent = resultado.fragmento;
          boton.addEventListener("click", function () {
            cerrarPaneles();
            cargarCapitulo(resultado.capitulo, { fraccion: resultado.posicion });
          });
          li.appendChild(boton);
          lista.appendChild(li);
        });
      })
      .catch(function () { lista.innerHTML = "<li class='vacio'>No he podido buscar.</li>"; });
  });

  // --------------------------------------------------------------- barras
  var ocultador = null;
  function mostrarBarras(fijas) {
    document.body.classList.remove("sin-barras");
    if (ocultador) clearTimeout(ocultador);
    if (!fijas) ocultador = setTimeout(function () {
      if (!document.body.classList.contains("con-panel")) document.body.classList.add("sin-barras");
    }, 2800);
  }
  function alternarBarras() {
    if (document.body.classList.contains("sin-barras")) mostrarBarras(true);
    else document.body.classList.add("sin-barras");
  }

  window.addEventListener("resize", function () {
    clearTimeout(window._remedir);
    window._remedir = setTimeout(function () { medir(true); }, 250);
  });
  window.addEventListener("pagehide", function () { guardarPosicion(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") guardarPosicion(true);
    else ultimaMarca = null;   // el rato fuera no cuenta como lectura
  });

  // --------------------------------------------------------------- arranque
  aplicarAjustes();
  if (typeof inicio.capitulo_inicial === "number") {
    var pedida = parseInt(new URLSearchParams(location.search).get("pag") || "", 10);
    cargarCapitulo(inicio.capitulo_inicial, isNaN(pedida) ? {} : { pagina: pedida });
    mostrarBarras();
    return;
  }
  var guardada = (inicio.posicion || "").match(/^c(\d+):([\d.]+)$/);
  if (guardada) {
    cargarCapitulo(parseInt(guardada[1], 10), { fraccion: parseFloat(guardada[2]) });
  } else {
    estado.destino = {};
  }
  mostrarBarras();
})();
