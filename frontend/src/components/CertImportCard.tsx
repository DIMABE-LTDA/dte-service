/**
 * Cargar de una vez los diez sets de un contribuyente, desde un archivo.
 *
 * El SII entrega a cada RUT sus propios casos y su propio número de atención.
 * Hasta ahora eso se cargaba con una llamada al API a mano: en un expediente
 * nuevo no hay de dónde clonar, y definir diez sets pegando JSON uno por uno no
 * es trabajo para el portal.
 *
 * El archivo trae sólo los datos del caso. El emisor, las fechas, la referencia
 * al caso y los receptores los pone el sistema al emitir.
 */
import { useRef, useState } from "react";
import { api, type ApiError } from "../api";
import { useToast } from "../toast";
import Icon from "./Icon";

interface Props {
  cid: number;
  writable: boolean;
  onImported: () => Promise<void>;
}

/** Lo que se acepta: { kind: { code, endpoint, payload } }. */
function revisar(texto: string): { sets: Record<string, unknown>; resumen: string } {
  const datos = JSON.parse(texto) as Record<string, { code?: string; endpoint?: string }>;
  if (!datos || typeof datos !== "object" || Array.isArray(datos)) {
    throw new Error("El archivo debe ser un objeto con un set por clave.");
  }
  const conCodigo = Object.entries(datos).filter(([, v]) => v && v.code);
  if (!conCodigo.length) {
    throw new Error("Ningún set trae número de atención (`code`): sin él no se puede identificar.");
  }
  const sinEndpoint = conCodigo.filter(([, v]) => !v.endpoint).map(([k]) => k);
  if (sinEndpoint.length) {
    throw new Error(`Sin endpoint de emisión: ${sinEndpoint.join(", ")}.`);
  }
  const omitidos = Object.keys(datos).length - conCodigo.length;
  return {
    sets: Object.fromEntries(conCodigo),
    resumen:
      `${conCodigo.length} set(s): ${conCodigo.map(([k, v]) => `${k} (${v.code})`).join(", ")}` +
      (omitidos ? ` · ${omitidos} omitido(s) por no traer número de atención` : ""),
  };
}

export default function CertImportCard({ cid, writable, onImported }: Props) {
  const toast = useToast();
  const entrada = useRef<HTMLInputElement>(null);
  const [previo, setPrevio] = useState<{ sets: Record<string, unknown>; resumen: string } | null>(
    null,
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function elegir(archivo: File | undefined) {
    setError("");
    setPrevio(null);
    if (!archivo) return;
    try {
      setPrevio(revisar(await archivo.text()));
    } catch (err) {
      setError(`${archivo.name}: ${(err as Error).message}`);
    }
  }

  async function importar() {
    if (!previo) return;
    setBusy(true);
    try {
      await api.certImport(cid, previo.sets);
      toast.ok("Sets cargados con su definición.");
      setPrevio(null);
      if (entrada.current) entrada.current.value = "";
      await onImported();
    } catch (err) {
      const e = err as ApiError;
      setError(e.message);
      toast.error(e.message, e.hints);
    } finally {
      setBusy(false);
    }
  }

  if (!writable) return null;

  return (
    <div className="card">
      <div className="card-head">
        <h2>Cargar los sets del contribuyente</h2>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Un archivo con los casos que el SII asignó a este RUT: da de alta cada set con su número de
        atención y guarda qué emite. Los sets que ya existan se reemplazan.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <input
          ref={entrada}
          type="file"
          accept="application/json,.json"
          aria-label="Archivo de definiciones"
          onChange={(e) => void elegir(e.target.files?.[0])}
        />
        {previo && (
          <button type="button" disabled={busy} onClick={() => void importar()}>
            <Icon name="upload" />
            {busy ? "Cargando…" : "Cargar"}
          </button>
        )}
      </div>
      {previo && <p className="muted">{previo.resumen}</p>}
    </div>
  );
}
