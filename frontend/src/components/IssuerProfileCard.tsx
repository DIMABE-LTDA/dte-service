/**
 * Datos del emisor: lo que va en el encabezado de cada documento.
 *
 * Antes iban escritos a mano en cada definición de set, repetidos siete
 * veces, y clonar las definiciones a otro cliente emitía con el emisor del
 * anterior. Ahora viven aquí, en la ficha, y el sistema los pone al emitir.
 * También se pueden sincronizar desde Odoo.
 */
import { useState, type FormEvent } from "react";
import { api, type ApiError } from "../api";
import { useToast } from "../toast";
import type { Customer, IssuerProfile } from "../types";
import Icon from "./Icon";
import Modal from "./Modal";

interface Props {
  customer: Customer;
  writable: boolean;
  onSaved: () => Promise<void>;
}

/** Campo del perfil, cómo se llama para quien lo llena, su largo SII y un ejemplo. */
const CAMPOS: {
  key: keyof IssuerProfile;
  label: string;
  max?: number;
  numero?: boolean;
  ayuda?: string;
  obligatorio?: boolean;
}[] = [
  { key: "legal_name", label: "Razón social", max: 100, obligatorio: true },
  {
    key: "activity",
    label: "Giro",
    max: 80,
    obligatorio: true,
    ayuda: "Tal como aparece en tu inscripción en el SII.",
  },
  {
    key: "economic_activity",
    label: "Código ACTECO",
    numero: true,
    obligatorio: true,
    ayuda: "Actividad económica principal (6 dígitos).",
  },
  { key: "address", label: "Dirección", max: 70, obligatorio: true },
  { key: "commune", label: "Comuna", max: 20, obligatorio: true },
  { key: "city", label: "Ciudad", max: 20 },
  {
    key: "branch_name",
    label: "Sucursal",
    max: 20,
    ayuda: "Sólo si emites desde una sucursal distinta a la casa matriz.",
  },
  {
    key: "branch_code",
    label: "Código SII de la sucursal",
    numero: true,
    ayuda: "Lo ves en Mi SII → Direcciones.",
  },
  {
    key: "sii_office",
    label: "Unidad del SII",
    max: 40,
    ayuda:
      "Dirección Regional de tu domicilio (p. ej. RANCAGUA). Va impresa bajo el recuadro del documento.",
  },
];

export default function IssuerProfileCard({ customer, writable, onSaved }: Props) {
  const toast = useToast();
  const [abierto, setAbierto] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // `??`: un API anterior no manda estos campos, y la ficha entera no debe
  // caerse por eso.
  const perfil = (customer.issuer ?? {}) as IssuerProfile;
  const faltan = customer.issuer_missing ?? [];

  function abrir() {
    setForm(
      Object.fromEntries(
        CAMPOS.map((c) => [c.key, perfil[c.key] == null ? "" : String(perfil[c.key])]),
      ),
    );
    setError("");
    setAbierto(true);
  }

  async function guardar(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const issuer = Object.fromEntries(
        CAMPOS.map((c) => {
          const v = (form[c.key] ?? "").trim();
          return [c.key, v === "" ? null : c.numero ? Number(v) : v];
        }),
      ) as unknown as IssuerProfile;
      await api.updateCustomer(customer.id, { issuer });
      toast.ok("Datos del emisor guardados.");
      setAbierto(false);
      await onSaved();
    } catch (err) {
      const ex = err as ApiError;
      setError(ex.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`card ${faltan.length ? "incompleta" : ""}`}>
      <div className="card-head">
        <h2>Datos del emisor</h2>
        <span className="spacer" />
        {writable && (
          <button className="secondary" onClick={abrir}>
            <Icon name="edit" />
            {faltan.length ? "Completar" : "Editar"}
          </button>
        )}
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Van en el encabezado de cada documento. El sistema los usa al emitir los sets de
        certificación; también se pueden sincronizar desde Odoo.
      </p>
      {faltan.length > 0 && (
        <div className="notice error">
          <strong>Faltan: {faltan.join(", ")}.</strong> Sin ellos no se puede emitir.
        </div>
      )}
      <dl className="perfil">
        {CAMPOS.filter((c) => perfil[c.key] != null && perfil[c.key] !== "").map((c) => (
          <div key={c.key}>
            <dt>{c.label}</dt>
            <dd>{String(perfil[c.key])}</dd>
          </div>
        ))}
      </dl>

      {abierto && (
        <Modal
          wide
          title="Datos del emisor"
          onClose={() => setAbierto(false)}
          footer={
            <>
              <button className="secondary" type="button" onClick={() => setAbierto(false)}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="emisor-form" disabled={busy}>
                <Icon name="check" />
                {busy ? "Guardando…" : "Guardar"}
              </button>
            </>
          }
        >
          {error && <p className="error">{error}</p>}
          <form id="emisor-form" className="form-grid dos" onSubmit={guardar}>
            {CAMPOS.map((c) => (
              <div className="field" key={c.key}>
                <label htmlFor={`emisor-${c.key}`}>
                  {c.label}
                  {c.obligatorio ? " *" : ""}
                </label>
                <input
                  id={`emisor-${c.key}`}
                  inputMode={c.numero ? "numeric" : undefined}
                  maxLength={c.max}
                  value={form[c.key] ?? ""}
                  onChange={(e) => setForm({ ...form, [c.key]: e.target.value })}
                />
                {c.ayuda && <small className="muted">{c.ayuda}</small>}
              </div>
            ))}
          </form>
        </Modal>
      )}
    </div>
  );
}
