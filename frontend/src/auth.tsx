import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Me } from "./types";

interface AuthState {
  user: Me | null;
  loading: boolean;
  login: (
    email: string,
    password: string,
    second?: { totp_code?: string; recovery_code?: string },
  ) => Promise<void>;
  logout: () => Promise<void>;
  /** Vuelve a preguntar quién somos. Lo necesita cualquier pantalla que
   *  cambie algo del propio usuario, como activar el segundo factor: si no,
   *  el topbar sigue mostrando el estado con el que se cargó la sesión. */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // La sesión es una cookie HttpOnly: preguntamos al servidor quién somos.
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(
    email: string,
    password: string,
    second?: { totp_code?: string; recovery_code?: string },
  ) {
    // El servidor setea la cookie de sesión; el token del body se ignora (opción A).
    await api.login(email, password, second);
    setUser(await api.me());
  }

  async function refresh() {
    setUser(await api.me().catch(() => null));
  }

  async function logout() {
    await api.logout().catch(() => undefined);
    setUser(null);
    window.location.assign("/login");
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fuera de AuthProvider");
  return ctx;
}

export const isAdmin = (role?: string) =>
  role === "superadmin" || role === "operator" || role === "auditor";
export const canWrite = (role?: string) => role === "superadmin" || role === "operator";
