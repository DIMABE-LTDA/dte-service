import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, type Mock } from "vitest";
import { api } from "../api";
import Customers from "./Customers";

vi.mock("../api", () => ({
  api: { customers: vi.fn() },
}));
vi.mock("../auth", async (orig) => {
  const actual = await orig<typeof import("../auth")>();
  return {
    ...actual,
    useAuth: () => ({
      user: { id: 1, email: "op@x.cl", role: "operator", customer_id: null },
      loading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("Customers", () => {
  it("lista clientes y muestra el alta para operador", async () => {
    (api.customers as Mock).mockResolvedValue([
      { id: 1, name: "ACME", key: "acme", rut: "76158145-7", environment: "CERTIFICATION" },
    ]);
    render(
      <MemoryRouter>
        <Customers />
      </MemoryRouter>,
    );
    expect(await screen.findByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("Nuevo cliente")).toBeInTheDocument();
  });

  it("agrupa las dos fichas de un mismo RUT bajo una empresa", async () => {
    (api.customers as Mock).mockResolvedValue([
      { id: 1, name: "ACME", key: "acme-cert", rut: "76158145-7", environment: "CERTIFICATION" },
      { id: 2, name: "ACME", key: "acme-prod", rut: "76158145-7", environment: "PRODUCTION" },
    ]);
    render(
      <MemoryRouter>
        <Customers />
      </MemoryRouter>,
    );
    // El nombre aparece UNA vez, en la cabecera del grupo, no una por ficha.
    expect(await screen.findByText("2 fichas, una por ambiente")).toBeInTheDocument();
    expect(screen.getAllByText("ACME")).toHaveLength(1);
    // Pero las fichas siguen siendo dos, cada una con su customerCode.
    expect(screen.getByText("acme-cert")).toBeInTheDocument();
    expect(screen.getByText("acme-prod")).toBeInTheDocument();
  });

  it("no agrupa cuando la empresa tiene una sola ficha", async () => {
    (api.customers as Mock).mockResolvedValue([
      { id: 1, name: "ACME", key: "acme", rut: "76158145-7", environment: "CERTIFICATION" },
      { id: 2, name: "OTRA", key: "otra", rut: "77073851-2", environment: "CERTIFICATION" },
    ]);
    render(
      <MemoryRouter>
        <Customers />
      </MemoryRouter>,
    );
    expect(await screen.findByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("OTRA")).toBeInTheDocument();
    expect(screen.queryByText(/fichas, una por ambiente/)).not.toBeInTheDocument();
    // Sin agrupar, el RUT se sigue viendo en su columna.
    expect(screen.getByText("76158145-7")).toBeInTheDocument();
  });

  it("ofrece «Certificar» sólo en certificación, no en producción", async () => {
    // En producción el expediente no existe y el endpoint responde 400: el
    // enlace llevaría a un error, así que no debe estar.
    (api.customers as Mock).mockResolvedValue([
      { id: 1, name: "ACME", key: "acme-cert", rut: "76158145-7", environment: "CERTIFICATION" },
      { id: 2, name: "OTRA", key: "otra-prod", rut: "77262159-0", environment: "PRODUCTION" },
    ]);
    render(
      <MemoryRouter>
        <Customers />
      </MemoryRouter>,
    );

    const certificar = await screen.findAllByRole("link", { name: /Certificar/ });
    expect(certificar).toHaveLength(1);
    expect(certificar[0]).toHaveAttribute("href", "/customers/1/certification");
  });
});
