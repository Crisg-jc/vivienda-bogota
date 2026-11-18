// Navegadores sin soporte de módulos (Internet Explorer y versiones anteriores a 2018).
// Escrito en JavaScript antiguo a propósito: debe funcionar justamente donde app.js no funciona.
(function () {
  var aviso = document.createElement("p");
  aviso.className = "sin-soporte";
  aviso.innerHTML = "Su navegador es demasiado antiguo para esta aplicaci&oacute;n. " +
    "Use una versi&oacute;n reciente de Chrome, Edge, Firefox, Safari, Opera o Samsung Internet.";
  document.body.insertBefore(aviso, document.body.firstChild);
})();
