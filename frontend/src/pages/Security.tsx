import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import Icon from "../components/Icon";
import { useApi } from "../hooks/useApi";

/** Segundo factor de la cuenta propia.
 *
 * Quien entra aquí administra los certificados de firma de todas las empresas
 * del servicio, así que la contraseña sola es poca cosa. El alta es en dos
 * pasos a propósito: hasta confirmar un código no se activa nada, porque si se
 * activara al generar el secreto, cerrar la pestaña a medias dejaría al usuario
 * fuera de su propio portal.
 */
export default function Security() {
  const { user, refresh } = useAuth();
  const { data: status, loading, reload } = useApi(() => api.totpStatus(), []);

  const [setup, setSetup] = useState<{ otpauth_uri: string; secret: string } | null>(null);
  const [codigo, setCodigo] = useState("");
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function empezar() {
    setError("");
    setBusy(true);
    try {
      setSetup(await api.totpSetup());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function activar(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const res = await api.totpActivate(codigo, password);
      setRecovery(res.recovery_codes);
      setSetup(null);
      setCodigo("");
      setPassword("");
      await Promise.all([reload(), refresh()]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function apagar(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api.totpDisable(password);
      setPassword("");
      setRecovery(null);
      await Promise.all([reload(), refresh()]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">Cargando…</p>;

  return (
    <>
      {error && <p className="error">{error}</p>}

      {recovery && (
        <div className="notice ok">
          <strong>Guarda estos códigos de recuperación ahora.</strong> Son la única forma de entrar
          si pierdes el teléfono, cada uno sirve una vez y no se vuelven a mostrar.
          <div className="secret" style={{ flexWrap: "wrap" }}>
            {recovery.map((c) => (
              <span className="code" key={c}>
                {c}
              </span>
            ))}
            <button
              className="secondary sm"
              type="button"
              onClick={() => navigator.clipboard?.writeText(recovery.join("\n"))}
            >
              <Icon name="copy" />
              Copiar todos
            </button>
            <button className="btn-link" type="button" onClick={() => setRecovery(null)}>
              <Icon name="x" />
              Ya los guardé
            </button>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Verificación en dos pasos</h2>
          <span className="spacer" />
          <span className={`badge ${status?.enabled ? "ok" : "neutral"}`}>
            {status?.enabled ? "activa" : "no configurada"}
          </span>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Cuenta <strong>{user?.email}</strong>. Con la verificación activa, entrar al portal pide
          además un código de seis dígitos de tu app de autenticación.
        </p>

        {status?.enabled ? (
          <>
            <p>
              Te quedan <strong>{status.recovery_codes_left}</strong> códigos de recuperación sin
              usar.
              {status.recovery_codes_left === 0 && (
                <>
                  {" "}
                  Sin códigos, perder el teléfono obliga a pedirle a un superadmin que la apague.
                </>
              )}
            </p>
            <form className="form-grid" onSubmit={apagar}>
              <div className="field">
                <label>Para desactivarla, confirma tu contraseña</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <button className="danger" disabled={busy || !password}>
                <Icon name="x" />
                Desactivar
              </button>
            </form>
          </>
        ) : setup ? (
          <>
            <ol className="steps-list">
              <li>
                Abre tu app de autenticación y añade una cuenta nueva pegando este enlace, o
                escribiendo la clave a mano:
                <div className="secret" style={{ marginTop: "0.5rem" }}>
                  <span className="code">{setup.secret}</span>
                  <button
                    className="secondary sm"
                    type="button"
                    onClick={() => navigator.clipboard?.writeText(setup.otpauth_uri)}
                  >
                    <Icon name="copy" />
                    Copiar enlace
                  </button>
                </div>
              </li>
              <li>Escribe el código de seis dígitos que aparezca y confirma con tu contraseña:</li>
            </ol>
            <form className="form-grid" onSubmit={activar}>
              <div className="field">
                <label>Código de verificación</label>
                <input
                  value={codigo}
                  onChange={(e) => setCodigo(e.target.value)}
                  placeholder="6 dígitos"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  autoFocus
                />
              </div>
              <div className="field">
                {/* Se pide igual que para desactivar: con una sesión robada no
                    debe poder fijarse un segundo factor ajeno. */}
                <label>Tu contraseña</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <div className="actions">
                <button disabled={busy || codigo.length < 6 || !password}>
                  <Icon name="check" />
                  Activar
                </button>
                <button className="secondary" type="button" onClick={() => setSetup(null)}>
                  <Icon name="x" />
                  Cancelar
                </button>
              </div>
            </form>
          </>
        ) : (
          <button onClick={empezar} disabled={busy}>
            <Icon name="key" />
            Activar la verificación en dos pasos
          </button>
        )}
      </div>
    </>
  );
}
