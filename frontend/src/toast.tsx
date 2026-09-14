/**
 * Avisos globales, visibles desde cualquier pantalla.
 *
 * Antes cada página pintaba su resultado como un párrafo al principio del
 * contenido. En una lista corta se veía; en la ficha de cliente o en el
 * expediente de certificación —donde se actúa sobre tarjetas que están muy por
 * debajo del primer scroll— el mensaje aparecía fuera de la vista de quien
 * acababa de pulsar el botón, y la acción parecía no haber hecho nada.
 *
 * Dos decisiones que no son de estética:
 *
 * - **El éxito se va solo; el error no.** Un aviso de error que desaparece
 *   reproduce exactamente el problema que este componente viene a resolver: el
 *   operador mira otra cosa tres segundos y se queda sin saber qué falló. Los
 *   errores se cierran a mano.
 * - **El error puede traer una guía.** El API devuelve `details` con qué
 *   revisar y en qué orden; caben aquí porque es justo el momento en que se
 *   necesitan.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import Icon from "./components/Icon";

export type ToastKind = "ok" | "error";

interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
  /** Qué revisar, cuando el API lo dice. Sólo en errores. */
  hints: string[];
}

interface ToastApi {
  ok: (text: string) => void;
  error: (text: string, hints?: string[]) => void;
  dismiss: (id: number) => void;
}

const Ctx = createContext<ToastApi | null>(null);

/** Cuánto vive un aviso de éxito. Los errores no caducan. */
const VIDA_OK_MS = 5000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [avisos, setAvisos] = useState<Toast[]>([]);
  const siguiente = useRef(1);

  const dismiss = useCallback((id: number) => {
    setAvisos((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastKind, text: string, hints: string[] = []) => {
      const id = siguiente.current++;
      setAvisos((prev) => [...prev, { id, kind, text, hints }]);
      if (kind === "ok") window.setTimeout(() => dismiss(id), VIDA_OK_MS);
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      ok: (text) => push("ok", text),
      error: (text, hints) => push("error", text, hints),
      dismiss,
    }),
    [push, dismiss],
  );

  return (
    <Ctx.Provider value={api}>
      {children}
      {/* aria-live en el contenedor y no en cada aviso: si el elemento con la
          región viva se monta junto al texto, los lectores de pantalla no
          siempre lo anuncian. */}
      <div className="avisos" aria-live="polite" aria-atomic="false">
        {avisos.map((a) => (
          <div
            className={`aviso ${a.kind}`}
            key={a.id}
            role={a.kind === "error" ? "alert" : "status"}
          >
            <Icon name={a.kind === "ok" ? "check" : "x"} />
            <div className="aviso-cuerpo">
              <span>{a.text}</span>
              {a.hints.length > 0 && (
                <ul className="aviso-guia">
                  {a.hints.map((h) => (
                    <li key={h}>{h}</li>
                  ))}
                </ul>
              )}
            </div>
            <button
              className="close"
              type="button"
              aria-label="Cerrar aviso"
              onClick={() => dismiss(a.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToast necesita estar dentro de <ToastProvider>");
  return ctx;
}
