/**
 * Crear los diez sets de una certificación nueva, desde la plantilla del SII.
 *
 * Antes, empezar un expediente exigía clonar el de otro contribuyente o pegar
 * a mano el JSON de cada set: quien no supiera de memoria que el set básico son
 * cuatro facturas, tres notas de crédito y una de débito —y a qué documento
 * apunta cada una— no podía empezar.
 *
 * La estructura la pone el sistema. Aquí sólo se piden los números de atención,
 * que son lo único que el SII asigna distinto a cada empresa. Los montos quedan
 * de relleno y se ajustan después, set por set, con el PDF del SII delante.
 */
import { useEffect, useState } from "react";
import { api, type ApiError } from "../api";
import { useApi } from "../hooks/useApi";
import { useToast } from "../toast";
import Icon from "./Icon";
import Modal from "./Modal";

interface Props {
  cid: number;
  writable: boolean;
  /** Si el expediente ya tiene sets: crear de nuevo pisa las definiciones. */
  hasSets: boolean;
  onCreated: () => Promise<void> | void;
}

export default function CertTemplateCard({ cid, writable, hasSets, onCreated }: Props) {
  const toast = useToast();
  const { data } = useApi(() => api.certTemplate(cid), [cid]);
  const [abierto, setAbierto] = useState(false);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Al abrir se parte en blanco: los números son del PDF que el operador tiene
  // delante, y arrastrar los de una sesión anterior sería peor que no poner nada.
  useEffect(() => {
    if (abierto) setCodes({});
  }, [abierto]);

  if (!writable) return null;

  const puestos = Object.values(codes).filter((c) => c.trim()).length;

  async function crear() {
    setError("");
    setBusy(true);
    try {
      await api.certCreateFromTemplate(cid, codes);
      toast.ok(`${puestos} set(s) creados. Ajusta los ítems de cada uno con tu set de pruebas.`);
      setAbierto(false);
      await onCreated();
    } catch (err) {
      const e = err as ApiError;
      setError(e.message);
      toast.error(e.message, e.hints);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h2>Empezar desde la plantilla del SII</h2>
        <span className="spacer" />
        <button type="button" onClick={() => setAbierto(true)}>
          <Icon name="plus" />
          Crear los sets
        </button>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Da de alta los sets con la estructura que define el SII —qué documentos lleva cada uno, en
        qué orden y qué referencia a qué—. Tú sólo pones los números de atención.
      </p>

      {abierto && (
        <Modal
          wide
          title="Crear los sets desde la plantilla"
          onClose={() => !busy && setAbierto(false)}
          footer={
            <>
              <span className="muted">
                {puestos === 0
                  ? "Indica al menos un número de atención."
                  : `${puestos} set(s) se van a crear.`}
              </span>
              <span className="spacer" />
              <button
                className="secondary"
                type="button"
                disabled={busy}
                onClick={() => setAbierto(false)}
              >
                <Icon name="x" />
                Cancelar
              </button>
              <button type="button" disabled={busy || puestos === 0} onClick={() => void crear()}>
                <Icon name="check" />
                {busy ? "Creando…" : "Crear los sets"}
              </button>
            </>
          }
        >
          {error && <p className="error">{error}</p>}
          {hasSets && (
            <p className="warn">
              Este expediente ya tiene sets. Los que coincidan en número de atención se reemplazan
              por la plantilla, y se pierde lo que hayas ajustado en ellos.
            </p>
          )}
          <p className="muted">
            Copia cada número del set de pruebas que el SII le entregó a esta empresa. Deja en
            blanco los que aún no te hayan asignado.
          </p>
          <table>
            <thead>
              <tr>
                <th>Set</th>
                <th>N° de atención</th>
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((s) => (
                <tr key={s.kind}>
                  <td>
                    <strong>{s.label}</strong>
                    <br />
                    <span className="muted">{s.help}</span>
                  </td>
                  <td>
                    <input
                      value={codes[s.kind] ?? ""}
                      aria-label={s.label}
                      inputMode="numeric"
                      placeholder="5038170"
                      style={{ maxWidth: "9rem" }}
                      onChange={(e) => setCodes((prev) => ({ ...prev, [s.kind]: e.target.value }))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Modal>
      )}
    </div>
  );
}
