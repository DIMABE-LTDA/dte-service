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
  sin_dar_de_alta: { color: "neutral", texto: "sin dar de alta" },
};

/** Los `kind` vienen en snake_case porque son valores del modelo. */
const TIPO: Record<string, string> = {
  basico: "Set básico",
  exenta: "Factura exenta",
  guias: "Guías de despacho",
  exportacion_1: "Documentos de exportación (1)",
  exportacion_2: "Documentos de exportación (2)",
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
  const [altas, setAltas] = useState<Record<string, string>>({});
  const [editando, setEditando] = useState<number | null>(null);
  const [endpoint, setEndpoint] = useState("issue-batch");
  const [payload, setPayload] = useState("");
  const [clonarDe, setClonarDe] = useState("");

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
  const prog = data?.dossier.progress ?? {
    sets_total: 0,
    sets_declared: 0,
    sets_accepted: 0,
    sets_pending: 0,
  };
  const faltan = sets.filter((s) => s.id === null);

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
          <span className={`badge ${prog.sets_declared === prog.sets_total ? "ok" : "neutral"}`}>
            {prog.sets_declared} de {prog.sets_total} sets declarados
          </span>
        </div>
        {/* Barra de avance: da de un vistazo lo que la tabla cuenta en detalle. */}
        <div className="barra" aria-hidden="true">
          <div
            className="barra-relleno"
            style={{ width: `${(prog.sets_declared / Math.max(prog.sets_total, 1)) * 100}%` }}
          />
        </div>
        <p className="muted" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {prog.sets_accepted} aceptados por el SII · {prog.sets_pending} sin cerrar. Cada envío
          queda guardado con su TrackID y su sobre.
        </p>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Pasos del trámite</h2>
        </div>
        <div className="etapas pasos">
          {(data?.dossier.steps ?? []).map((p, i) => (
            <div className={`etapa ${p.state}`} key={p.key}>
              <div className="etapa-titulo">
                <span className="etapa-punto" aria-hidden="true" />
                {i + 1}. {p.label}
              </div>
              <div className="etapa-detalle">{p.detail}</div>
              {/* El paso 5 se arma desde los sobres guardados: el botón va aquí,
                  que es donde el operador lo busca. */}
              {p.key === "impresion" && writable && (
                <button
                  className="btn-link"
                  type="button"
                  style={{ padding: 0, marginTop: "0.35rem" }}
                  disabled={busy}
                  onClick={() =>
                    correr(async () => {
                      const r = await api.certPrintSamples(cid);
                      const partes = r.documents.map((d) => String(d.html ?? ""));
                      const blob = new Blob(
                        [
                          `<!doctype html><meta charset="utf-8"><title>Muestras de impresión</title>${partes.join("<hr>")}`,
                        ],
                        { type: "text/html" },
                      );
                      const url = URL.createObjectURL(blob);
                      const a = document.createElement("a");
                      a.href = url;
                      a.download = "muestras-impresion.html";
                      a.click();
                      URL.revokeObjectURL(url);
                      if (r.skipped.length) {
                        setActionError(
                          `Se saltaron ${r.skipped.length} sobre(s): ${r.skipped
                            .map((x) => `${x.track_id} (${x.reason})`)
                            .join(", ")}`,
                        );
                      }
                    }, "Muestras generadas: ábrelas e imprímelas a PDF.")
                  }
                >
                  <Icon name="download" />
                  Generar muestras
                </button>
              )}
              {!p.automatic && writable && (
                <button
                  className="btn-link"
                  type="button"
                  style={{ padding: 0, marginTop: "0.35rem" }}
                  disabled={busy}
                  onClick={() =>
                    correr(
                      () =>
                        api.certStep(
                          cid,
                          p.key,
                          p.done_at ? null : new Date().toISOString().slice(0, 10),
                          p.note,
                        ),
                      p.done_at ? `Paso "${p.label}" reabierto.` : `Paso "${p.label}" cerrado.`,
                    )
                  }
                >
                  <Icon name={p.done_at ? "restore" : "check"} />
                  {p.done_at ? `Reabrir (${p.done_at})` : "Marcar cumplido"}
                </button>
              )}
            </div>
          ))}
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          El primero lo lleva el sistema con los sets. Los otros cinco ocurren en el sitio del SII o
          por correo, así que los confirmas tú.
        </p>
      </div>

      {faltan.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Sets sin dar de alta</h2>
            <span className="spacer" />
            <span className="badge warn">{faltan.length}</span>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            El SII asigna a cada contribuyente un número de atención por set. Cópialos de Mi SII una
            vez y el expediente sabrá qué falta; los envíos caerán solos en su sitio.
          </p>
          <div className="tabla-scroll">
            <table>
              <thead>
                <tr>
                  <th>Set</th>
                  <th>N.º de atención</th>
                </tr>
              </thead>
              <tbody>
                {faltan.map((s) => (
                  <tr key={s.kind}>
                    <td>{TIPO[s.kind] ?? s.kind}</td>
                    <td>
                      <input
                        value={altas[s.kind] ?? ""}
                        onChange={(ev) => setAltas({ ...altas, [s.kind]: ev.target.value })}
                        placeholder="p. ej. 5038170"
                        aria-label={`Número de atención de ${TIPO[s.kind] ?? s.kind}`}
                        style={{ maxWidth: "11rem" }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {writable && (
            <button
              disabled={busy || !Object.values(altas).some((v) => v.trim())}
              onClick={() => correr(() => api.certSetup(cid, altas), "Sets dados de alta.")}
            >
              <Icon name="check" />
              Dar de alta
            </button>
          )}
        </div>
      )}

      {sets
        .filter((s): s is CertSet & { id: number } => s.id !== null)
        .map((s) => (
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
                  {s.submissions.flatMap((e) => [
                    <tr key={e.id}>
                      <td>
                        {e.track_id ? (
                          <span className="code">{e.track_id}</span>
                        ) : (
                          <span className="muted">sin enviar</span>
                        )}
                      </td>
                      <td className="nowrap">{fecha(e.sent_at)}</td>
                      <td className="muted">{e.envelope_kind}</td>
                      <td>{e.documents.length}</td>
                      <td>
                        {!e.track_id ? (
                          <span className="badge warn">emitido, sin enviar</span>
                        ) : e.sii_state ? (
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
                          {writable && !e.track_id && (
                            <button
                              className="btn-link"
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                correr(() => api.certSend(cid, e.id), "Sobre enviado al SII.")
                              }
                            >
                              <Icon name="upload" />
                              Enviar al SII
                            </button>
                          )}
                          {writable && e.track_id && (
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
                    </tr>,
                    // La guía aparece sólo cuando aporta: un aceptado sin nada
                    // que revisar no necesita una fila que empuje la tabla.
                    e.cause && (e.cause.usually || e.cause.check.length) ? (
                      <tr key={`${e.id}-guia`} className="guia">
                        <td colSpan={6}>
                          <div className={`guia-caja ${e.cause.ok ? "ok" : "error"}`}>
                            <strong>{e.cause.label}.</strong> {e.cause.meaning}
                            {e.cause.usually && (
                              <>
                                {" "}
                                <em>{e.cause.usually}</em>
                              </>
                            )}
                            {e.cause.check.length > 0 && (
                              <ol className="guia-pasos">
                                {e.cause.check.map((paso) => (
                                  <li key={paso}>{paso}</li>
                                ))}
                              </ol>
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null,
                  ])}
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
                  disabled={busy}
                  onClick={() =>
                    correr(
                      () => api.certEmit(cid, s.id),
                      `Set ${s.code} emitido. Revísalo y envíalo cuando esté bien.`,
                    )
                  }
                >
                  <Icon name="plus" />
                  Emitir
                </button>
                <button
                  className="secondary"
                  type="button"
                  onClick={async () => {
                    if (editando === s.id) {
                      setEditando(null);
                      return;
                    }
                    setDeclarando(null);
                    setNotaSet(null);
                    setEditando(s.id);
                    setPayload("");
                    try {
                      const d = await api.certDefinition(cid, s.id);
                      setEndpoint(d.endpoint);
                      setPayload(JSON.stringify(d.payload, null, 2));
                    } catch {
                      setEndpoint("issue-batch");
                      setPayload("{}");
                    }
                  }}
                >
                  <Icon name="settings" />
                  Qué emitir
                </button>
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

            {editando === s.id && (
              <form
                className="form-grid"
                style={{ marginTop: "0.8rem" }}
                onSubmit={(ev: FormEvent) => {
                  ev.preventDefault();
                  let cuerpo: unknown;
                  try {
                    cuerpo = JSON.parse(payload);
                  } catch {
                    setActionError("El contenido no es JSON válido.");
                    return;
                  }
                  correr(
                    () => api.certSaveDefinition(cid, s.id, endpoint, cuerpo),
                    "Definición guardada.",
                  ).then(() => setEditando(null));
                }}
              >
                <div className="field">
                  <label>Endpoint de emisión</label>
                  <select value={endpoint} onChange={(ev) => setEndpoint(ev.target.value)}>
                    <option value="issue-batch">Documentos en lote (33, 34, 52, 56, 61, 46)</option>
                    <option value="issue-export-batch">Exportación (110, 111, 112)</option>
                    <option value="issue-settlement-batch">Liquidación factura (43)</option>
                    <option value="books">Libro de compras / ventas</option>
                    <option value="books/guides">Libro de guías</option>
                  </select>
                </div>
                <div className="field">
                  <label>
                    Cuerpo de la emisión — se guarda tal cual, así que esto es exactamente lo que se
                    enviará
                  </label>
                  <textarea
                    value={payload}
                    onChange={(ev) => setPayload(ev.target.value)}
                    rows={12}
                    spellCheck={false}
                    className="json"
                  />
                </div>
                <div className="actions">
                  <button disabled={busy}>
                    <Icon name="check" />
                    Guardar
                  </button>
                  <button className="secondary" type="button" onClick={() => setEditando(null)}>
                    <Icon name="x" />
                    Cancelar
                  </button>
                  <span className="spacer" />
                  <input
                    value={clonarDe}
                    onChange={(ev) => setClonarDe(ev.target.value)}
                    placeholder="id de otro cliente"
                    style={{ maxWidth: "11rem" }}
                    aria-label="Cliente del que copiar la definición"
                  />
                  <button
                    className="secondary"
                    type="button"
                    disabled={busy || !clonarDe.trim()}
                    title="Copia la definición del mismo tipo de set desde otro contribuyente ya probado"
                    onClick={() =>
                      correr(async () => {
                        const d = await api.certCloneDefinition(cid, s.id, Number(clonarDe));
                        setEndpoint(d.endpoint);
                        setPayload(JSON.stringify(d.payload, null, 2));
                      }, "Definición copiada. Revísala antes de emitir.")
                    }
                  >
                    <Icon name="copy" />
                    Clonar
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
