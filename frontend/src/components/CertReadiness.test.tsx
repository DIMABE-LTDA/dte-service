import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../toast";
import type { CertReadiness } from "../types";
import CertReadinessPanel from "./CertReadiness";

vi.mock("../api", () => ({ api: { certCheckSii: vi.fn() } }));

function montar(data: CertReadiness) {
  return render(
    <ToastProvider>
      <CertReadinessPanel
        cid={1}
        data={data}
        loading={false}
        error={null}
        reload={vi.fn()}
        writable
      />
    </ToastProvider>,
  );
}

const bloqueada: CertReadiness = {
  ready: false,
  errors: 1,
  warnings: 0,
  checked_at: "2026-09-10T10:00:00",
  groups: [
    {
      key: "emisor",
      label: "Emisor",
      state: "ok",
      checks: [
        { key: "ambiente", label: "Ambiente", state: "ok", detail: "certificación", fix: "" },
      ],
    },
    {
      key: "certificado",
      label: "Certificado",
      state: "error",
      checks: [
        {
          key: "certificado",
          label: "Certificado de firma",
          state: "error",
          detail: "PRUEBA 12291733-9 — es AUTOFIRMADO",
          fix: "Sube el de la entidad acreditada.",
        },
      ],
    },
  ],
};

describe("Verificación antes de emitir", () => {
  it("dice que no se puede emitir y abre sólo lo que falla", () => {
    montar(bloqueada);

    expect(screen.getByText(/1 problema\(s\) impiden emitir/)).toBeInTheDocument();
    // Lo que falla se ve sin hacer clic, con qué hacer.
    const cert = screen.getByText("Certificado").closest("details");
    expect(cert).toHaveAttribute("open");
    expect(screen.getByText(/Sube el de la entidad acreditada/)).toBeVisible();
    // Lo que está bien queda plegado: no compite con lo que hay que arreglar.
    const emisor = screen.getByText("Emisor").closest("details");
    expect(emisor).not.toHaveAttribute("open");
  });

  it("con todo en orden dice que se puede emitir", () => {
    montar({ ...bloqueada, ready: true, errors: 0, groups: [bloqueada.groups[0]] });
    expect(screen.getByText(/Listo para emitir/)).toBeInTheDocument();
  });
});
