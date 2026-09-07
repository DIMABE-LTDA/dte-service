import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { api } from "../api";
import CustomerDetail from "./CustomerDetail";

vi.mock("../api", () => ({
  api: {
    customer: vi.fn(),
    customerServices: vi.fn(),
    customerCerts: vi.fn(),
    customerCafs: vi.fn(),
    services: vi.fn(),
    siiKeyStatus: vi.fn(),
    revokeService: vi.fn(),
    deleteSiiKey: vi.fn(),
  },
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

function mount() {
  return render(
    <MemoryRouter initialEntries={["/customers/1"]}>
      <Routes>
        <Route path="/customers/:id" element={<CustomerDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.customer as Mock).mockResolvedValue({
    id: 1,
    name: "ACME",
    key: "acme",
    rut: "76158145-7",
    environment: "CERTIFICATION",
  });
  (api.customerServices as Mock).mockResolvedValue([
    { service_code: "svc-dte", name: "Emisión de DTE" },
  ]);
  (api.customerCerts as Mock).mockResolvedValue([]);
  (api.customerCafs as Mock).mockResolvedValue([]);
  (api.services as Mock).mockResolvedValue([{ code: "svc-dte", name: "Emisión de DTE" }]);
  (api.siiKeyStatus as Mock).mockResolvedValue({ configured: true });
  (api.revokeService as Mock).mockResolvedValue({});
  (api.deleteSiiKey as Mock).mockResolvedValue({});
});

describe("CustomerDetail · acciones irreversibles", () => {
  it("revocar un servicio pide confirmación antes de llamar al API", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Revocar/ }));

    // El clic abre el diálogo y NO ejecuta nada: es lo que fallaba antes.
    expect(await screen.findByText("Revocar servicio")).toBeInTheDocument();
    expect(api.revokeService).not.toHaveBeenCalled();

    // Cancelar tampoco ejecuta.
    await user.click(screen.getByRole("button", { name: /Cancelar/ }));
    expect(api.revokeService).not.toHaveBeenCalled();
  });

  it("sólo revoca cuando se confirma, y con el servicio correcto", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Revocar/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /Revocar/ }));

    await waitFor(() => expect(api.revokeService).toHaveBeenCalledWith(1, "svc-dte"));
  });

  it("eliminar la clave tributaria pide confirmación antes de llamar al API", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(await screen.findByRole("button", { name: /Eliminar clave/ }));

    expect(await screen.findByText("Eliminar clave tributaria")).toBeInTheDocument();
    expect(api.deleteSiiKey).not.toHaveBeenCalled();

    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^Eliminar$/ }));
    await waitFor(() => expect(api.deleteSiiKey).toHaveBeenCalledWith(1));
  });
});
