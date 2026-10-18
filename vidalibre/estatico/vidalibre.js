// Vidalibre — los cuatro adornos que necesita la web. Sin librerías.
(function () {
  "use strict";

  // 1) Confirmar lo que no tiene vuelta atrás.
  document.querySelectorAll("form[data-confirmar]").forEach(function (formulario) {
    formulario.addEventListener("submit", function (evento) {
      if (!window.confirm(formulario.dataset.confirmar)) evento.preventDefault();
    });
  });

  // 2) Arrastrar libros a la zona de subida.
  var zona = document.getElementById("zona-soltar");
  if (zona) {
    var campo = zona.querySelector("input[type=file]");
    var cartel = document.getElementById("elegidos");
    ["dragenter", "dragover"].forEach(function (evento) {
      zona.addEventListener(evento, function (e) { e.preventDefault(); zona.classList.add("encima"); });
    });
    ["dragleave", "drop"].forEach(function (evento) {
      zona.addEventListener(evento, function (e) { e.preventDefault(); zona.classList.remove("encima"); });
    });
    zona.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { campo.files = e.dataTransfer.files; avisar(); }
    });
    campo.addEventListener("change", avisar);
    function avisar() {
      if (!cartel) return;
      var n = campo.files.length;
      cartel.textContent = n ? (n === 1 ? "1 libro elegido: " + campo.files[0].name
                                        : n + " libros elegidos") : cartel.dataset.original || "";
    }
  }

  // 3) Refrescar los trabajos en marcha sin recargar la página entera.
  var lista = document.getElementById("lista-trabajos");
  if (lista) {
    var destacado = lista.dataset.destacado;
    if (destacado) {
      var fila = lista.querySelector('[data-id="' + destacado + '"]');
      if (fila) fila.classList.add("destacado");
    }
    setInterval(function () {
      if (!document.querySelector(".estado-en-cola, .estado-en-marcha")) return;
      fetch("trabajos.json", { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.json(); })
        .then(function (datos) {
          var cambio = false;
          datos.trabajos.forEach(function (trabajo) {
            var fila = lista.querySelector('[data-id="' + trabajo.id + '"]');
            if (!fila) { cambio = true; return; }
            var clase = "trabajo estado-" + trabajo.estado.replace(/ /g, "-");
            if (fila.classList.contains("destacado")) clase += " destacado";
            if (fila.className !== clase) cambio = true;
            fila.className = clase;
            // La barra se estira con transform: con width el navegador tiene que
          // recalcular y repintar en cada fotograma y se nota el tirón.
          fila.querySelector(".progreso i").style.transform =
            "scaleX(" + (trabajo.porcentaje / 100) + ")";
            fila.querySelector(".detalle").textContent = trabajo.detalle;
            var estado = fila.querySelector(".estado");
            estado.textContent = trabajo.estado + (trabajo.pasos > 1 ? " · " + trabajo.paso + "/" + trabajo.pasos : "");
          });
          // Cuando termina algo, se recarga para ver los resultados con sus enlaces.
          if (cambio) location.reload();
        })
        .catch(function () {});
    }, 2500);
  }

  // 4) Claro / oscuro: auto (lo que diga el sistema) → claro → oscuro → auto.
  var interruptor = document.getElementById("cambia-tema");
  if (interruptor) {
    var orden = ["auto", "claro", "oscuro"];
    var nombres = { auto: "Tema: el del sistema", claro: "Tema: claro", oscuro: "Tema: oscuro" };
    var simbolos = { auto: "\u25D0", claro: "\u2600", oscuro: "\u263E" };
    pintar(document.documentElement.dataset.tema || "auto");
    interruptor.addEventListener("click", function () {
      var actual = document.documentElement.dataset.tema || "auto";
      var siguiente = orden[(orden.indexOf(actual) + 1) % orden.length];
      document.documentElement.dataset.tema = siguiente;
      try {
        if (siguiente === "auto") localStorage.removeItem("tema");
        else localStorage.setItem("tema", siguiente);
      } catch (e) {}
      pintar(siguiente);
    });
    function pintar(tema) {
      interruptor.textContent = simbolos[tema] || simbolos.auto;
      interruptor.title = nombres[tema] || nombres.auto;
    }
  }

  // 5) Marcar y desmarcar todos los libros de una lista de envío.
  document.querySelectorAll(".envio").forEach(function (formulario) {
    var casillas = formulario.querySelectorAll('input[name="ids"]');
    if (!casillas.length) return;
    var boton = document.createElement("button");
    boton.type = "button";
    boton.className = "suave pequeno";
    boton.textContent = "marcar todos";
    boton.addEventListener("click", function () {
      var marcar = boton.textContent === "marcar todos";
      casillas.forEach(function (c) { c.checked = marcar; });
      boton.textContent = marcar ? "desmarcar todos" : "marcar todos";
    });
    var lista = formulario.querySelector(".elegir");
    if (lista) lista.parentNode.insertBefore(boton, lista);
  });
})();
