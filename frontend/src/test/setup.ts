import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Con globals:false, RTL no registra el cleanup automático: lo hacemos aquí.
afterEach(() => cleanup());

// jsdom no implementa File.text(), que los navegadores soportan desde 2020 y
// que usa la carga de definiciones. Sin esto, el componente falla sólo en los
// tests, que es la peor forma de fallar: la del entorno, no la del código.
if (typeof File !== "undefined" && !File.prototype.text) {
  File.prototype.text = function (this: File) {
    return new Promise<string>((resolve, reject) => {
      const lector = new FileReader();
      lector.onload = () => resolve(String(lector.result));
      lector.onerror = () => reject(lector.error);
      lector.readAsText(this);
    });
  };
}
