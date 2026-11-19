// Botón de modo claro/oscuro. Sin elección, la página sigue al sistema; la elección se recuerda en
// este navegador. Los mapas y gráficos leen sus colores al dibujarse, por eso se recarga la página
// (conserva lo escrito: el formulario vive en la dirección).
import { $, REG, temaOscuro } from "./base.js";

export function iniciarTema() {
  const boton = $("#cambiar-tema");
  if (!boton) return;
  const pintar = () => {
    const oscuro = temaOscuro();
    boton.setAttribute("aria-pressed", String(oscuro));
    $("#tema-texto").textContent = oscuro ? "Modo claro" : "Modo oscuro";
    boton.title = oscuro ? "Cambiar a modo claro" : "Cambiar a modo oscuro";
  };
  pintar();
  boton.addEventListener("click", () => {
    const nuevo = temaOscuro() ? "claro" : "oscuro";
    try { localStorage.setItem("vivienda-tema", nuevo); } catch (e) { /* sin almacenamiento: solo esta vez */ }
    REG.info(`Tema elegido: ${nuevo}`);
    document.documentElement.setAttribute("data-tema", nuevo);
    document.documentElement.setAttribute("data-tema-efectivo", nuevo);
    location.reload();
  });
}
