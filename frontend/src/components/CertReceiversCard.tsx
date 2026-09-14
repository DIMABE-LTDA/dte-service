/**
 * Receptores de prueba: clientes reales del contribuyente para el set.
 *
 * El instructivo del SII pide «un Rut receptor de un cliente existente» y «RUT
 * distintos para las distintas facturas». Las definiciones del set traían al
 * propio SII como receptor de todo. El sistema asigna estos en orden a cada
 * factura y guía de venta; las notas heredan el receptor de su documento.
 */
import { useEffect, useState } from "react";
import { api, type ApiError } from "../api";
import { useApi } from "../hooks/useApi";
import { useToast } from "../toast";
import type { CertReceiver } from "../types";
import Icon from "./Icon";

interface Props {
  cid: number;
  writable: boolean;
  onSaved: () => void;
}

const VACIO: CertReceiver = {
  rut: "",
  business_name: "",
  activity: "",
  address: "",
  commune: "",
  city: "",
};

const COLUMNAS: { key: keyof CertReceiver; label: string; max: number }[] = [
  { key: "rut", label: "RUT", max: 12 },
  { key: "business_name", label: "Razón social", max: 100 },
  { key: "activity", label: "Giro", max: 40 },
  { key: "address", label: "Dirección", max: 70 },
  { key: "commune", label: "Comuna", max: 20 },
];

export default function CertReceiversCard({ cid, writable, onSaved }: Props) {
  const toast = useToast();
  const { data, loading, reload } = useApi(() => api.certReceivers(cid), [cid]);
  const [filas, setFilas] = useState<CertReceiver[]>([]);
  const [editando, setEditando] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data && !editando) setFilas(data);
  }, [data, editando]);

  function cambiar(i: number, key: keyof CertReceiver, valor: string) {
    setFilas(filas.map((f, j) => (j === i ? { ...f, [key]: valor } : f)));
  }

  async function guardar() {
    setError("");
    setBusy(true);
    try {
      const limpias = filas.filter((f) => f.rut.trim());
      await api.certSaveReceivers(cid, limpias);
      toast.ok(`${limpias.length} receptor(es) de prueba guardados.`);
      setEditando(false);
      await reload();
      onSaved();
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return null;

  return (
    <div className="card">
      <div className="card-head">
        <h2>Receptores de prueba</h2>
        <span className="spacer" />
        {writable && !editando && (
          <button className="secondary" type="button" onClick={() => setEditando(true)}>
            <Icon name="edit" />
            Editar
          </button>
        )}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Clientes reales de la empresa. El SII pide «un Rut receptor de un cliente existente» y «RUT
        distintos para las distintas facturas». Se asignan en orden a cada factura y guía de venta;
        las notas heredan el receptor del documento que modifican. La verificación dice cuántos
        hacen falta.
      </p>
      {error && <p className="error">{error}</p>}

      <div className="tabla-scroll">
        <table>
          <thead>
            <tr>
              {COLUMNAS.map((c) => (
                <th key={c.key}>{c.label}</th>
              ))}
              {editando && <th />}
            </tr>
          </thead>
          <tbody>
            {filas.map((f, i) => (
              <tr key={i}>
                {COLUMNAS.map((c) => (
                  <td key={c.key}>
                    {editando ? (
                      <input
                        aria-label={`${c.label} del receptor ${i + 1}`}
                        maxLength={c.max}
                        value={f[c.key] ?? ""}
                        onChange={(e) => cambiar(i, c.key, e.target.value)}
                      />
                    ) : c.key === "rut" ? (
                      <span className="code">{f.rut}</span>
                    ) : (
                      f[c.key]
                    )}
                  </td>
                ))}
                {editando && (
                  <td className="right">
                    <button
                      className="btn-link danger"
                      type="button"
                      onClick={() => setFilas(filas.filter((_, j) => j !== i))}
                    >
                      <Icon name="trash" />
                      Quitar
                    </button>
                  </td>
                )}
              </tr>
            ))}
            {filas.length === 0 && (
              <tr>
                <td colSpan={COLUMNAS.length + 1} className="muted">
                  Sin receptores: las facturas irían al RUT del propio SII.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {editando && (
        <div className="actions" style={{ marginTop: "0.8rem" }}>
          <button
            className="secondary"
            type="button"
            onClick={() => setFilas([...filas, { ...VACIO }])}
          >
            <Icon name="plus" />
            Agregar receptor
          </button>
          <span className="spacer" />
          <button
            className="secondary"
            type="button"
            onClick={() => {
              setEditando(false);
              setError("");
            }}
          >
            <Icon name="x" />
            Cancelar
          </button>
          <button type="button" disabled={busy} onClick={() => void guardar()}>
            <Icon name="check" />
            {busy ? "Guardando…" : "Guardar"}
          </button>
        </div>
      )}
    </div>
  );
}
