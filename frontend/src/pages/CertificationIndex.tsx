/**
 * Portada del módulo de certificación: qué contribuyentes están en trámite y
 * cómo va cada uno.
 *
 * Existe porque quien opera esto lleva varias certificaciones a la vez. Sin
 * esta pantalla, llegar a un expediente exigía recordar en qué ficha de cliente
 * estaba, y no había forma de ver de un vistazo cuál se quedó atrás.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Icon from "../components/Icon";
import { api } from "../api";
import type { CertCustomer } from "../types";

export default function CertificationIndex() {
  const [filas, setFilas] = useState<CertCustomer[]>([]);
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(true);

  useEffect(() => {
    api
      .certIndex()
      .then(setFilas)
      .catch((e: Error) => setError(e.message))
      .finally(() => setCargando(false));
  }, []);

  if (cargando) return <p className="muted">Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <>
      <div className="card">
        <div className="card-head">
          <h2>Certificaciones en curso</h2>
        </div>
        <p className="muted" style={{ marginTop: 0 }}>
          Contribuyentes en ambiente de certificación. El avance cuenta los sets del trámite que ya
          se declararon en Mi SII.
        </p>
      </div>

      {filas.map((f) => {
        const pct = f.progress.sets_total
          ? Math.round((f.progress.sets_declared / f.progress.sets_total) * 100)
          : 0;
        return (
          <div className="card" key={f.customer_id}>
            <div className="card-head">
              <h2>{f.name}</h2>
              <span className="badge neutral">{f.rut}</span>
              <span className="spacer" />
              <Link className="boton-enlace" to={`/customers/${f.customer_id}/certification`}>
                <Icon name="audit" />
                Abrir expediente
              </Link>
            </div>
            <div className="avance">
              <div className="barra">
                <div className="barra-relleno" style={{ width: `${pct}%` }} />
              </div>
              <p className="muted" style={{ margin: "0.5rem 0 0" }}>
                {f.progress.sets_declared} de {f.progress.sets_total} sets declarados ·{" "}
                {f.progress.sets_accepted} aceptados por el SII ·{" "}
                {/* La fecha del último envío es lo que distingue una certificación
                    detenida de una que simplemente va lenta. */}
                {f.last_activity
                  ? `último envío el ${f.last_activity.slice(0, 10)}`
                  : "sin envíos todavía"}
              </p>
            </div>
          </div>
        );
      })}

      {filas.length === 0 && (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>
            No hay clientes en ambiente de certificación. Crea uno desde{" "}
            <Link to="/customers">Clientes</Link> con ambiente CERTIFICATION.
          </p>
        </div>
      )}
    </>
  );
}
