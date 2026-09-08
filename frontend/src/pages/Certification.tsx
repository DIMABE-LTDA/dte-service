import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { canWrite, useAuth } from "../auth";
import Icon from "../components/Icon";
import { useApi } from "../hooks/useApi";
import type { CertSet, CertSubmission } from "../types";

/** Expediente de certificación de un cliente.
 *
 * Sólo lectura y anotación: desde aquí no se emite ni se envía nada. Lo que
 * resuelve es el seguimiento — qué sets van, en qué etapa está cada uno y
 * dónde está el sobre que hace falta para las muestras de impresión.
 */

/** Color del badge del set. El estado de una ETAPA no lleva badge: el color del
 *  borde ya lo dice, y repetirlo en texto era ruido — además de filtrar los
 *  valores internos del API ("atencion") a la pantalla. */
const ESTADO: Record<string, { color: string; texto: string }> = {
  pendiente: { color: "neutral", texto: "pendiente" },
  enviado: { color: "neutral", texto: "enviado, sin respuesta" },
  aceptado: { color: "warn", texto: "aceptado, falta declarar" },
  rechazado: { color: "error", texto: "rechazado" },
  declarado: { color: "ok", texto: "declarado" },
};

/** Los `kind` vienen en snake_case porque son valores del modelo. */
const TIPO: Record<string, string> = {
  basico: "Set básico",
  exenta: "Factura exenta",
  guias: "Guías de despacho",
  exportacion: "Documentos de exportación",
  liquidacion: "Liquidación factura",
  factura_compra: "Factura de compra",
  libro_ventas: "Libro de ventas",
  libro_compras: "Libro de compras",
  libro_guias: "Libro de guías",
  boletas: "Boletas",
};

function fecha(iso: string | null) {
  return iso
    ? new Date(iso).toLocaleString("es-CL", { dateStyle: "medium", timeStyle: "short" })
    : "—";
}

function descargar(nombre: string, xmlBase64: string) {
  const bytes = Uint8Array.from(atob(xmlBase64), (ch) => ch.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/xml" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Certification() {
  const { id } = useParams();
  const cid = Number(id);
  const { user } = useAuth();
  const writable = canWrite(user?.role);

  const { data, loading, error, reload } = useApi(async () => {
    const [dossier, notes] = await Promise.all([api.certDossier(cid), api.certNotes(cid)]);
    return { dossier, notes };
  }, [cid]);

  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [msg, setMsg] = useState("");
  const [declarando, setDeclarando] = useState<CertSet | null>(null);
  const [fechaDecl, setFechaDecl] = useState(() => new Date().toISOString().slice(0, 10));
  const [asignando, setAsignando] = useState<CertSubmission | null>(null);
  const [codigo, setCodigo] = useState("");
  const [notaSet, setNotaSet] = useState<number | null>(null);
  const [nota, setNota] = useState("");

  async function correr(fn: () => Promise<unknown>, ok: string) {
    setActionError("");
    setMsg("");
    setBusy(true);
    try {
      await fn();
      setMsg(ok);
      await reload();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  const sets = data?.dossier.sets ?? [];
  const sueltos = data?.dossier.unassigned ?? [];
  const declarados = sets.filter((s) => s.state === "declarado").length;

  return (
    <>
      <p>
        <Link to={`/customers/${cid}`}>← Volver a la ficha</Link>
      </p>
      {msg && <p style={{ color: "var(--ok)" }}>{msg}</p>}
      {actionError && <p className="error">{actionError}</p>}

      <div className="card">
        <div className="card-head">
          <h2>Expediente de certificación</h2>
          <span className="spacer" />
          <span className={`badge ${declarados === sets.length && sets.length ? "ok" : "neutral"}`}>
            {declarados} de {sets.length} declarados
          </span>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Cada envío al SII queda guardado con su TrackID y su sobre. Declarar el avance es el único
          paso que se hace fuera: el SII no tiene API para eso.
        </p>
      </div>

      {sets.length === 0 && sueltos.length === 0 && (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            Todavía no hay envíos registrados para este cliente. Aparecerán aquí solos en cuanto se
            emita contra el ambiente de certificación.
          </p>
        </div>
      )}

      {sets.map((s) => (
        <div className="card" key={s.id}>
          <div className="card-head">
            <h2>
              Set <span className="code">{s.code}</span>
            </h2>
            {s.kind && <span className="muted">{TIPO[s.kind] ?? s.kind}</span>}
            <span className="spacer" />
            <span className={`badge ${(ESTADO[s.state] ?? ESTADO.pendiente).color}`}>
              {(ESTADO[s.state] ?? ESTADO.pendiente).texto}
            </span>
          </div>

          <div className="etapas">
            {s.stages.map((e) => (
              <div className={`etapa ${e.state}`} key={e.key}>
                <div className="etapa-titulo">
                  <span className="etapa-punto" aria-hidden="true" />
                  {e.label}
                </div>
                {e.detail && <div className="etapa-detalle">{e.detail}</div>}
              </div>
            ))}
          </div>

          <div className="tabla-scroll">
            <table>
              <thead>
                <tr>
                  <th>TrackID</th>
                  <th>Enviado</th>
                  <th>Sobre</th>
                  <th>Docs</th>
                  <th>Estado SII</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {s.submissions.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <span className="code">{e.track_id}</span>
                    </td>
                    <td className="nowrap">{fecha(e.sent_at)}</td>
                    <td className="muted">{e.envelope_kind}</td>
                    <td>{e.documents.length}</td>
                    <td>
                      {e.sii_state ? (
                        <span
                          className={`badge ${e.sii_state === "EPR" || e.sii_state === "LOK" ? "ok" : "error"}`}
                        >
                          {e.sii_state}
                        </span>
                      ) : (
                        <span className="muted">sin consultar</span>
                      )}
                    </td>
                    <td>
                      <div className="actions">
                        {writable && (
                          <button
                            className="btn-link"
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              correr(() => api.certRefresh(cid, e.id), "Estado actualizado.")
                            }
                          >
                            <Icon name="search" />
                            Consultar
                          </button>
                        )}
                        <button
                          className="btn-link neutral"
                          type="button"
                          onClick={() =>
                            correr(async () => {
                              const env = await api.certEnvelope(cid, e.id);
                              descargar(env.filename, env.xml_base64);
                            }, "Sobre descargado.")
                          }
                        >
                          <Icon name="download" />
                          Sobre
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {writable && (
            <div className="actions" style={{ marginTop: "0.8rem" }}>
              {/* Sólo se ofrece declarar lo que el SII ya aceptó. Declarar un set
                  que aún no respondió —o que rechazó— es informar al Servicio un
                  avance que no ocurrió, y eso no se deshace desde aquí. */}
              {!s.declared_at &&
                (s.state === "aceptado" ? (
                  <button
                    type="button"
                    onClick={() => {
                      setDeclarando(s);
                      setNotaSet(null);
                    }}
                  >
                    <Icon name="check" />
                    Marcar declarado en Mi SII
                  </button>
                ) : (
                  <span className="muted" style={{ fontSize: "0.85rem" }}>
                    {s.state === "rechazado"
                      ? "El SII rechazó el último envío: corrige y reenvía antes de declarar."
                      : "Consulta el estado en el SII antes de declarar el avance."}
                  </span>
                ))}
              <button
                className="secondary"
                type="button"
                onClick={() => {
                  setNotaSet(notaSet === s.id ? null : s.id);
                  setDeclarando(null);
                }}
              >
                <Icon name="edit" />
                Anotar
              </button>
            </div>
          )}

          {declarando?.id === s.id && (
            <form
              className="form-grid"
              style={{ marginTop: "0.8rem" }}
              onSubmit={(ev: FormEvent) => {
                ev.preventDefault();
                correr(
                  () => api.certDeclare(cid, s.id, fechaDecl),
                  `Set ${s.code} marcado como declarado.`,
                ).then(() => setDeclarando(null));
              }}
            >
              <div className="field">
                <label>Fecha en que se declaró el avance</label>
                <input
                  type="date"
                  value={fechaDecl}
                  onChange={(ev) => setFechaDecl(ev.target.value)}
                />
              </div>
              <div className="actions">
                <button disabled={busy}>
                  <Icon name="check" />
                  Confirmar
                </button>
                <button className="secondary" type="button" onClick={() => setDeclarando(null)}>
                  <Icon name="x" />
                  Cancelar
                </button>
              </div>
            </form>
          )}

          {notaSet === s.id && (
            <form
              className="form-grid"
              style={{ marginTop: "0.8rem" }}
              onSubmit={(ev: FormEvent) => {
                ev.preventDefault();
                correr(() => api.certAddNote(cid, s.id, nota), "Anotado.").then(() => {
                  setNota("");
                  setNotaSet(null);
                });
              }}
            >
              <div className="field">
                <label>Qué se probó o se descartó</label>
                <input
                  value={nota}
                  onChange={(ev) => setNota(ev.target.value)}
                  placeholder="No es la firma: xmlsec valida los sobres enviados"
                />
              </div>
              <div className="actions">
                <button disabled={busy || !nota.trim()}>
                  <Icon name="check" />
                  Guardar
                </button>
              </div>
            </form>
          )}

          {(data?.notes ?? [])
            .filter((n) => n.set_id === s.id)
            .map((n) => (
              <p className="muted" key={n.id} style={{ marginBottom: "0.3rem" }}>
                <span className="code">{fecha(n.created_at)}</span> {n.text} <em>— {n.author}</em>
              </p>
            ))}
        </div>
      ))}

      {sueltos.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Envíos sin clasificar</h2>
            <span className="spacer" />
            <span className="badge warn">{sueltos.length}</span>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            Se guardaron sin saber a qué set del SII pertenecen. Preferimos capturarlos así antes
            que perderlos; asígnalos para que entren en el expediente.
          </p>
          <div className="tabla-scroll">
            <table>
              <thead>
                <tr>
                  <th>TrackID</th>
                  <th>Enviado</th>
                  <th>Sobre</th>
                  <th>Docs</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sueltos.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <span className="code">{e.track_id}</span>
                    </td>
                    <td className="nowrap">{fecha(e.sent_at)}</td>
                    <td className="muted">{e.envelope_kind}</td>
                    <td>{e.documents.length}</td>
                    <td>
                      {writable && (
                        <button
                          className="btn-link"
                          type="button"
                          onClick={() => {
                            setAsignando(e);
                            setCodigo("");
                          }}
                        >
                          <Icon name="settings" />
                          Asignar a un set
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {asignando && (
            <form
              className="form-grid"
              style={{ marginTop: "0.8rem" }}
              onSubmit={(ev: FormEvent) => {
                ev.preventDefault();
                correr(
                  () => api.certAssign(cid, asignando.id, codigo, ""),
                  `Envío ${asignando.track_id} asignado al set ${codigo}.`,
                ).then(() => setAsignando(null));
              }}
            >
              <div className="field">
                <label>
                  N.º de atención del set para <span className="code">{asignando.track_id}</span>
                </label>
                <input
                  value={codigo}
                  onChange={(ev) => setCodigo(ev.target.value)}
                  placeholder="5038170"
                  autoFocus
                />
              </div>
              <div className="actions">
                <button disabled={busy || !codigo.trim()}>
                  <Icon name="check" />
                  Asignar
                </button>
                <button className="secondary" type="button" onClick={() => setAsignando(null)}>
                  <Icon name="x" />
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </div>
      )}
    </>
  );
}
