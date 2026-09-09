import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../api";
import { canWrite, useAuth } from "../auth";
import ConfirmModal from "../components/ConfirmModal";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useApi } from "../hooks/useApi";
import { useToast } from "../toast";
import type { Customer } from "../types";

const EMPTY = {
  name: "",
  rut: "",
  environment: "CERTIFICATION",
  resolution_number: "",
  resolution_date: "",
};

type Confirm = { kind: "delete" | "restore"; customer: Customer };

type Company = { rut: string; name: string; fichas: Customer[] };

/** Agrupa las fichas por RUT.
 *
 * Una empresa que opera en certificación y en producción son dos clientes
 * distintos —cada uno con su certificado, sus CAF y sus correlativos— y esa
 * separación es deliberada. Pero en la lista se leía como dos empresas: aquí
 * se muestran juntas sin que dejen de ser fichas independientes.
 */
function groupByRut(items: Customer[]): Company[] {
  const byRut = new Map<string, Company>();
  for (const c of items) {
    const g = byRut.get(c.rut);
    if (g) g.fichas.push(c);
    else byRut.set(c.rut, { rut: c.rut, name: c.name, fichas: [c] });
  }
  return [...byRut.values()].sort((a, b) => a.name.localeCompare(b.name, "es"));
}

export default function Customers() {
  const { user } = useAuth();
  const writable = canWrite(user?.role);
  const [showArchived, setShowArchived] = useState(false);
  const {
    data: items,
    loading,
    error,
    reload,
  } = useApi(() => api.customers(showArchived), [showArchived]);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Customer | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [actionError, setActionError] = useState("");
  const toast = useToast();

  /** Muestra el fallo donde se está mirando y, además, como aviso global. */
  function avisar(err: unknown) {
    const e = err as ApiError;
    setActionError(e.message);
    toast.error(e.message, e.hints);
  }

  function openCreate() {
    setEditing(null);
    setForm(EMPTY);
    setFormError("");
    setCreated(null);
    setOpen(true);
  }

  function openEdit(c: Customer) {
    setEditing(c);
    // Precargados: el formulario se abría vacío, así que editar cualquier
    // cosa obligaba a recordar de memoria el número de resolución para no
    // dejarlo atrás.
    setForm({
      ...EMPTY,
      name: c.name,
      rut: c.rut,
      environment: c.environment,
      resolution_number: c.resolution_number ? String(c.resolution_number) : "",
      resolution_date: c.resolution_date ?? "",
    });
    setFormError("");
    setCreated(null);
    setOpen(true);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setFormError("");
    setBusy(true);
    try {
      const payload: {
        name: string;
        rut: string;
        environment: string;
        resolution_number?: number;
        resolution_date?: string;
      } = { name: form.name, rut: form.rut, environment: form.environment };
      if (form.environment === "PRODUCTION" && form.resolution_number) {
        payload.resolution_number = Number(form.resolution_number);
        if (form.resolution_date) payload.resolution_date = form.resolution_date;
      }
      if (editing) {
        await api.updateCustomer(editing.id, payload);
      } else {
        setCreated(await api.createCustomer(payload));
      }
      setOpen(false);
      await reload();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function doConfirm() {
    if (!confirm) return;
    setActionError("");
    setBusy(true);
    try {
      if (confirm.kind === "delete") await api.deleteCustomer(confirm.customer.id);
      else await api.restoreCustomer(confirm.customer.id);
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

      {created && (
        <div className="notice ok">
          Cliente <strong>{created.name}</strong> creado. Su código de cliente generado es:
          <div className="secret">
            <span className="code">{created.key}</span>
            <button
              className="secondary sm"
              type="button"
              onClick={() => navigator.clipboard?.writeText(created.key)}
            >
              <Icon name="copy" />
              Copiar
            </button>
            <Link className="btn-link neutral" to={`/customers/${created.id}`}>
              <Icon name="settings" />
              Gestionar
            </Link>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Clientes registrados</h2>
          <span className="spacer" />
          <label className="toggle">
            <input
              type="checkbox"
              checked={showArchived}
              onChange={(e) => setShowArchived(e.target.checked)}
            />
            Mostrar archivados
          </label>
          {writable && (
            <button onClick={openCreate}>
              <Icon name="plus" />
              Nuevo cliente
            </button>
          )}
        </div>
        {loading ? (
          <p className="muted">Cargando…</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Nombre</th>
                <th>Código</th>
                <th>RUT</th>
                <th>Ambiente</th>
                <th />
              </tr>
            </thead>
            {groupByRut(items ?? []).map((company) => {
              // Una sola ficha se muestra como siempre; con dos o más, el
              // nombre y el RUT suben a una cabecera y no se repiten fila a fila.
              const agrupada = company.fichas.length > 1;
              return (
                <tbody key={company.rut}>
                  {agrupada && (
                    <tr className="group-head">
                      <td />
                      <td colSpan={5}>
                        <strong>{company.name}</strong> · RUT {company.rut} ·{" "}
                        <span className="muted">
                          {company.fichas.length} fichas, una por ambiente
                        </span>
                      </td>
                    </tr>
                  )}
                  {company.fichas.map((c) => {
                    const archived = !!c.deleted_at;
                    return (
                      <tr key={c.id} className={archived ? "archived" : ""}>
                        <td>{c.id}</td>
                        <td className={agrupada ? "sub" : ""}>
                          {agrupada ? (
                            <span className="muted">↳</span>
                          ) : (
                            <>
                              {c.name}
                              {archived && <span className="badge neutral"> archivado</span>}
                            </>
                          )}
                          {agrupada && archived && <span className="badge denied"> archivado</span>}
                        </td>
                        <td>
                          <span className="code">{c.key}</span>
                        </td>
                        <td className="nowrap">{agrupada ? "" : c.rut}</td>
                        <td>
                          <span
                            className={`badge ${c.environment === "PRODUCTION" ? "warn" : "neutral"}`}
                          >
                            {c.environment}
                          </span>
                        </td>
                        <td>
                          <div className="actions">
                            <Link className="btn-link neutral" to={`/customers/${c.id}`}>
                              <Icon name="settings" />
                              Gestionar
                            </Link>
                            {c.environment === "CERTIFICATION" && !archived && (
                              <Link className="btn-link" to={`/customers/${c.id}/certification`}>
                                <Icon name="audit" />
                                Certificar
                              </Link>
                            )}
                            {writable && !archived && (
                              <>
                                <button
                                  className="btn-link"
                                  type="button"
                                  onClick={() => openEdit(c)}
                                >
                                  <Icon name="edit" />
                                  Editar
                                </button>
                                <button
                                  className="btn-link danger"
                                  type="button"
                                  onClick={() => setConfirm({ kind: "delete", customer: c })}
                                >
                                  <Icon name="trash" />
                                  Eliminar
                                </button>
                              </>
                            )}
                            {writable && archived && (
                              <button
                                className="btn-link"
                                type="button"
                                onClick={() => setConfirm({ kind: "restore", customer: c })}
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
                </tbody>
              );
            })}
            {items && items.length === 0 && (
              <tbody>
                <tr>
                  <td colSpan={6} className="muted">
                    Sin clientes.
                  </td>
                </tr>
              </tbody>
            )}
          </table>
        )}
      </div>

      {open && (
        <Modal
          title={editing ? `Editar cliente · ${editing.name}` : "Nuevo cliente"}
          onClose={() => setOpen(false)}
          footer={
            <>
              <button className="secondary" type="button" onClick={() => setOpen(false)}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="customer-form" disabled={busy}>
                <Icon name="check" />
                {editing ? "Guardar cambios" : "Crear cliente"}
              </button>
            </>
          }
        >
          <form id="customer-form" className="form-grid" onSubmit={submit}>
            {formError && <p className="error">{formError}</p>}
            {editing ? (
              <div className="field">
                <label>Código de cliente (no editable)</label>
                <span className="code">{editing.key}</span>
              </div>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                El código de cliente (customerCode) se genera automáticamente.
              </p>
            )}
            <div className="field">
              <label>Nombre</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
              />
            </div>
            <div className="field">
              <label>RUT</label>
              <input
                value={form.rut}
                onChange={(e) => setForm({ ...form, rut: e.target.value })}
                placeholder="76158145-7"
                required
              />
            </div>
            <div className="field">
              <label>Ambiente</label>
              <select
                value={form.environment}
                onChange={(e) => setForm({ ...form, environment: e.target.value })}
              >
                <option value="CERTIFICATION">Certificación</option>
                <option value="PRODUCTION">Producción</option>
              </select>
            </div>
            {form.environment === "PRODUCTION" && (
              <>
                <div className="field">
                  <label>N° resolución</label>
                  <input
                    value={form.resolution_number}
                    onChange={(e) => setForm({ ...form, resolution_number: e.target.value })}
                    placeholder={editing ? "(dejar vacío = sin cambio)" : ""}
                  />
                </div>
                <div className="field">
                  <label>Fecha resolución</label>
                  <input
                    type="date"
                    value={form.resolution_date}
                    onChange={(e) => setForm({ ...form, resolution_date: e.target.value })}
                  />
                </div>
              </>
            )}
          </form>
        </Modal>
      )}

      {confirm && (
        <ConfirmModal
          title={confirm.kind === "delete" ? "Eliminar cliente" : "Reactivar cliente"}
          danger={confirm.kind === "delete"}
          busy={busy}
          confirmLabel={confirm.kind === "delete" ? "Eliminar" : "Reactivar"}
          confirmIcon={confirm.kind === "delete" ? "trash" : "restore"}
          onClose={() => setConfirm(null)}
          onConfirm={doConfirm}
          message={
            confirm.kind === "delete" ? (
              <>
                ¿Archivar el cliente <strong>{confirm.customer.name}</strong>? No podrá autenticarse
                ni aparecerá en los listados. Se conserva su historial (folios, auditoría) y podrás
                reactivarlo después.
              </>
            ) : (
              <>
                ¿Reactivar el cliente <strong>{confirm.customer.name}</strong>? Volverá a estar
                operativo.
              </>
            )
          }
        />
      )}
    </>
  );
}
