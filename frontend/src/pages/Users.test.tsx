import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ToastProvider } from "../toast";
import { describe, expect, it, vi, type Mock } from "vitest";
import { api } from "../api";
import Users from "./Users";

vi.mock("../api", () => ({
  api: {
    users: vi.fn(),
    createUser: vi.fn(),
    setUserActive: vi.fn(),
    setUserPassword: vi.fn(),
    deleteUser: vi.fn(),
    restoreUser: vi.fn(),
    resetUserTotp: vi.fn(),
  },
}));

const USER = {
  id: 1,
  email: "op@dimabe.cl",
  role: "operator",
  customer_id: null,
  is_active: true,
  created_at: "2026-01-01T00:00:00",
  last_login: null,
  deleted_at: null,
};

function renderPage() {
  return render(
    <ToastProvider>
      <Users />
    </ToastProvider>,
  );
}

describe("Users", () => {
  it("muestra la acción de cambiar contraseña por fila", async () => {
    (api.users as Mock).mockResolvedValue([USER]);
    renderPage();
    expect(await screen.findByText("op@dimabe.cl")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cambiar contraseña/ })).toBeInTheDocument();
  });

  it("pide la contraseña dos veces y avisa si no coinciden", async () => {
    (api.users as Mock).mockResolvedValue([USER]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("op@dimabe.cl");

    await user.click(screen.getByRole("button", { name: /Cambiar contraseña/ }));
    const dialog = await screen.findByRole("dialog", { name: "Cambiar contraseña" });
    await user.type(screen.getByLabelText("Contraseña nueva"), "Clave-Nueva-999");
    await user.type(screen.getByLabelText("Repetir contraseña"), "Clave-Distinta-999");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText("Las dos contraseñas no coinciden.")).toBeInTheDocument();
    expect(api.setUserPassword).not.toHaveBeenCalled();
    expect(dialog).toBeInTheDocument();
  });

  it("envía la contraseña nueva cuando ambas coinciden", async () => {
    (api.users as Mock).mockResolvedValue([USER]);
    (api.setUserPassword as Mock).mockResolvedValue({ ...USER });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("op@dimabe.cl");

    await user.click(screen.getByRole("button", { name: /Cambiar contraseña/ }));
    await user.type(screen.getByLabelText("Contraseña nueva"), "Clave-Nueva-999");
    await user.type(screen.getByLabelText("Repetir contraseña"), "Clave-Nueva-999");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(api.setUserPassword).toHaveBeenCalledWith(1, "Clave-Nueva-999"));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Cambiar contraseña" })).toBeNull(),
    );
  });
});
