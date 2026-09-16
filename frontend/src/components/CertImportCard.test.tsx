import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Mock } from "vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { ToastProvider } from "../toast";
import CertImportCard from "./CertImportCard";

vi.mock("../api", () => ({ api: { certImport: vi.fn(), certSheet: vi.fn() } }));

function montar() {
  const onImported = vi.fn().mockResolvedValue(undefined);
  render(
    <ToastProvider>
      <CertImportCard cid={1} writable onImported={onImported} />
    </ToastProvider>,
  );
  return onImported;
}

function archivo(contenido: unknown) {
  return new File([JSON.stringify(contenido)], "sets.json", { type: "application/json" });
}

const campo = () => screen.getByLabelText("Archivos del SII o de definiciones");

describe("Cargar los sets del contribuyente", () => {
  beforeEach(() => vi.clearAllMocks());

  it("resume lo que va a cargar y omite lo que no tiene número de atención", async () => {
    const user = userEvent.setup();
    montar();

    await user.upload(
      campo(),
      archivo({
        basico: { code: "5038170", endpoint: "issue-batch", payload: {} },
        boletas: { code: "", endpoint: "boletas", payload: {} },
      }),
    );

    await waitFor(() => expect(screen.getByText(/basico \(5038170\)/)).toBeInTheDocument());
    expect(screen.getByText(/1 omitido\(s\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cargar/ })).toBeInTheDocument();
  });

  it("no ofrece cargar un archivo sin números de atención", async () => {
    const user = userEvent.setup();
    montar();

    await user.upload(campo(), archivo({ basico: { endpoint: "issue-batch", payload: {} } }));

    await waitFor(() =>
      expect(screen.getByText(/Ningún set trae número de atención/)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /Cargar/ })).not.toBeInTheDocument();
  });

  it("lee los archivos del SII, muestra qué cargará y sólo entonces lo guarda", async () => {
    const user = userEvent.setup();
    (api.certSheet as Mock).mockResolvedValue({
      sets: [
        { kind: "basico", code: "5038170", items: 8 },
        { kind: "boletas", code: "", items: 5 },
      ],
      notes: ["basico: IMPORTANTE: los descuentos deben ir en la representación impresa."],
      loaded: false,
    });
    const onImported = montar();

    await user.upload(campo(), [
      new File(["SET BASICO - NUMERO DE ATENCION: 5038170"], "SIISetDePruebas.txt"),
      new File(["SII SET DE PRUEBA DE BOLETA ELECTRONICA"], "Set Prueba BE.txt"),
    ]);

    await waitFor(() => expect(screen.getByText(/basico \(5038170\) · 8/)).toBeInTheDocument());
    expect(screen.getByText(/boletas · 5/)).toBeInTheDocument();
    expect(screen.getByText(/Indicaciones del SII que conviene leer \(1\)/)).toBeInTheDocument();
    // Leer no guarda.
    expect(api.certSheet).toHaveBeenCalledTimes(1);
    const [, subidos, dryRun] = (api.certSheet as Mock).mock.calls[0];
    expect(dryRun).toBe(true);
    expect(subidos.map((f: { name: string }) => f.name)).toEqual([
      "SIISetDePruebas.txt",
      "Set Prueba BE.txt",
    ]);

    await user.click(screen.getByRole("button", { name: /Cargar/ }));

    await waitFor(() => expect(onImported).toHaveBeenCalled());
    expect((api.certSheet as Mock).mock.calls[1][2]).toBe(false);
  });

  it("si el archivo del SII trae algo que no entiende, lo dice y no ofrece cargar", async () => {
    const user = userEvent.setup();
    (api.certSheet as Mock).mockRejectedValue({
      message: "set.txt: línea 21: fila de tabla sin valores: «OBSERVACION RARA»",
    });
    montar();

    await user.upload(campo(), new File(["x"], "set.txt"));

    expect(await screen.findByRole("alert")).toHaveTextContent("línea 21");
    expect(screen.queryByRole("button", { name: /Cargar/ })).not.toBeInTheDocument();
  });
});
