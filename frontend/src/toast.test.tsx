import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "./toast";

function Disparador() {
  const toast = useToast();
  return (
    <>
      <button onClick={() => toast.ok("Servicio habilitado.")}>éxito</button>
      <button
        onClick={() =>
          toast.error("El SII rechazó la semilla firmada (estado=10).", [
            "El certificado tiene que estar emitido por una entidad acreditada.",
            "El RUT necesita «Enviar Doctos» en ese ambiente.",
          ])
        }
      >
        fallo
      </button>
    </>
  );
}

function montar() {
  return render(
    <ToastProvider>
      <Disparador />
    </ToastProvider>,
  );
}

describe("Avisos globales", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => vi.useRealTimers());

  it("el aviso de éxito se va solo", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    montar();

    await user.click(screen.getByText("éxito"));
    expect(screen.getByText("Servicio habilitado.")).toBeInTheDocument();

    act(() => void vi.advanceTimersByTime(6000));
    expect(screen.queryByText("Servicio habilitado.")).not.toBeInTheDocument();
  });

  it("el aviso de error NO se va solo", async () => {
    // Un error que desaparece reproduce el problema que este componente viene
    // a resolver: el operador mira otra cosa y se queda sin saber qué falló.
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    montar();

    await user.click(screen.getByText("fallo"));
    act(() => void vi.advanceTimersByTime(60000));

    expect(screen.getByText(/rechazó la semilla firmada/)).toBeInTheDocument();
  });

  it("el error muestra la guía del API y se puede cerrar", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    montar();

    await user.click(screen.getByText("fallo"));
    expect(screen.getByText(/entidad acreditada/)).toBeInTheDocument();
    expect(screen.getByText(/Enviar Doctos/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cerrar aviso" }));
    expect(screen.queryByText(/rechazó la semilla firmada/)).not.toBeInTheDocument();
  });

  it("un error se anuncia como alerta y un éxito no", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    montar();

    await user.click(screen.getByText("fallo"));
    expect(screen.getByRole("alert")).toHaveTextContent(/semilla firmada/);

    await user.click(screen.getByText("éxito"));
    expect(screen.getByRole("status")).toHaveTextContent("Servicio habilitado.");
  });
});
