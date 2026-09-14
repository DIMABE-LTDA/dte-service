import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../toast";
import CertImportCard from "./CertImportCard";

vi.mock("../api", () => ({ api: { certImport: vi.fn() } }));

function montar() {
  return render(
    <ToastProvider>
      <CertImportCard cid={1} writable onImported={vi.fn()} />
    </ToastProvider>,
  );
}

function archivo(contenido: unknown) {
  return new File([JSON.stringify(contenido)], "sets.json", { type: "application/json" });
}

describe("Cargar los sets del contribuyente", () => {
  it("resume lo que va a cargar y omite lo que no tiene número de atención", async () => {
    const user = userEvent.setup();
    montar();

    await user.upload(
      screen.getByLabelText("Archivo de definiciones"),
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

    await user.upload(
      screen.getByLabelText("Archivo de definiciones"),
      archivo({ basico: { endpoint: "issue-batch", payload: {} } }),
    );

    await waitFor(() =>
      expect(screen.getByText(/Ningún set trae número de atención/)).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /Cargar/ })).not.toBeInTheDocument();
  });
});
