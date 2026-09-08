import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import Icon from "./Icon";

const TITLES: Record<string, string> = {
  "/customers": "Clientes",
  "/users": "Usuarios",
  "/audit": "Auditoría",
  "/security": "Mi cuenta",
};

function titleFor(path: string): string {
  if (path.endsWith("/certification")) return "Certificación SII";
  if (path.startsWith("/customers/")) return "Detalle de cliente";
  return TITLES[path] ?? "Panel";
}

export default function Layout() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();
  const initial = (user?.email ?? "?").charAt(0);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="logo">D</span>
          <span>DTE Service</span>
        </div>
        <nav>
          <div className="section">Administración</div>
          <NavLink to="/customers">
            <Icon name="customers" width={18} height={18} />
            <span>Clientes</span>
          </NavLink>
          {user?.role === "superadmin" && (
            <NavLink to="/users">
              <Icon name="users" width={18} height={18} />
              <span>Usuarios</span>
            </NavLink>
          )}
          <NavLink to="/audit">
            <Icon name="audit" width={18} height={18} />
            <span>Auditoría</span>
          </NavLink>
        </nav>
        <div className="sidebar-footer">SII Chile · Panel de administración</div>
      </aside>

      <div className="main">
        <header className="topbar">
          <div className="page-title">{titleFor(pathname)}</div>
          <span className="spacer" />
          <NavLink className="user" to="/security" title="Mi cuenta y verificación en dos pasos">
            <span className="avatar">{initial}</span>
            <span>
              {user?.email} · {user?.role}
            </span>
            {!user?.totp_enabled && (
              <span className="badge warn" title="Sin verificación en dos pasos">
                2FA
              </span>
            )}
          </NavLink>
          <button className="secondary sm" onClick={logout}>
            <Icon name="logout" />
            Salir
          </button>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
