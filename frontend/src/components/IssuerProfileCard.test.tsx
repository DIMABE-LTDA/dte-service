import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../toast";
import type { Customer } from "../types";
import IssuerProfileCard from "./IssuerProfileCard";

vi.mock("../api", () => ({ api: { updateCustomer: vi.fn() } }));

const VACIO = {
  legal_name: null,
  activity: null,
  economic_activity: null,
  address: null,
  commune: null,
  city: null,
  branch_name: null,
  branch_code: null,
};

function cliente(over: Partial<Customer> = {}): Customer {
  return {
    id: 1,
    name: "Dimabe cert",
    key: "dimabe-cert",
    rut: "77262159-0",
    environment: "CERTIFICATION",
    resolution_number: 0,
    resolution_date: "2026-08-26",
    issuer: VACIO,
    issuer_missing: ["razón social", "giro", "código ACTECO", "dirección", "comuna"],
    ...over,
  };
}

function montar(c: Customer) {
  return render(
    <ToastProvider>
      <IssuerProfileCard customer={c} writable onSaved={vi.fn()} />
    </ToastProvider>,
  );
}

describe("Datos del emisor", () => {
  it("dice qué falta y que sin eso no se emite", () => {
    montar(cliente());
    expect(screen.getByText(/Faltan: razón social, giro/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Completar/ })).toBeInTheDocument();
  });

  it("con el perfil completo muestra los datos", () => {
    montar(
      cliente({
        issuer: { ...VACIO, legal_name: "CONSTRUCTORA DIMABE SPA", economic_activity: 439000 },
        issuer_missing: [],
      }),
    );
    expect(screen.getByText("CONSTRUCTORA DIMABE SPA")).toBeInTheDocument();
    expect(screen.queryByText(/Faltan/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Editar/ })).toBeInTheDocument();
  });
});
