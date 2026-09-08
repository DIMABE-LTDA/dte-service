import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, type Mock } from "vitest";
import { api } from "../api";
import type { CertCustomer } from "../types";
import CertificationIndex from "./CertificationIndex";

vi.mock("../api", () => ({
  api: { certIndex: vi.fn() },
}));

function fila(over: Partial<CertCustomer> = {}): CertCustomer {
  return {
    customer_id: 1,
    name: "CONSTRUCTORA DIMABE SPA",
    rut: "77262159-0",
    key: "dimabe-cert",
    progress: { sets_total: 10, sets_declared: 4, sets_accepted: 6, sets_pending: 6 },
    last_activity: "2026-09-02T12:30:00",
    ...over,
  };
}

describe("Portada de certificación", () => {
  it("lista cada contribuyente con su avance y enlaza a su expediente", async () => {
    (api.certIndex as Mock).mockResolvedValue([fila()]);
    render(
      <MemoryRouter>
        <CertificationIndex />
      </MemoryRouter>,
    );

    expect(await screen.findByText("CONSTRUCTORA DIMABE SPA")).toBeInTheDocument();
    expect(screen.getByText(/4 de 10 sets declarados/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Abrir expediente/ })).toHaveAttribute(
      "href",
      "/customers/1/certification",
    );
  });

  it("distingue una certificación sin empezar de una detenida", async () => {
    // La fecha del último envío es lo único que separa «va lenta» de «nadie la
    // ha tocado». Sin ella, las dos se ven igual en el índice.
    (api.certIndex as Mock).mockResolvedValue([fila({ last_activity: null })]);
    render(
      <MemoryRouter>
        <CertificationIndex />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/sin envíos todavía/)).toBeInTheDocument();
  });

  it("cuando no hay ninguno, dice cómo crear uno en vez de quedar en blanco", async () => {
    (api.certIndex as Mock).mockResolvedValue([]);
    render(
      <MemoryRouter>
        <CertificationIndex />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/No hay clientes en ambiente de certificación/)).toBeVisible();
  });
});
