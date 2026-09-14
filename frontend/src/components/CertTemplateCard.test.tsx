import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ToastProvider } from "../toast";
import CertTemplateCard from "./CertTemplateCard";

const PLANTILLA = [
  {
    kind: "basico",
    label: "Set básico",
    endpoint: "issue-batch",
    help: "Cuatro facturas afectas…",
    transcribe: true,
  },
  {
    kind: "libro_ventas",
    label: "Libro de ventas",
    endpoint: "books",
    help: "No se transcribe…",
    transcribe: false,
  },
];

function montar(hasSets = false, onCreated = vi.fn()) {
  render(
    <ToastProvider>
      <CertTemplateCard cid={9} writable hasSets={hasSets} onCreated={onCreated} />
    </ToastProvider>,
  );
  return onCreated;
}

describe("Crear los sets desde la plantilla", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "certTemplate").mockResolvedValue(PLANTILLA);
  });

  it("no se puede crear sin ningún número de atención", async () => {
    const user = userEvent.setup();
    const crear = vi.spyOn(api, "certCreateFromTemplate");
    montar();

    await user.click(screen.getByRole("button", { name: /Crear los sets/ }));
    await screen.findByRole("dialog");

    // El botón del pie está deshabilitado mientras no haya números: sin número
    // de atención el set no se puede identificar ni enviar.
    const botones = screen.getAllByRole("button", { name: /Crear los sets/ });
    expect(botones[botones.length - 1]).toBeDisabled();
    expect(crear).not.toHaveBeenCalled();
  });

  it("envía sólo los sets con número y avisa al expediente", async () => {
    const user = userEvent.setup();
    const crear = vi.spyOn(api, "certCreateFromTemplate").mockResolvedValue({ sets: [] } as never);
    const onCreated = montar();

    await user.click(screen.getByRole("button", { name: /Crear los sets/ }));
    await screen.findByRole("dialog");

    await user.type(screen.getByLabelText("Set básico"), "5038170");
    // El libro de ventas se deja en blanco a propósito.

    const botones = screen.getAllByRole("button", { name: /Crear los sets/ });
    await user.click(botones[botones.length - 1]);

    await waitFor(() => expect(crear).toHaveBeenCalledWith(9, { basico: "5038170" }));
    expect(onCreated).toHaveBeenCalled();
  });

  it("avisa que se pierde lo ajustado si el expediente ya tiene sets", async () => {
    const user = userEvent.setup();
    montar(true);

    await user.click(screen.getByRole("button", { name: /Crear los sets/ }));
    await screen.findByRole("dialog");

    expect(screen.getByText(/se pierde lo que hayas ajustado/)).toBeInTheDocument();
  });

  it("un fallo del API deja el diálogo abierto con el error a la vista", async () => {
    // Cerrar al fallar es lo que escondía el motivo: el operador ve el modal
    // desaparecer y no sabe si se creó algo.
    const user = userEvent.setup();
    vi.spyOn(api, "certCreateFromTemplate").mockRejectedValue(
      Object.assign(new Error("Indica al menos un número de atención."), { hints: [] }),
    );
    montar();

    await user.click(screen.getByRole("button", { name: /Crear los sets/ }));
    await screen.findByRole("dialog");
    await user.type(screen.getByLabelText("Set básico"), "5038170");

    const botones = screen.getAllByRole("button", { name: /Crear los sets/ });
    await user.click(botones[botones.length - 1]);

    const dialogo = await screen.findByRole("dialog");
    expect(await within(dialogo).findByText(/Indica al menos un número/)).toBeInTheDocument();
  });

  it("no se muestra a quien sólo puede leer", () => {
    render(
      <ToastProvider>
        <CertTemplateCard cid={9} writable={false} hasSets={false} onCreated={vi.fn()} />
      </ToastProvider>,
    );
    expect(screen.queryByText("Empezar desde la plantilla del SII")).not.toBeInTheDocument();
  });
});
