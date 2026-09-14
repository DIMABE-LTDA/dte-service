import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import Icon from "../components/Icon";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // El API responde 'totp_required' cuando la contraseña es correcta pero falta
  // el segundo factor: recién ahí se pide el código, para no mostrar un campo
  // que la mayoría de los usuarios no usa.
  const [pideCodigo, setPideCodigo] = useState(false);
  const [conRecuperacion, setConRecuperacion] = useState(false);
  const [codigo, setCodigo] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const second = pideCodigo
        ? conRecuperacion
          ? { recovery_code: codigo }
          : { totp_code: codigo }
        : undefined;
      await login(email, password, second);
      nav("/");
    } catch (err) {
      const msg = (err as Error).message;
      if (msg === "totp_required") {
        setPideCodigo(true);
        setError("");
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card login-box" onSubmit={submit}>
        <div className="login-brand">
          <span className="logo">D</span>
          DTE Service
        </div>
        <div className="field">
          <label>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label>Contraseña</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {pideCodigo && (
          <div className="field">
            <label>{conRecuperacion ? "Código de recuperación" : "Código de verificación"}</label>
            <input
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              placeholder={conRecuperacion ? "abcd-ef01-2345" : "6 dígitos"}
              inputMode={conRecuperacion ? "text" : "numeric"}
              autoComplete="one-time-code"
              autoFocus
            />
            <button
              className="btn-link"
              type="button"
              style={{ marginTop: "0.4rem", padding: 0 }}
              onClick={() => {
                setConRecuperacion(!conRecuperacion);
                setCodigo("");
                setError("");
              }}
            >
              {conRecuperacion
                ? "Tengo el teléfono a mano"
                : "Perdí el teléfono: usar un código de recuperación"}
            </button>
          </div>
        )}
        {error && <p className="error">{error}</p>}
        <button disabled={busy} style={{ marginTop: "0.8rem", width: "100%" }}>
          <Icon name="login" />
          {busy ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
