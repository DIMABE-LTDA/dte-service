/**
 * Verificación de configuración antes de emitir un set de certificación.
 *
 * La primera certificación que pasó por la plataforma perdió una semana en
 * cosas que ahora se ven aquí antes del primer envío: un certificado
 * autofirmado, CAF sin la firma del SII, folios fuera de rango y un timbre que
 * el SII no podía validar. Todo eso se descubrió a mano; con otro cliente no va
 * a haber nadie leyendo XML.
 *
 * Los grupos que están bien se muestran cerrados en una línea, y los que tienen
 * algo abierto: lo que hay que mirar primero es lo que falla, no la lista
 * entera.
 */
import { useState } from "react";
import { api, type ApiError } from "../api";
import { useToast } from "../toast";
import type { CertCheck, CertReadiness } from "../types";
import Icon from "./Icon";

interface Props {
  cid: number;
  data: CertReadiness | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  writable: boolean;
}

const ESTADO: Record<CertCheck["state"], string> = {
  ok: "bien",
  atencion: "revisar",
  error: "bloquea",
};

export default function CertReadinessPanel({ cid, data, loading, error, reload, writable }: Props) {
  const toast = useToast();
  const [sii, setSii] = useState<CertCheck | null>(null);
  const [probando, setProbando] = useState(false);

  async function probarSii() {
    setProbando(true);
    try {
      const r = await api.certCheckSii(cid);
      setSii(r);
      if (r.state === "ok") toast.ok(r.detail);
      else toast.error(r.detail, r.fix ? [r.fix] : []);
    } catch (err) {
      const e = err as ApiError;
      toast.error(e.message, e.hints);
    } finally {
      setProbando(false);
    }
  }

  if (loading && !data) return <div className="card muted">Verificando configuración…</div>;
  if (error) return <div className="card error">No se pudo verificar: {error}</div>;
  if (!data) return null;

  return (
    <div className={`card verificacion ${data.ready ? "lista" : "bloqueada"}`}>
      <div className="card-head">
        <h2>Verificación antes de emitir</h2>
        <span className="spacer" />
        <button className="secondary sm" type="button" onClick={() => void reload()}>
          <Icon name="restore" />
          Volver a verificar
        </button>
        {writable && (
          <button
            className="secondary sm"
            type="button"
            disabled={probando}
            onClick={() => void probarSii()}
            title="Pide un token a Maullín con el certificado del cliente. Es la única prueba que sale a la red."
          >
            <Icon name="search" />
            {probando ? "Probando…" : "Probar conexión con el SII"}
          </button>
        )}
      </div>

      <p className="verificacion-veredicto">
        {data.ready ? (
          <>
            <strong>Listo para emitir.</strong>{" "}
            {data.warnings > 0
              ? `${data.warnings} aviso(s) que conviene leer antes de gastar folios.`
              : "Todo en orden."}
          </>
        ) : (
          <>
            <strong>{data.errors} problema(s) impiden emitir con garantías.</strong> Emitir ahora
            gastaría folios en documentos que el SII rechazaría.
          </>
        )}
      </p>
      <p className="muted" style={{ marginTop: 0 }}>
        Comprobado sin gastar folios ni hablar con el SII. Incluye una firma de prueba del timbre
        con cada CAF, que es lo que el SII verifica en cada documento.
      </p>

      {sii && (
        <div className={`etapa ${sii.state} verificacion-sii`}>
          <div className="etapa-titulo">
            <span className="etapa-punto" />
            {sii.label}
          </div>
          <div className="etapa-detalle">
            {sii.detail}
            {sii.fix && <div>{sii.fix}</div>}
          </div>
        </div>
      )}

      {data.groups.map((g) => (
        // <details> y no un acordeón propio: es nativo, se opera con teclado
        // y no hace falta estado para saber cuál está abierto.
        <details key={g.key} className={`grupo ${g.state}`} open={g.state !== "ok"}>
          <summary>
            <span className={`etapa-punto ${g.state}`} />
            <span className="grupo-nombre">{g.label}</span>
            <span className="muted">
              {g.checks.length} comprobación(es)
              {g.state !== "ok" &&
                ` · ${g.checks.filter((c) => c.state !== "ok").length} para revisar`}
            </span>
          </summary>
          <ul className="comprobaciones">
            {g.checks.map((c) => (
              <li key={c.key} className={c.state}>
                <span className={`badge ${c.state === "atencion" ? "warn" : c.state}`}>
                  {ESTADO[c.state]}
                </span>
                <div>
                  <strong>{c.label}</strong>
                  <span className="muted"> — {c.detail}</span>
                  {c.fix && c.state !== "ok" && <div className="arreglo">→ {c.fix}</div>}
                </div>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  );
}
