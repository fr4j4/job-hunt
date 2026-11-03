// Fuente única de los colores de los gráficos (validada en CI por scripts/validar_paleta.mjs).
// Categórica de 8 slots en orden fijo: el orden ES el mecanismo de seguridad para daltonismo.
export const SUPERFICIE = { light: '#ffffff', dark: '#171e27' };

export const PALETA = {
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
};

// Rampa ordinal (seniority, encaje, inglés, estado): 4 pasos de un solo tono azul.
export const ORDINAL = {
  light: ['#86b6ef', '#3987e5', '#256abf', '#104281'],
  dark: ['#184f95', '#256abf', '#3987e5', '#6da7ec'],   // bajo→alto: oscuro (cerca de la superficie) → claro
};

// Secuencial: azul 100→700 (el más claro "se funde" con la superficie = cercano a cero).
export const SECUENCIAL = {
  light: ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'],
  dark: ['#0d366b', '#104281', '#184f95', '#1c5cab', '#2a78d6', '#5598e7', '#9ec5f4'],
};

// Divergente: azul ↔ rojo con gris neutro al centro.
export const DIVERGENTE = {
  light: ['#256abf', '#6da7ec', '#cde2fb', '#f0efec', '#f5c4c3', '#e66767', '#b52e2e'],
  dark: ['#6da7ec', '#2a78d6', '#184f95', '#383835', '#8f2f2e', '#d04545', '#f08c8b'],
};

// Estado: reservado, siempre con ícono + texto.
export const ESTADO = { bueno: '#0ca30c', aviso: '#fab219', serio: '#ec835a', critico: '#d03b3b' };

export const NEUTRO = { light: '#898781', dark: '#898781' };
