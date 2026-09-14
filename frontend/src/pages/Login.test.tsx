import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import Login from "./Login";

const { login, navigate } = vi.hoisted(() => ({ login: vi.fn(), navigate: vi.fn() }));
vi.mock("../auth", () => ({ useAuth: () => ({ login }) }));
vi.mock("react-router-dom", () => ({ useNavigate: () => navigate }));

describe("Login", () => {
  it("envía las credenciales escritas", async () => {
    login.mockResolvedValueOnce(undefined);
    const { container } = render(<Login />);
    await userEvent.type(screen.getByRole("textbox"), "a@b.cl");
    await userEvent.type(container.querySelector('input[type="password"]') as HTMLElement, "pw");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    // El tercer argumento es el segundo factor: sin él, undefined.
    expect(login).toHaveBeenCalledWith("a@b.cl", "pw", undefined);
  });

  it("pide el código sólo cuando el API lo exige", async () => {
    // El campo no se muestra de entrada: la mayoría de las cuentas no lo usa, y
    // enseñarlo antes de tiempo sugiere que hace falta siempre.
    login.mockRejectedValueOnce(new Error("totp_required"));
    const { container } = render(<Login />);
    await userEvent.type(screen.getByRole("textbox"), "a@b.cl");
    await userEvent.type(container.querySelector('input[type="password"]') as HTMLElement, "pw");
    expect(screen.queryByText("Código de verificación")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("Código de verificación")).toBeInTheDocument();
    // 'totp_required' es un código interno: no debe salir en pantalla como error.
    expect(screen.queryByText("totp_required")).not.toBeInTheDocument();

    login.mockResolvedValueOnce(undefined);
    await userEvent.type(screen.getByPlaceholderText("6 dígitos"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(login).toHaveBeenLastCalledWith("a@b.cl", "pw", { totp_code: "123456" });
  });

  it("deja cambiar al código de recuperación si se perdió el teléfono", async () => {
    login.mockRejectedValueOnce(new Error("totp_required"));
    const { container } = render(<Login />);
    await userEvent.type(screen.getByRole("textbox"), "a@b.cl");
    await userEvent.type(container.querySelector('input[type="password"]') as HTMLElement, "pw");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));

    await userEvent.click(await screen.findByText(/Perdí el teléfono/));
    login.mockResolvedValueOnce(undefined);
    await userEvent.type(screen.getByPlaceholderText("abcd-ef01-2345"), "aaaa-bbbb-cccc");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(login).toHaveBeenLastCalledWith("a@b.cl", "pw", { recovery_code: "aaaa-bbbb-cccc" });
  });

  it("muestra el error si el login falla", async () => {
    login.mockRejectedValueOnce(new Error("credenciales inválidas"));
    const { container } = render(<Login />);
    await userEvent.type(screen.getByRole("textbox"), "a@b.cl");
    await userEvent.type(container.querySelector('input[type="password"]') as HTMLElement, "x");
    await userEvent.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByText("credenciales inválidas")).toBeInTheDocument();
  });
});
