import { useState, type FormEvent } from "react";
import { api, type ApiError } from "../api";
import ConfirmModal from "../components/ConfirmModal";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useApi } from "../hooks/useApi";
import { useToast } from "../toast";
import type { User } from "../types";

const EMPTY = { email: "", password: "", role: "operator", customer_id: "" };

type Confirm = { kind: "delete" | "restore" | "totp"; user: User };

const TITULOS = {
  delete: "Eliminar usuario",
  restore: "Reactivar usuario",
  totp: "Resetear la verificación en dos pasos",
} as const;
const ETIQUETAS = { delete: "Eliminar", restore: "Reactivar", totp: "Resetear" } as const;
const ICONOS = { delete: "trash", restore: "restore", totp: "key" } as const;

const EMPTY_PW = { password: "", confirm: "" };

export default function Users() {
  const [showArchived, setShowArchived] = useState(false);
  const {
    data: items,
    loading,
    error,
    reload,
  } = useApi(() => api.users(showArchived), [showArchived]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const toast = useToast();

  const [pwTarget, setPwTarget] = useState<User | null>(null);
  const [pwForm, setPwForm] = useState(EMPTY_PW);
  const [pwError, setPwError] = useState("");

  /** Muestra el fallo donde se está mirando y, además, como aviso global. */
  function avisar(err: unknown) {
    const e = err as ApiError;
    setActionError(e.message);
    toast.error(e.message, e.hints);
  }
  const [confirm, setConfirm] = useState<Confirm | null>(null);

  function openCreate() {
    setForm(EMPTY);
    setFormError("");
    setOpen(true);
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    setFormError("");
    setBusy(true);
    try {
      await api.createUser({
        email: form.email,
        password: form.password,
        role: form.role,
        customer_id: form.role === "client" ? Number(form.customer_id) : null,
      });
      setOpen(false);
      await reload();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function openPassword(u: User) {
    setPwTarget(u);
    setPwForm(EMPTY_PW);
    setPwError("");
  }

  /** Cierra el diálogo y suelta lo escrito.
   *
   * Sin esto la contraseña en claro se queda en el estado del componente hasta
   * que alguien vuelva a abrir el diálogo: no se ve en pantalla, pero sigue en
   * memoria y aparece en las herramientas de desarrollo de React. Es gratis no
   * conservarla. */
  function closePassword() {
    setPwTarget(null);
    setPwForm(EMPTY_PW);
    setPwError("");
  }

  async function changePassword(e: FormEvent) {
    e.preventDefault();
    if (!pwTarget) return;
    if (pwForm.password !== pwForm.confirm) {
      setPwError("Las dos contraseñas no coinciden.");
      return;
    }
    setPwError("");
    setBusy(true);
    try {
      await api.setUserPassword(pwTarget.id, pwForm.password);
      closePassword();
      toast.ok(`Contraseña actualizada para ${pwTarget.email}.`);
    } catch (err) {
      setPwError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggle(u: User) {
    setActionError("");
    try {
      await api.setUserActive(u.id, !u.is_active);
      await reload();
    } catch (err) {
      avisar(err);
    }
  }

  async function doConfirm() {
    if (!confirm) return;
    setActionError("");
    setBusy(true);
    try {
      if (confirm.kind === "delete") await api.deleteUser(confirm.user.id);
      else if (confirm.kind === "totp") await api.resetUserTotp(confirm.user.id);
      else await api.restoreUser(confirm.user.id);
      setConfirm(null);
      await reload();
    } catch (err) {
      avisar(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {error && <p className="error">{error}</p>}
      {actionError && <p className="error">{actionError}</p>}

      <div className="card">
        <div className="card-head">
          <h2>Usuarios del portal</h2>
          <span className="spacer" />
          <label className="toggle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Mostrar archivados
          </label>
          <button onClick={openCreate}>
            <Icon name="plus" />
            Nuevo usuario
          </button>
        </div>
        {loading ? (
          <p className="muted">Cargando…</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Email</th>
                <th>Rol</th>
                <th>Cliente</th>
                <th>Activo</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(items ?? []).map((u) => {
                const archived = !!u.deleted_at;
                return (
                  <tr key={u.id} className={archived ? "archived" : ""}>
                    <td>{u.id}</td>
                    <td>
                      {u.email}
                      {archived && <span className="badge neutral"> archivado</span>}
                    </td>
                    <td>{u.role}</td>
                    <td>{u.customer_id ?? "—"}</td>
                    <td>
                      <span className={`badge ${u.is_active ? "ok" : "error"}`}>
                        {u.is_active ? "sí" : "no"}
                      </span>
                    </td>
                    <td>
                      <div className="actions">
                        {!archived && (
                          <>
                            <button
                              className="btn-link"
                              type="button"
                              title="Apaga su verificación en dos pasos: la salida cuando alguien pierde el teléfono y los códigos"
                              onClick={() => setConfirm({ kind: "totp", user: u })}
                            >
                              <Icon name="key" />
                              Resetear 2FA
                            </button>
                            <button
                              className="btn-link"
                              type="button"
                              title="Fija una contraseña nueva para este usuario"
                              onClick={() => openPassword(u)}
                            >
                              <Icon name="edit" />
                              Cambiar contraseña
                            </button>
                            <button className="btn-link" type="button" onClick={() => toggle(u)}>
                              <Icon name="power" />
                              {u.is_active ? "Desactivar" : "Activar"}
                            </button>
                            <button
                              className="btn-link danger"
                              type="button"
                              onClick={() => setConfirm({ kind: "delete", user: u })}
                            >
                              <Icon name="trash" />
                              Eliminar
                            </button>
                          </>
                        )}
                        {archived && (
                          <button
                            className="btn-link"
                            type="button"
                            onClick={() => setConfirm({ kind: "restore", user: u })}
                          >
                            <Icon name="restore" />
                            Reactivar
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {items && items.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    Sin usuarios.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {open && (
        <Modal
          title="Nuevo usuario"
          onClose={() => setOpen(false)}
          footer={
            <>
              <button className="secondary" type="button" onClick={() => setOpen(false)}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="user-form" disabled={busy}>
                <Icon name="check" />
                Crear usuario
              </button>
            </>
          }
        >
          <form id="user-form" className="form-grid" onSubmit={create}>
            {formError && <p className="error">{formError}</p>}
            <div className="field">
              <label>Email</label>
              <input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                required
              />
            </div>
            <div className="field">
              <label>Contraseña</label>
              <input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required
              />
            </div>
            <div className="field">
              <label>Rol</label>
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
              >
                <option value="superadmin">superadmin</option>
                <option value="operator">operator</option>
                <option value="auditor">auditor</option>
                <option value="client">client</option>
              </select>
            </div>
            {form.role === "client" && (
              <div className="field">
                <label>customer_id</label>
                <input
                  value={form.customer_id}
                  onChange={(e) => setForm({ ...form, customer_id: e.target.value })}
                  required
                />
              </div>
            )}
          </form>
        </Modal>
      )}

      {pwTarget && (
        <Modal
          title="Cambiar contraseña"
          onClose={closePassword}
          footer={
            <>
              <button className="secondary" type="button" onClick={closePassword}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="password-form" disabled={busy}>
                <Icon name="check" />
                Guardar
              </button>
            </>
          }
        >
          <form id="password-form" className="form-grid" onSubmit={changePassword}>
            <p className="muted" style={{ margin: 0 }}>
              Nueva contraseña para <strong>{pwTarget.email}</strong>. Queda anotado en la auditoría
              de cambios que se le cambió la contraseña (no el valor).
            </p>
            {pwError && <p className="error">{pwError}</p>}
            <div className="field">
              <label htmlFor="pw-new">Contraseña nueva</label>
              <input
                id="pw-new"
                type="password"
                autoComplete="new-password"
                value={pwForm.password}
                onChange={(e) => setPwForm({ ...pwForm, password: e.target.value })}
                required
              />
            </div>
            <div className="field">
              <label htmlFor="pw-confirm">Repetir contraseña</label>
              <input
                id="pw-confirm"
                type="password"
                autoComplete="new-password"
                value={pwForm.confirm}
                onChange={(e) => setPwForm({ ...pwForm, confirm: e.target.value })}
                required
              />
            </div>
          </form>
        </Modal>
      )}

      {confirm && (
        <ConfirmModal
          title={TITULOS[confirm.kind]}
          danger={confirm.kind !== "restore"}
          busy={busy}
          confirmLabel={ETIQUETAS[confirm.kind]}
          confirmIcon={ICONOS[confirm.kind]}
          onClose={() => setConfirm(null)}
          onConfirm={doConfirm}
          message={
            confirm.kind === "delete" ? (
              <>
                ¿Archivar al usuario <strong>{confirm.user.email}</strong>? No podrá iniciar sesión.
                Podrás reactivarlo después.
              </>
            ) : confirm.kind === "totp" ? (
              <>
                ¿Apagar la verificación en dos pasos de <strong>{confirm.user.email}</strong>? Su
                cuenta quedará protegida sólo por la contraseña hasta que vuelva a activarla, y se
                borran sus códigos de recuperación. Hazlo únicamente si te lo pidió esa persona:
                queda anotado en la auditoría de cambios.
              </>
            ) : (
              <>
                ¿Reactivar al usuario <strong>{confirm.user.email}</strong>?
              </>
            )
          }
        />
      )}
    </>
  );
}
