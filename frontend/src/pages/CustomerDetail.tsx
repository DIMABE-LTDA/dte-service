import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { canWrite, useAuth } from "../auth";
import ConfirmModal from "../components/ConfirmModal";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useApi } from "../hooks/useApi";
import type { BheResponse, CafInfo, GrantedService, RcvResponse } from "../types";

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",")[1] ?? "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

const money = (n: number) => "$" + n.toLocaleString("es-CL");
// El backend usa período AAAAMM; el selector <input type="month"> da "AAAA-MM".
const toPeriod = (month: string) => month.replace("-", "");

type ModalKind = "grant" | "cert" | "caf" | "sii" | "rcv" | "bhe" | null;

export default function CustomerDetail() {
  const { id } = useParams();
  const cid = Number(id);
  const { user } = useAuth();
  const writable = canWrite(user?.role);

  const { data, loading, error, reload } = useApi(async () => {
    const [customer, granted, certs, cafs, services, siiKey] = await Promise.all([
      api.customer(cid),
      api.customerServices(cid),
      api.customerCerts(cid),
      api.customerCafs(cid),
      api.services(),
      api.siiKeyStatus(cid),
    ]);
    return { customer, granted, certs, cafs, services, siiKey };
  }, [cid]);

  const [modal, setModal] = useState<ModalKind>(null);
  const [msg, setMsg] = useState("");
  const [actionError, setActionError] = useState("");
  const [modalError, setModalError] = useState("");
  const [busy, setBusy] = useState(false);
  const [grantedKey, setGrantedKey] = useState<string | null>(null);
  const [grantSvc, setGrantSvc] = useState("");
  const [grantKey, setGrantKey] = useState("");
  const [certFile, setCertFile] = useState<File | null>(null);
  const [certPass, setCertPass] = useState("");
  const [cafFile, setCafFile] = useState<File | null>(null);
  const [confirmCaf, setConfirmCaf] = useState<CafInfo | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState<GrantedService | null>(null);
  const [confirmSiiKey, setConfirmSiiKey] = useState(false);
  const keyNotice = useRef<HTMLDivElement>(null);
  const [siiPass, setSiiPass] = useState("");
  const [period, setPeriod] = useState(() => new Date().toISOString().slice(0, 7));
  const [operation, setOperation] = useState("COMPRA");
  const [rcv, setRcv] = useState<RcvResponse | null>(null);
  const [bhePeriod, setBhePeriod] = useState(() => new Date().toISOString().slice(0, 7));
  const [bhe, setBhe] = useState<BheResponse | null>(null);

  // La apiKey se ve UNA vez y el aviso se pinta al principio de la página: si
  // el operador estaba abajo, se lo perdía sin enterarse.
  useEffect(() => {
    if (grantedKey) keyNotice.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [grantedKey]);

  function openModal(kind: Exclude<ModalKind, null>) {
    setActionError("");
    setMsg("");
    setModalError("");
    setModal(kind);
  }
  const close = () => setModal(null);
  function openGrant() {
    setGrantSvc("");
    setGrantKey("");
    setGrantedKey(null);
    openModal("grant");
  }
  function openCert() {
    setCertFile(null);
    setCertPass("");
    openModal("cert");
  }
  function openCaf() {
    setCafFile(null);
    openModal("caf");
  }
  function openSii() {
    setSiiPass("");
    openModal("sii");
  }
  function openRcv() {
    setRcv(null);
    openModal("rcv");
  }
  function openBhe() {
    setBhe(null);
    openModal("bhe");
  }

  function grant(e: FormEvent) {
    e.preventDefault();
    setModalError("");
    setMsg("");
    setGrantedKey(null);
    setBusy(true);
    api
      .grant(cid, grantSvc, grantKey || undefined)
      .then((res) => {
        setMsg("Servicio habilitado.");
        setGrantedKey(res.apikey ?? null);
        setModal(null);
        return reload();
      })
      .catch((err) => setModalError((err as Error).message))
      .finally(() => setBusy(false));
  }
  function retireCaf() {
    if (!confirmCaf) return;
    setActionError("");
    setMsg("");
    setBusy(true);
    api
      .retireCaf(cid, confirmCaf.id)
      .then(() => {
        setMsg(`CAF tipo ${confirmCaf.doc_type} retirado.`);
        setConfirmCaf(null);
        return reload();
      })
      .catch((err) => setActionError((err as Error).message))
      .finally(() => setBusy(false));
  }
  function revoke() {
    if (!confirmRevoke) return;
    setActionError("");
    setMsg("");
    setBusy(true);
    api
      .revokeService(cid, confirmRevoke.service_code)
      .then(() => {
        setMsg(`Servicio "${confirmRevoke.name}" revocado.`);
        setConfirmRevoke(null);
        return reload();
      })
      .catch((err) => setActionError((err as Error).message))
      .finally(() => setBusy(false));
  }
  async function uploadCert(e: FormEvent) {
    e.preventDefault();
    if (!certFile) return;
    setModalError("");
    setMsg("");
    setBusy(true);
    try {
      const b64 = await fileToBase64(certFile);
      await api.uploadCert(cid, b64, certPass);
      setMsg("Certificado cargado.");
      setModal(null);
      await reload();
    } catch (err) {
      setModalError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function uploadCaf(e: FormEvent) {
    e.preventDefault();
    if (!cafFile) return;
    setModalError("");
    setMsg("");
    setBusy(true);
    try {
      const b64 = await fileToBase64(cafFile);
      await api.uploadCaf(cid, b64);
      setMsg("CAF cargado.");
      setModal(null);
      await reload();
    } catch (err) {
      setModalError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveSiiKey(e: FormEvent) {
    e.preventDefault();
    if (!siiPass) return;
    setModalError("");
    setMsg("");
    setBusy(true);
    try {
      await api.setSiiKey(cid, siiPass);
      setMsg("Clave tributaria guardada.");
      setModal(null);
      await reload();
    } catch (err) {
      setModalError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function deleteSiiKey() {
    setActionError("");
    setMsg("");
    setBusy(true);
    api
      .deleteSiiKey(cid)
      .then(() => {
        setMsg("Clave tributaria eliminada.");
        setConfirmSiiKey(false);
        return reload();
      })
      .catch((err) => setActionError((err as Error).message))
      .finally(() => setBusy(false));
  }
  function queryRcv(e: FormEvent) {
    e.preventDefault();
    setModalError("");
    setBusy(true);
    api
      .rcv(cid, toPeriod(period), operation)
      .then(setRcv)
      .catch((err) => setModalError((err as Error).message))
      .finally(() => setBusy(false));
  }
  function queryBhe(e: FormEvent) {
    e.preventDefault();
    setModalError("");
    setBusy(true);
    api
      .bheReceived(cid, toPeriod(bhePeriod))
      .then(setBhe)
      .catch((err) => setModalError((err as Error).message))
      .finally(() => setBusy(false));
  }

  if (!data) {
    if (loading) return <p className="muted">Cargando cliente…</p>;
    if (error) return <p className="error">{error}</p>;
    return null;
  }
  const { customer, granted, certs, cafs, services, siiKey } = data;

  return (
    <>
      <p>
        <Link to="/customers">← Clientes</Link>
      </p>
      <h1>{customer.name}</h1>
      <p className="muted">
        Código <span className="code">{customer.key}</span> · RUT {customer.rut} ·{" "}
        <span className={`badge ${customer.environment === "PRODUCTION" ? "warn" : "neutral"}`}>
          {customer.environment}
        </span>
      </p>
      {msg && !grantedKey && <p style={{ color: "var(--ok)" }}>{msg}</p>}
      {actionError && <p className="error">{actionError}</p>}
      {grantedKey && (
        <div className="notice ok" ref={keyNotice}>
          Servicio habilitado. Copia la <strong>apiKey</strong> ahora — no se vuelve a mostrar:
          <div className="secret">
            <span className="code">{grantedKey}</span>
            <button
              className="secondary sm"
              type="button"
              onClick={() => navigator.clipboard?.writeText(grantedKey)}
            >
              <Icon name="copy" />
              Copiar
            </button>
            <button className="btn-link" type="button" onClick={() => setGrantedKey(null)}>
              <Icon name="x" />
              Ya la copié
            </button>
          </div>
        </div>
      )}

      {/* Servicios habilitados */}
      <div className="card">
        <div className="card-head">
          <h2>Servicios habilitados</h2>
          <span className="spacer" />
          {writable && (
            <button onClick={openGrant}>
              <Icon name="plus" />
              Habilitar servicio
            </button>
          )}
        </div>
        <table>
          <thead>
            <tr>
              <th>Servicio</th>
              <th>Código</th>
              {writable && <th />}
            </tr>
          </thead>
          <tbody>
            {granted.map((s) => (
              <tr key={s.service_code}>
                <td>{s.name}</td>
                <td className="muted">{s.service_code}</td>
                {writable && (
                  <td>
                    <button
                      className="btn-link danger"
                      type="button"
                      onClick={() => setConfirmRevoke(s)}
                    >
                      <Icon name="revoke" />
                      Revocar
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {granted.length === 0 && (
              <tr>
                <td colSpan={writable ? 3 : 2} className="muted">
                  Sin servicios habilitados.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Certificados */}
      <div className="card">
        <div className="card-head">
          <h2>Certificados</h2>
          <span className="spacer" />
          {writable && (
            <button onClick={openCert}>
              <Icon name="upload" />
              Subir certificado
            </button>
          )}
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Vence</th>
              <th>Cargado</th>
              <th>Estado</th>
            </tr>
          </thead>
          <tbody>
            {certs.map((c) => (
              <tr key={c.id}>
                <td>{c.id}</td>
                <td>{c.due_date}</td>
                <td>{c.created_at.slice(0, 10)}</td>
                <td>
                  <span className={`badge ${c.expired ? "error" : "ok"}`}>
                    {c.expired ? "vencido" : "vigente"}
                  </span>
                </td>
              </tr>
            ))}
            {certs.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  Sin certificados.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Clave tributaria SII (para BHE) */}
      <div className="card">
        <div className="card-head">
          <h2>Clave tributaria SII (BHE)</h2>
          <span className="spacer" />
          {writable && (
            <button onClick={openSii}>
              <Icon name="key" />
              {siiKey.configured ? "Actualizar clave" : "Configurar clave"}
            </button>
          )}
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Clave del portal del SII (login web) para consultar las Boletas de Honorarios recibidas.
        </p>
        <div className="actions">
          <span className={`badge ${siiKey.configured ? "ok" : "neutral"}`}>
            {siiKey.configured ? "configurada" : "no configurada"}
          </span>
          {writable && siiKey.configured && (
            <button
              className="btn-link danger"
              type="button"
              onClick={() => setConfirmSiiKey(true)}
            >
              <Icon name="trash" />
              Eliminar clave
            </button>
          )}
        </div>
      </div>

      {/* CAF / folios */}
      <div className="card">
        <div className="card-head">
          <h2>CAF / folios</h2>
          <span className="spacer" />
          {writable && (
            <button onClick={openCaf}>
              <Icon name="upload" />
              Subir CAF
            </button>
          )}
        </div>
        <table>
          <thead>
            <tr>
              <th>Tipo</th>
              <th>Desde</th>
              <th>Hasta</th>
              <th>Último usado</th>
              <th>Estado</th>
              {writable && <th />}
            </tr>
          </thead>
          <tbody>
            {cafs.map((c) => (
              <tr key={c.id}>
                <td>{c.doc_type}</td>
                <td>{c.folio_from}</td>
                <td>{c.folio_to}</td>
                <td>{c.last_folio || "—"}</td>
                <td>
                  <span className={`badge ${c.exhausted ? "warn" : "ok"}`}>
                    {c.exhausted ? "agotado" : "disponible"}
                  </span>
                </td>
                {writable && (
                  <td className="right">
                    {!c.exhausted && (
                      <button className="btn-link danger" onClick={() => setConfirmCaf(c)}>
                        Retirar
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {cafs.length === 0 && (
              <tr>
                <td colSpan={writable ? 6 : 5} className="muted">
                  Sin CAF cargados.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Consultas SII (operador) */}
      {writable && (
        <div className="card">
          <div className="card-head">
            <h2>Consultas SII (operador)</h2>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            Consulta directa al SII con las credenciales guardadas del cliente.
          </p>
          <div className="actions">
            <button className="secondary" onClick={openRcv}>
              <Icon name="search" />
              Consultar RCV
            </button>
            <button className="secondary" onClick={openBhe}>
              <Icon name="search" />
              Consultar BHE recibidas
            </button>
          </div>
        </div>
      )}

      {/* ---- Modales ---- */}
      {modal === "grant" && (
        <Modal
          title="Habilitar / rotar servicio"
          onClose={close}
          footer={
            <>
              <button className="secondary" type="button" onClick={close} disabled={busy}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="grant-form" disabled={busy}>
                <Icon name="check" />
                {busy ? "Guardando…" : "Guardar"}
              </button>
            </>
          }
        >
          <form id="grant-form" className="form-grid" onSubmit={grant}>
            {modalError && <p className="error">{modalError}</p>}
            <div className="field">
              <label>Servicio</label>
              <select value={grantSvc} onChange={(e) => setGrantSvc(e.target.value)} required>
                <option value="">—</option>
                {services.map((s) => (
                  <option key={s.code} value={s.code}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>apiKey (opcional — se genera si la dejas vacía)</label>
              <input
                value={grantKey}
                onChange={(e) => setGrantKey(e.target.value)}
                placeholder="(autogenerada)"
              />
            </div>
          </form>
        </Modal>
      )}

      {modal === "cert" && (
        <Modal
          title="Subir certificado (.pfx)"
          onClose={close}
          footer={
            <>
              <button className="secondary" type="button" onClick={close} disabled={busy}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="cert-form" disabled={!certFile || busy}>
                <Icon name="upload" />
                {busy ? "Subiendo…" : "Subir"}
              </button>
            </>
          }
        >
          <form id="cert-form" className="form-grid" onSubmit={uploadCert}>
            {modalError && <p className="error">{modalError}</p>}
            <div className="field">
              <label>Archivo .pfx / .p12</label>
              <input
                type="file"
                accept=".pfx,.p12"
                onChange={(e) => setCertFile(e.target.files?.[0] ?? null)}
              />
            </div>
            <div className="field">
              <label>Contraseña</label>
              <input
                type="password"
                value={certPass}
                onChange={(e) => setCertPass(e.target.value)}
              />
            </div>
            {busy && <p className="muted">Subiendo y validando el certificado…</p>}
          </form>
        </Modal>
      )}

      {confirmRevoke && (
        <ConfirmModal
          title="Revocar servicio"
          danger
          busy={busy}
          confirmLabel="Revocar"
          confirmIcon="revoke"
          onClose={() => setConfirmRevoke(null)}
          onConfirm={revoke}
          message={
            <>
              ¿Quitarle a este cliente <strong>{confirmRevoke.name}</strong>? Su sistema empezará a
              recibir <strong>401</strong> en ese servicio de inmediato, y para devolvérselo hay que
              habilitarlo de nuevo con una <strong>apiKey nueva</strong>: la actual no se recupera y
              habrá que reconfigurar el sistema del cliente.
            </>
          }
        />
      )}

      {confirmSiiKey && (
        <ConfirmModal
          title="Eliminar clave tributaria"
          danger
          busy={busy}
          confirmLabel="Eliminar"
          confirmIcon="trash"
          onClose={() => setConfirmSiiKey(false)}
          onConfirm={deleteSiiKey}
          message={
            <>
              ¿Borrar la clave del portal del SII de este cliente? Se dejarán de poder consultar sus
              boletas de honorarios recibidas. La clave no queda guardada en ninguna otra parte:
              para reponerla hay que volver a pedírsela a la empresa.
            </>
          }
        />
      )}

      {confirmCaf && (
        <ConfirmModal
          title="Retirar CAF"
          danger
          busy={busy}
          confirmLabel="Retirar"
          onClose={() => setConfirmCaf(null)}
          onConfirm={retireCaf}
          message={
            <>
              ¿Sacar de circulación el CAF del tipo <strong>{confirmCaf.doc_type}</strong> (folios{" "}
              {confirmCaf.folio_from}–{confirmCaf.folio_to})? Los documentos ya emitidos con él
              siguen siendo válidos; lo que se corta es que se emitan más. El siguiente folio saldrá
              del CAF que siga, y esto no se puede deshacer.
            </>
          }
        />
      )}

      {modal === "caf" && (
        <Modal
          title="Subir CAF"
          onClose={close}
          footer={
            <>
              <button className="secondary" type="button" onClick={close} disabled={busy}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="caf-form" disabled={!cafFile || busy}>
                <Icon name="upload" />
                {busy ? "Subiendo…" : "Subir"}
              </button>
            </>
          }
        >
          <form id="caf-form" className="form-grid" onSubmit={uploadCaf}>
            {modalError && <p className="error">{modalError}</p>}
            <div className="field">
              <label>Archivo CAF (.xml)</label>
              <input
                type="file"
                accept=".xml"
                onChange={(e) => setCafFile(e.target.files?.[0] ?? null)}
              />
            </div>
            {busy && <p className="muted">Subiendo el CAF…</p>}
          </form>
        </Modal>
      )}

      {modal === "sii" && (
        <Modal
          title={siiKey.configured ? "Actualizar clave tributaria" : "Configurar clave tributaria"}
          onClose={close}
          footer={
            <>
              <button className="secondary" type="button" onClick={close} disabled={busy}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="sii-form" disabled={!siiPass || busy}>
                <Icon name="check" />
                {busy ? "Guardando…" : "Guardar"}
              </button>
            </>
          }
        >
          <form id="sii-form" className="form-grid" onSubmit={saveSiiKey}>
            {modalError && <p className="error">{modalError}</p>}
            <p className="muted" style={{ margin: 0 }}>
              Clave con la que el RUT <strong>{customer.rut}</strong> entra al portal del SII. Se
              guarda cifrada y solo se usa para consultar las BHE recibidas.
            </p>
            <div className="field">
              <label>Clave tributaria</label>
              <input
                type="password"
                value={siiPass}
                onChange={(e) => setSiiPass(e.target.value)}
                autoFocus
                required
              />
            </div>
          </form>
        </Modal>
      )}

      {modal === "rcv" && (
        <Modal
          wide
          title="RCV — Registro de Compra y Venta"
          onClose={close}
          footer={
            <button className="secondary" type="button" onClick={close}>
              <Icon name="x" />
              Cerrar
            </button>
          }
        >
          <form id="rcv-form" className="row" onSubmit={queryRcv}>
            <div className="field">
              <label>Período</label>
              <input
                type="month"
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
                required
              />
            </div>
            <div className="field">
              <label>Operación</label>
              <select value={operation} onChange={(e) => setOperation(e.target.value)}>
                <option value="COMPRA">Compras</option>
                <option value="VENTA">Ventas</option>
              </select>
            </div>
            <button disabled={busy}>
              <Icon name="search" />
              {busy ? "Consultando…" : "Consultar"}
            </button>
          </form>
          {modalError && <p className="error">{modalError}</p>}
          {rcv && (
            <table style={{ marginTop: "1rem" }}>
              <thead>
                <tr>
                  <th>Tipo</th>
                  <th>Folio</th>
                  <th>RUT</th>
                  <th>Nombre</th>
                  <th>Fecha</th>
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {rcv.documents.map((d, i) => (
                  <tr key={i}>
                    <td>{d.doc_type}</td>
                    <td>{d.folio}</td>
                    <td>{d.counterpart_rut}</td>
                    <td>{d.counterpart_name}</td>
                    <td>{d.date}</td>
                    <td>{money(d.total_amount)}</td>
                  </tr>
                ))}
                {rcv.count === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      Sin documentos en el período.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </Modal>
      )}

      {modal === "bhe" && (
        <Modal
          wide
          title="BHE — Boletas de Honorarios recibidas"
          onClose={close}
          footer={
            <button className="secondary" type="button" onClick={close}>
              <Icon name="x" />
              Cerrar
            </button>
          }
        >
          <form id="bhe-form" className="row" onSubmit={queryBhe}>
            <div className="field">
              <label>Período</label>
              <input
                type="month"
                value={bhePeriod}
                onChange={(e) => setBhePeriod(e.target.value)}
                required
              />
            </div>
            <button disabled={busy}>
              <Icon name="search" />
              {busy ? "Consultando…" : "Consultar"}
            </button>
          </form>
          {modalError && <p className="error">{modalError}</p>}
          {bhe && (
            <table style={{ marginTop: "1rem" }}>
              <thead>
                <tr>
                  <th>Folio</th>
                  <th>Emisor</th>
                  <th>Fecha</th>
                  <th>Bruto</th>
                  <th>Retención</th>
                  <th>Líquido</th>
                  <th>Estado</th>
                </tr>
              </thead>
              <tbody>
                {bhe.documents.map((d, i) => (
                  <tr key={i}>
                    <td>{d.folio}</td>
                    <td>
                      {d.issuer_name}
                      <br />
                      <span className="muted">{d.issuer_rut}</span>
                    </td>
                    <td>{d.issue_date ?? "—"}</td>
                    <td>{money(d.gross_amount)}</td>
                    <td>{money(d.retention_amount)}</td>
                    <td>{money(d.net_amount)}</td>
                    <td>
                      <span className={`badge ${d.status === "anulada" ? "error" : "ok"}`}>
                        {d.status}
                      </span>
                    </td>
                  </tr>
                ))}
                {bhe.count === 0 && (
                  <tr>
                    <td colSpan={7} className="muted">
                      Sin boletas en el período.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </Modal>
      )}
    </>
  );
}
