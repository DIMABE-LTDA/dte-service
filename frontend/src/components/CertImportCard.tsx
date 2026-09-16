/**
 * Cargar de una vez los sets de un contribuyente, desde lo que entrega el SII.
 *
 * El SII entrega a cada RUT un texto con sus casos —el set de pruebas— y otro
 * con el set de boletas. Subirlos aquí es todo lo que hace falta: el servidor
 * los lee, muestra qué va a cargar y recién entonces lo guarda. Transcribirlos a
 * mano fue la fuente de casi todos los rechazos de la primera certificación.
 *
 * Sigue aceptando el JSON de definiciones, para clonar un expediente armado.
 *
 * El emisor, las fechas, la referencia al caso y los receptores los pone el
 * sistema al emitir.
 */
import { useRef, useState } from "react";
import { api, type ApiError } from "../api";
import { useToast } from "../toast";
import type { CertSheet, CertSheetFile } from "../types";
import Icon from "./Icon";

interface Props {
  cid: number;
  writable: boolean;
  onImported: () => Promise<void>;
}

/** Lo que se acepta en JSON: { kind: { code, endpoint, payload } }. */
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

function aBase64(archivo: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const lector = new FileReader();
    lector.onload = () => resolve((lector.result as string).split(",")[1] ?? "");
    lector.onerror = reject;
    lector.readAsDataURL(archivo);
  });
}

type Previo =
  | { tipo: "json"; sets: Record<string, unknown>; resumen: string }
  | { tipo: "sii"; archivos: CertSheetFile[]; hoja: CertSheet };

export default function CertImportCard({ cid, writable, onImported }: Props) {
  const toast = useToast();
  const entrada = useRef<HTMLInputElement>(null);
  const [previo, setPrevio] = useState<Previo | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function elegir(lista: FileList | null) {
    setError("");
    setPrevio(null);
    const archivos = Array.from(lista ?? []);
    if (!archivos.length) return;
    const json = archivos.filter((a) => a.name.toLowerCase().endsWith(".json"));
    if (json.length && json.length !== archivos.length) {
      setError("Sube el JSON de definiciones o los archivos del SII, no los dos a la vez.");
      return;
    }
    if (json.length) {
      try {
        setPrevio({ tipo: "json", ...revisar(await json[0].text()) });
      } catch (err) {
        setError(`${json[0].name}: ${(err as Error).message}`);
      }
      return;
    }
    setBusy(true);
    try {
      const subidos = await Promise.all(
        archivos.map(async (a) => ({ name: a.name, content_base64: await aBase64(a) })),
      );
      const hoja = await api.certSheet(cid, subidos, true);
      setPrevio({ tipo: "sii", archivos: subidos, hoja });
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  async function importar() {
    if (!previo) return;
    setBusy(true);
    try {
      if (previo.tipo === "json") {
        await api.certImport(cid, previo.sets);
      } else {
        await api.certSheet(cid, previo.archivos, false);
      }
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
        Sube los archivos que entregó el SII: el set de pruebas y, si lo tienes, el de boletas. Se
        leen y se muestra qué se va a cargar antes de guardar. Los sets que ya existan se
        reemplazan. También acepta un JSON de definiciones.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <input
          ref={entrada}
          type="file"
          multiple
          accept=".txt,text/plain,application/json,.json"
          aria-label="Archivos del SII o de definiciones"
          disabled={busy}
          onChange={(e) => void elegir(e.target.files)}
        />
        {previo && (
          <button type="button" disabled={busy} onClick={() => void importar()}>
            <Icon name="upload" />
            {busy ? "Cargando…" : "Cargar"}
          </button>
        )}
      </div>
      {previo?.tipo === "json" && <p className="muted">{previo.resumen}</p>}
      {previo?.tipo === "sii" && (
        <>
          <p className="muted">
            {previo.hoja.sets.length} set(s):{" "}
            {previo.hoja.sets
              .map((s) => `${s.kind}${s.code ? ` (${s.code})` : ""} · ${s.items}`)
              .join(", ")}
          </p>
          {previo.hoja.notes.length > 0 && (
            <details>
              <summary>Indicaciones del SII que conviene leer ({previo.hoja.notes.length})</summary>
              <ul>
                {previo.hoja.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
