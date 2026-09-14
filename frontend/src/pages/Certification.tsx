import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ApiError } from "../api";
import { canWrite, useAuth } from "../auth";
import Icon from "../components/Icon";
import CertReadinessPanel from "../components/CertReadiness";
import CertImportCard from "../components/CertImportCard";
import CertReceiversCard from "../components/CertReceiversCard";
import CertTemplateCard from "../components/CertTemplateCard";
import ConfirmModal from "../components/ConfirmModal";
import Modal from "../components/Modal";
import { useApi } from "../hooks/useApi";
import type { CertCause, CertDocStatus, CertPreview, CertSet, CertSubmission } from "../types";
import { useToast } from "../toast";

/** Expediente de certificación de un cliente.
 *
 * Sólo lectura y anotación: desde aquí no se emite ni se envía nada. Lo que
 * resuelve es el seguimiento — qué sets van, en qué etapa está cada uno y
 * dónde está el sobre que hace falta para las muestras de impresión.
 */

/** Color y texto del badge de un set.
 *
 * Textos cortos a propósito: en una columna de diez sets, «aceptado, falta
 *  declarar» repetido siete veces obliga a leer la frase entera cada vez para
 *  notar cuál es la fila distinta. El matiz que se pierde lo recupera el título
 *  de la fila, que lleva la frase completa. */
const ESTADO: Record<string, { color: string; texto: string }> = {
  pendiente: { color: "neutral", texto: "Pendiente" },
  enviado: { color: "neutral", texto: "Sin respuesta" },
  aceptado: { color: "warn", texto: "Falta declarar" },
  con_reparos: { color: "warn", texto: "Con reparos" },
  rechazado: { color: "error", texto: "Rechazado" },
  declarado: { color: "ok", texto: "Declarado" },
  sin_dar_de_alta: { color: "neutral", texto: "Sin dar de alta" },
};

/** El estado de una etapa, en una palabra.
 *
 * Con las etapas en una línea el color de un punto de 8px pasa a ser el único
 * canal si no se dice también en texto, y hay quien no lo distingue. `atencion`
 * es un valor interno del API: aquí se traduce, no se filtra a la pantalla.
 */
const ETAPA: Record<string, string> = {
  ok: "cumplida",
  pendiente: "pendiente",
  atencion: "requiere atención",
  error: "con error",
};

/** El mismo estado en un glifo, para quien lee la forma antes que el color. */
const GLIFO: Record<string, string> = {
  ok: "✓",
  pendiente: "•",
  atencion: "!",
  error: "✕",
};

/** Estado registral de un documento ante el SII (servicio `getEstDte`).
 *
 * Describe la SITUACIÓN del documento, no si tuvo reparos. Confundir las dos
 * cosas hizo reemitir un set que estaba correcto: `MMC` y `AND` son lo que se
 * espera de una factura corregida y de una nota anulada.
 *
 * Tres niveles, no dos: `DOK` es "sin novedad"; `MMC` y `AND` son normales pero
 * con novedad —hay otro documento relacionado—, y esa diferencia sí le sirve al
 * operador. Ninguno es rojo: ninguno es un fallo del envío.
 */
const ESTADO_DOC: Record<string, { color: string; texto: string }> = {
  DOK: { color: "ok", texto: "recibido, sin novedad" },
  MMC: { color: "neutral", texto: "modificado por una nota de crédito" },
  AND: { color: "neutral", texto: "anulado por una nota de débito" },
  DNK: { color: "warn", texto: "la consulta no calza con lo registrado" },
};

/** Primero lo que pide acción. */
function orden(status: string): number {
  const color = ESTADO_DOC[status]?.color;
  if (color === "warn") return 0;
  if (color === undefined) return 1; // sin catalogar: conviene verlo
  if (color === "neutral") return 2;
  return 3;
}

/** Qué toca hacer ahora con un set, según su estado.
 *
 * No es un dato del API: es el mismo `state` traducido a un verbo. Existe
 * porque el siguiente paso había que deducirlo mirando cinco etapas y una tabla
 * de envíos, set por set, y con diez sets eso es trabajo de memoria.
 */
const ACCION: Record<string, string> = {
  sin_dar_de_alta: "Dar de alta con su número de atención",
  pendiente: "Emitir",
  enviado: "Consultar el estado en el SII",
  aceptado: "Declarar el avance en Mi SII",
  con_reparos: "Revisar los reparos",
  rechazado: "Corregir y reenviar",
  declarado: "Cerrado",
};

/** El mismo verbo de `ACCION`, en una palabra, para el botón de la fila.
 *
 * La frase entera repetida en diez filas no se veía clicable y ocupaba media
 * fila; va en el `title` del botón, que es donde sigue estando completa.
 */
const VERBO: Record<string, string> = {
  sin_dar_de_alta: "Dar de alta",
  pendiente: "Emitir",
  enviado: "Consultar",
  aceptado: "Declarar",
  con_reparos: "Revisar",
  rechazado: "Corregir",
};

/** En qué estado está lo que aún no se ha declarado.
 *
 * Se cuenta sobre los sets y no sobre `progress`, que sólo trae declarados y
 * aceptados: los reparos y los rechazos —justo los que piden trabajo— no vienen
 * contados desde el API, y eran invisibles hasta abrir set por set.
 */
const CUENTAS: { clave: string; texto: string; color: string; incluye: (e: string) => boolean }[] =
  [
    { clave: "aceptados", texto: "aceptados", color: "ok", incluye: (e) => e === "aceptado" },
    { clave: "reparos", texto: "con reparos", color: "warn", incluye: (e) => e === "con_reparos" },
    { clave: "rechazados", texto: "rechazados", color: "error", incluye: (e) => e === "rechazado" },
    {
      clave: "sin_declarar",
      texto: "sin enviar o sin respuesta",
      color: "neutral",
      incluye: (e) => e === "pendiente" || e === "enviado",
    },
  ];

/** Los tres cortes con que se mira el expediente.
 *
 * "Requieren acción" es lo que no avanza solo: emitir, consultar, corregir. Un
 * set aceptado NO entra ahí —ya está bien— pero sí en "listos para declarar",
 * que es exactamente cuando aparece el botón de declarar.
 */
const FILTROS: { clave: string; texto: string; incluye: (e: string) => boolean }[] = [
  { clave: "todos", texto: "Todos", incluye: () => true },
  {
    clave: "accion",
    texto: "Requieren acción",
    incluye: (e) =>
      e === "pendiente" || e === "enviado" || e === "con_reparos" || e === "rechazado",
  },
  { clave: "listos", texto: "Listos para declarar", incluye: (e) => e === "aceptado" },
];

/** Los `kind` vienen en snake_case porque son valores del modelo. */
const TIPO: Record<string, string> = {
  basico: "Set básico",
  exenta: "Factura exenta",
  guias: "Guías de despacho",
  exportacion_1: "Documentos de exportación (1)",
  exportacion_2: "Documentos de exportación (2)",
  exportacion: "Documentos de exportación",
  liquidacion: "Liquidación factura",
  factura_compra: "Factura de compra",
  libro_ventas: "Libro de ventas",
  libro_compras: "Libro de compras",
  libro_guias: "Libro de guías",
  boletas: "Boletas",
};

function fecha(iso: string | null) {
  return iso
    ? new Date(iso).toLocaleString("es-CL", { dateStyle: "medium", timeStyle: "short" })
    : "—";
}

/** Color del estado de un envío, mirando dentro del sobre.
 *
 * `EPR` está catalogado como aceptado y describe sólo al sobre. Un set con sus
 * 28 documentos rechazados dentro devolvía EPR, y pintarlo verde fue lo que
 * hizo que nadie mirara durante una semana.
 */
function colorEstado(e: CertSubmission): string {
  const aceptable = e.sii_state === "EPR" || e.sii_state === "LOK";
  if (!aceptable) return "error";
  // `?? []` dentro de `conteo` no es por el contrato —el API siempre lo manda—
  // sino por el modo de fallo: sin eso, un despliegue contra un API anterior
  // tumbaría el expediente entero por un campo que falta.
  const { informados, aceptados, reparos } = conteo(e);
  if (!informados) return "ok";
  // Un documento «aceptado con reparo» está aceptado: el SII lo registró y
  // anotó una observación. Cuenta como entregado, pero pinta ámbar porque la
  // observación hay que leerla —los rechazados van en su propia columna—.
  if (aceptados + reparos === 0) return "error";
  if (reparos > 0) return "warn";
  return aceptados < informados ? "warn" : "ok";
}

/** Cuántos documentos informó el SII de un envío, y cómo le fue a cada uno. */
function conteo(e: CertSubmission) {
  const stats = e.stats ?? [];
  return {
    informados: stats.reduce((n, s) => n + s.informed, 0),
    aceptados: stats.reduce((n, s) => n + s.accepted, 0),
    rechazados: stats.reduce((n, s) => n + s.rejected, 0),
    reparos: stats.reduce((n, s) => n + s.flagged, 0),
  };
}

/** Lo que hace falta para decidir si abrir un set, sin abrirlo.
 *
 * Todo sale del ÚLTIMO envío, nunca de la suma de todos: un set rechazado y
 * reenviado tiene dos envíos con los MISMOS documentos dentro, y sumarlos diría
 * "16 docs, 16 aceptados" donde hay ocho. El API los entrega ordenados por id
 * ascendente, así que el último es el vigente; si aún no se ha consultado, los
 * conteos quedan en cero y la fila sólo muestra cuántos documentos lleva, que
 * es exactamente lo que se sabe.
 */
function resumen(s: CertSet) {
  const ultimo = s.submissions[s.submissions.length - 1];
  if (!ultimo) return { docs: 0, informados: 0, aceptados: 0, rechazados: 0, reparos: 0 };
  return { docs: ultimo.documents.length, ...conteo(ultimo) };
}

/** Las guías del catálogo del set, una por código y no una por envío.
 *
 * El texto sale de `sii_state`, así que tres envíos con la misma respuesta
 * traían el MISMO párrafo tres veces —y con distinto color, porque el `ok` sí
 * se calcula por envío—. Eso hacía que un instructivo genérico («revisa el
 * detalle en Mi SII») se leyera como si fuera urgente en un envío y no en otro.
 * Aquí se deduplica por código; la urgencia de cada envío vive en su fila.
 */
function guias(envios: CertSubmission[]): CertCause[] {
  const porCodigo = new Map<string, CertCause>();
  for (const e of envios) {
    const c = e.cause;
    if (!c || (!c.usually && !c.check.length)) continue;
    if (!porCodigo.has(c.label)) porCodigo.set(c.label, c);
  }
  return [...porCodigo.values()];
}

/** Pesos: sin decimales y con separador de miles, que es como se leen. */
function money(v: unknown) {
  const n = Number(v ?? 0);
  // Sin decimales: el peso no los tiene. El descuento por línea puede dar una
  // fracción, pero el monto que el SII ve siempre es entero.
  return Number.isFinite(n) ? n.toLocaleString("es-CL", { maximumFractionDigits: 0 }) : "—";
}

function descargar(nombre: string, xmlBase64: string) {
  const bytes = Uint8Array.from(atob(xmlBase64), (ch) => ch.charCodeAt(0));
  const url = URL.createObjectURL(new Blob([bytes], { type: "application/xml" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = nombre;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Certification() {
  const { id } = useParams();
  const cid = Number(id);
  const { user } = useAuth();
  const writable = canWrite(user?.role);
  const toast = useToast();

  const { data, loading, error, reload } = useApi(async () => {
    const [dossier, notes] = await Promise.all([api.certDossier(cid), api.certNotes(cid)]);
    return { dossier, notes };
  }, [cid]);

  // La verificación va aparte del expediente: tarda más (firma un timbre de
  // prueba por CAF) y no debe retrasar la carga de la página.
  const verificacion = useApi(() => api.certChecks(cid), [cid]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [declarando, setDeclarando] = useState<CertSet | null>(null);
  const [fechaDecl, setFechaDecl] = useState(() => new Date().toISOString().slice(0, 10));
  const [asignando, setAsignando] = useState<CertSubmission | null>(null);
  const [codigo, setCodigo] = useState("");
  const [notaSet, setNotaSet] = useState<number | null>(null);
  const [nota, setNota] = useState("");
  const [altas, setAltas] = useState<Record<string, string>>({});
  const [editando, setEditando] = useState<number | null>(null);
  const [endpoint, setEndpoint] = useState("issue-batch");
  const [payload, setPayload] = useState("");
  const [clonarDe, setClonarDe] = useState("");
  const [vista, setVista] = useState<{ setId: number; datos: CertPreview } | null>(null);
  const [descartando, setDescartando] = useState<number | null>(null);
  const [docsSii, setDocsSii] = useState<{ sid: number; filas: CertDocStatus[] } | null>(null);
  // Un set abierto a la vez. Con los diez expandidos la página medía varios
  // miles de píxeles y no había forma de ver dónde estabas.
  const [abierto, setAbierto] = useState<number | null>(null);
  // `undefined` = todavía nadie ha tocado nada: se abre solo el primer set que
  // queda por trabajar. Después manda el operador, y un set que él cerró no
  // vuelve a abrirse porque el expediente se recargue.
  const [tocado, setTocado] = useState(false);
  const [filtro, setFiltro] = useState("todos");
  // Igual que el acordeón de los sets: hasta que el operador toca, manda el
  // estado del expediente; después manda él.
  const [prepAbierto, setPrepAbierto] = useState(false);
  const [prepTocada, setPrepTocada] = useState(false);

  /** Ejecuta la acción y dice si salió bien.
   *
   * Devuelve un booleano en vez de propagar la excepción porque quien la llama
   * cierra un modal al terminar, y cerrarlo cuando la acción falló se lleva por
   * delante el mensaje de error: el operador ve desaparecer el diálogo y
   * concluye que no pasó nada.
   */
  async function correr(fn: () => Promise<unknown>, ok: string): Promise<boolean> {
    setActionError("");
    setBusy(true);
    try {
      await fn();
      if (ok) toast.ok(ok);
      await reload();
      void verificacion.reload();
      return true;
    } catch (err) {
      const e = err as ApiError;
      // El error va al aviso Y se queda en el modal: dentro del diálogo es
      // donde se está mirando, y el aviso lo cubre si el modal ya se cerró.
      setActionError(e.message);
      toast.error(e.message, e.hints);
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="muted">Cargando…</p>;
  if (error) return <p className="error">{error}</p>;

  const sets = data?.dossier.sets ?? [];
  const sueltos = data?.dossier.unassigned ?? [];
  const prog = data?.dossier.progress ?? {
    sets_total: 0,
    sets_declared: 0,
    sets_accepted: 0,
    sets_pending: 0,
  };
  const faltan = sets.filter((s) => s.id === null);
  // Los que ya tienen número de atención: los que se trabajan. Los otros viven
  // en su propia tarjeta, porque lo único que admiten es que les peguen el
  // número.
  const conAlta = sets.filter((s): s is CertSet & { id: number } => s.id !== null);
  // El filtro es sólo de presentación: se aplica sobre lo que ya está cargado,
  // sin volver a pedir el expediente. Las cuentas de arriba se calculan sobre
  // `conAlta` a propósito — un contador que cambiara con el filtro no contaría
  // nada.
  const corte = FILTROS.find((f) => f.clave === filtro) ?? FILTROS[0];
  const visibles = conAlta.filter((s) => corte.incluye(s.state));

  // Plegada sólo cuando de verdad no queda nada que hacer en preparación. Si la
  // verificación aún no respondió, `ready` es undefined y la sección se abre:
  // no se puede afirmar que esté todo listo, y esconderlo sería afirmarlo.
  const prepLista = verificacion.data?.ready === true && conAlta.length > 0 && faltan.length === 0;
  const prepAbierta = prepTocada ? prepAbierto : !prepLista;

  // El primero que no está declarado: es el que toca. Si están todos cerrados,
  // no se abre ninguno.
  const porTrabajar = sets.find((s) => s.id !== null && s.state !== "declarado");
  const expandido = tocado ? abierto : (porTrabajar?.id ?? null);

  return (
    <>
      <p>
        <Link to={`/customers/${cid}`}>← Volver a la ficha</Link>
      </p>

      <div className="card">
        <div className="card-head">
          <h2>Expediente de certificación</h2>
        </div>
        {/* El avance del trámite, no un badge gris del tamaño del texto
            secundario: es el dato por el que se entra a esta pantalla. */}
        <p className="avance-cifra">
          <strong>
            {prog.sets_declared} de {prog.sets_total} sets declarados
          </strong>
          {prog.sets_declared === prog.sets_total && prog.sets_total > 0 && (
            <span className="badge ok con-punto">
              <span className="punto" aria-hidden="true" />
              Trámite completo
            </span>
          )}
        </p>
        <div
          className="barra"
          role="progressbar"
          aria-valuenow={prog.sets_declared}
          aria-valuemin={0}
          aria-valuemax={prog.sets_total}
          aria-label="Sets declarados"
        >
          <div
            className="barra-relleno"
            style={{ width: `${(prog.sets_declared / Math.max(prog.sets_total, 1)) * 100}%` }}
          />
        </div>
        {/* Los cuatro estados en que puede estar lo que falta. Salen de los sets
            reales y no de `progress`, que sólo trae declarados/aceptados: los
            reparos y los rechazos —que son los que piden trabajo— no vienen
            contados desde el API. */}
        <ul className="avance-cuentas">
          {CUENTAS.map((c) => (
            <li key={c.clave} className={c.color}>
              <span className="punto" aria-hidden="true" />
              <strong>{conAlta.filter((s) => c.incluye(s.state)).length}</strong>
              {c.texto}
            </li>
          ))}
        </ul>
        <p className="muted" style={{ marginTop: "0.7rem", marginBottom: 0 }}>
          Cada envío queda guardado con su TrackID y su sobre.
        </p>
      </div>

      {/* Preparación: lo que se hace UNA vez, antes de trabajar los sets.
          Ocupaba ~1200px antes del primer set, en una pantalla a la que se
          entra a mirar sets. Va plegada cuando ya no hay nada que hacer aquí
          —la verificación pasa y los sets existen— y abierta si falta algo.

          Las tarjetas se quedan montadas y sólo se oculta el contenedor: cada
          una trae su propio `useApi`, y desmontarlas al plegar convertiría un
          clic en una tanda de refetches. */}
      <section className="preparacion">
        <div className="card-head prep-head">
          <button
            className="btn-link prep-toggle"
            type="button"
            aria-expanded={prepAbierta}
            aria-controls="preparacion-cuerpo"
            onClick={() => {
              setPrepTocada(true);
              setPrepAbierto(!prepAbierta);
            }}
          >
            <span aria-hidden="true" className="set-flecha">
              {prepAbierta ? "▾" : "▸"}
            </span>
            <h2>Preparación</h2>
          </button>
          <span className="spacer" />
          {/* Plegar no puede esconder el estado: el resumen va en la cabecera. */}
          {!prepAbierta && (
            <span className="prep-resumen">
              {verificacion.data && (
                <span className={`badge ${verificacion.data.ready ? "ok" : "error"} con-punto`}>
                  <span className="punto" aria-hidden="true" />
                  {verificacion.data.ready
                    ? "Verificación OK"
                    : `${verificacion.data.errors} problema(s)`}
                </span>
              )}
              <span className="muted">
                {sets.length} set(s) cargados
                {faltan.length > 0 ? ` · ${faltan.length} sin dar de alta` : ""}
              </span>
            </span>
          )}
        </div>

        <div id="preparacion-cuerpo" hidden={!prepAbierta}>
          <CertReadinessPanel
            cid={cid}
            data={verificacion.data}
            loading={verificacion.loading}
            error={verificacion.error}
            reload={verificacion.reload}
            writable={writable}
          />

          <CertTemplateCard
            cid={cid}
            writable={writable}
            hasSets={sets.length > 0}
            onCreated={async () => {
              await reload();
              await verificacion.reload();
            }}
          />

          <CertImportCard
            cid={cid}
            writable={writable}
            onImported={async () => {
              await reload();
              await verificacion.reload();
            }}
          />

          <CertReceiversCard
            cid={cid}
            writable={writable}
            onSaved={() => void verificacion.reload()}
          />
        </div>
      </section>

      <div className="card">
        <div className="card-head">
          <h2>Pasos del trámite</h2>
        </div>
        {/* Un checklist secuencial, no seis tarjetas del mismo peso en una
            rejilla: los pasos dependen unos de otros y lo que se viene a ver es
            cuál está cumplido y cuál sigue. En vertical y numerado eso se lee
            de arriba abajo; en 3+3 había que reconstruirlo. */}
        <ol className="pasos-lista">
          {(data?.dossier.steps ?? []).map((p, i) => (
            <li className={p.state} key={p.key}>
              <span className="paso-n" aria-hidden="true">
                {p.done_at || p.state === "ok" ? "✓" : i + 1}
              </span>
              <div className="paso-cuerpo">
                <div className="paso-titulo">
                  {p.label}
                  <span className="paso-estado">
                    {p.done_at ? `cumplido el ${p.done_at}` : (ETAPA[p.state] ?? p.state)}
                  </span>
                </div>
                <div className="etapa-detalle">{p.detail}</div>
              </div>
              <div className="paso-acciones">
                {/* El paso 5 se arma desde los sobres guardados: el botón va aquí,
                  que es donde el operador lo busca. */}
                {p.key === "impresion" && writable && (
                  <button
                    className="btn-link"
                    type="button"
                    style={{ padding: 0 }}
                    disabled={busy}
                    onClick={() =>
                      correr(async () => {
                        const r = await api.certPrintSamples(cid);
                        const partes = r.documents.map((d) => String(d.html ?? ""));
                        const blob = new Blob(
                          [
                            `<!doctype html><meta charset="utf-8"><title>Muestras de impresión</title>${partes.join("<hr>")}`,
                          ],
                          { type: "text/html" },
                        );
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement("a");
                        a.href = url;
                        a.download = "muestras-impresion.html";
                        a.click();
                        URL.revokeObjectURL(url);
                        if (r.skipped.length) {
                          setActionError(
                            `Se saltaron ${r.skipped.length} sobre(s): ${r.skipped
                              .map((x) => `${x.track_id} (${x.reason})`)
                              .join(", ")}`,
                          );
                        }
                      }, "Muestras generadas: ábrelas e imprímelas a PDF.")
                    }
                  >
                    <Icon name="download" />
                    Generar muestras
                  </button>
                )}
                {!p.automatic && writable && (
                  <button
                    className="btn-link"
                    type="button"
                    style={{ padding: 0 }}
                    disabled={busy}
                    onClick={() =>
                      correr(
                        () =>
                          api.certStep(
                            cid,
                            p.key,
                            p.done_at ? null : new Date().toISOString().slice(0, 10),
                            p.note,
                          ),
                        p.done_at ? `Paso "${p.label}" reabierto.` : `Paso "${p.label}" cerrado.`,
                      )
                    }
                  >
                    <Icon name={p.done_at ? "restore" : "check"} />
                    {p.done_at ? "Reabrir" : "Marcar cumplido"}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
        <p className="muted" style={{ marginBottom: 0 }}>
          El primero lo lleva el sistema con los sets. Los otros cinco ocurren en el sitio del SII o
          por correo, así que los confirmas tú.
        </p>
      </div>

      {faltan.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Sets sin dar de alta</h2>
            <span className="spacer" />
            <span className="badge warn">{faltan.length}</span>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            El SII asigna a cada contribuyente un número de atención por set. Cópialos de Mi SII una
            vez y el expediente sabrá qué falta; los envíos caerán solos en su sitio.
          </p>
          <div className="tabla-scroll">
            <table>
              <thead>
                <tr>
                  <th>Set</th>
                  <th>N.º de atención</th>
                </tr>
              </thead>
              <tbody>
                {faltan.map((s) => (
                  <tr key={s.kind}>
                    <td>{TIPO[s.kind] ?? s.kind}</td>
                    <td>
                      <input
                        value={altas[s.kind] ?? ""}
                        onChange={(ev) => setAltas({ ...altas, [s.kind]: ev.target.value })}
                        placeholder="p. ej. 5038170"
                        aria-label={`Número de atención de ${TIPO[s.kind] ?? s.kind}`}
                        style={{ maxWidth: "11rem" }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {writable && (
            <button
              disabled={busy || !Object.values(altas).some((v) => v.trim())}
              onClick={() => correr(() => api.certSetup(cid, altas), "Sets dados de alta.")}
            >
              <Icon name="check" />
              Dar de alta
            </button>
          )}
        </div>
      )}

      {/* Una lista y no diez tarjetas: cada set plegado gastaba ~60px de alto y
          un borde redondeado para mostrar tres datos, y con diez sets eso es
          media pantalla de marco. Filas con divisor, densas y alineadas en
          columna para que el estado se escanee vertical. */}
      <div className="card">
        <div className="card-head">
          <h2>Sets</h2>
          <span className="spacer" />
          {/* Filtrado en cliente: son diez sets ya cargados, y volver a pedir el
              expediente para esconder filas sería gastar una llamada en nada. */}
          <div className="chips" role="group" aria-label="Filtrar los sets">
            {FILTROS.map((f) => {
              const n = conAlta.filter((x) => f.incluye(x.state)).length;
              return (
                <button
                  key={f.clave}
                  type="button"
                  className={`chip${filtro === f.clave ? " activo" : ""}`}
                  aria-pressed={filtro === f.clave}
                  onClick={() => setFiltro(f.clave)}
                >
                  {f.texto} <span className="chip-n">{n}</span>
                </button>
              );
            })}
          </div>
        </div>
        {visibles.length === 0 && (
          <p className="muted" style={{ margin: "0.4rem 0" }}>
            Ningún set en este corte. Los {conAlta.length} sets del expediente siguen ahí: quita el
            filtro para verlos.
          </p>
        )}
        <ul className="lista-sets">
          {visibles.map((s) => {
            const est = ESTADO[s.state] ?? ESTADO.pendiente;
            const c = resumen(s);
            const abiertoEste = expandido === s.id;
            return (
              <li key={s.id} className={abiertoEste ? "abierto" : undefined}>
                <div className="set-fila">
                  <button
                    className="btn-link set-toggle"
                    type="button"
                    aria-expanded={abiertoEste}
                    onClick={() => {
                      setTocado(true);
                      setAbierto(abiertoEste ? null : s.id);
                    }}
                  >
                    <span aria-hidden="true" className="set-flecha">
                      {abiertoEste ? "▾" : "▸"}
                    </span>
                    <strong className="code">{s.code}</strong>
                    {s.kind && <span className="set-tipo">{TIPO[s.kind] ?? s.kind}</span>}
                  </button>

                  {/* Lo que hace falta para decidir si abrir el set, que antes
                      obligaba a expandirlo para verlo. */}
                  <span className="set-cifras">
                    <span>{c.docs} docs</span>
                    {c.informados > 0 && (
                      <>
                        <span className="ok">{c.aceptados} aceptados</span>
                        {c.rechazados > 0 && <span className="mal">{c.rechazados} rechazados</span>}
                        {c.reparos > 0 && <span className="ojo">{c.reparos} con reparos</span>}
                      </>
                    )}
                  </span>

                  <span className={`badge ${est.color} con-punto`}>
                    <span className="punto" aria-hidden="true" />
                    {est.texto}
                  </span>

                  <span className="set-accion">
                    {/* Un set declarado está cerrado: no ofrece verbo. */}
                    {VERBO[s.state] && (
                      <button
                        className="secondary sm"
                        type="button"
                        title={ACCION[s.state]}
                        onClick={() => {
                          // "Declarar" abre el mismo diálogo que el botón de
                          // dentro del set. Los demás verbos necesitan lo que
                          // hay dentro —la previa, la tabla de envíos—, así que
                          // la fila abre el set y deja al operador donde están
                          // los controles de verdad.
                          if (s.state === "aceptado" && writable) {
                            setDeclarando(s);
                            setNotaSet(null);
                            return;
                          }
                          setTocado(true);
                          setAbierto(abiertoEste ? null : s.id);
                        }}
                      >
                        {VERBO[s.state]}
                      </button>
                    )}
                  </span>
                </div>

                {!abiertoEste ? null : (
                  <div className="set-cuerpo">
                    {/* Las cinco etapas en una línea, no en cinco cajas con borde.
                    Ocupaban una banda entera para decir cinco palabras, y el
                    porqué de cada una —que es lo único que no cabe— se consulta
                    abajo, cuando hace falta. El estado no se comunica sólo por
                    color: cada etapa lleva su glifo y su palabra. */}
                    <ul className="etapas-linea">
                      {s.stages.map((e) => (
                        <li className={e.state} key={e.key}>
                          <span className="etapa-punto" aria-hidden="true" />
                          <span aria-hidden="true" className="etapa-glifo">
                            {GLIFO[e.state] ?? "•"}
                          </span>
                          {e.label}
                          <span className="etapa-estado">{ETAPA[e.state] ?? e.state}</span>
                        </li>
                      ))}
                    </ul>
                    {s.stages.some((e) => e.detail) && (
                      <details className="etapas-detalle">
                        <summary>Por qué está así cada etapa</summary>
                        <dl>
                          {s.stages
                            .filter((e) => e.detail)
                            .map((e) => (
                              <div key={e.key}>
                                <dt>{e.label}</dt>
                                <dd>{e.detail}</dd>
                              </div>
                            ))}
                        </dl>
                      </details>
                    )}

                    <div className="tabla-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>TrackID</th>
                            <th>Enviado</th>
                            <th>Sobre</th>
                            <th>Docs</th>
                            <th>Estado SII</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {s.submissions.map((e) => (
                            <tr key={e.id}>
                              <td>
                                {e.track_id ? (
                                  <span className="code">{e.track_id}</span>
                                ) : (
                                  <span className="muted">sin enviar</span>
                                )}
                              </td>
                              <td className="nowrap">{fecha(e.sent_at)}</td>
                              <td className="muted">{e.envelope_kind}</td>
                              <td>{e.documents.length}</td>
                              <td>
                                {!e.track_id ? (
                                  <span className="badge warn">emitido, sin enviar</span>
                                ) : e.sii_state ? (
                                  <>
                                    {/* El color sale del CONTENIDO, no del estado del
                                sobre: EPR dice que el sobre se pudo leer, y
                                puede traer todos sus documentos rechazados.
                                Aquí es el único sitio donde ese color significa
                                algo, porque aquí está el conteo que lo explica. */}
                                    <span className={`badge ${colorEstado(e)}`}>{e.sii_state}</span>
                                    {e.cause && (
                                      <span className="estado-glosa">{e.cause.label}</span>
                                    )}
                                    {(e.stats ?? []).length > 0 && (
                                      <div className="conteo">
                                        {/* El dato que distingue un sobre entregado de
                                    uno que sólo se pudo leer. Va en texto, no
                                    sólo en el color de la insignia. */}
                                        <strong>
                                          {conteo(e).aceptados} de {conteo(e).informados} aceptados
                                        </strong>
                                        {(e.stats ?? []).map((s) => (
                                          <div className="muted" key={s.doc_type}>
                                            tipo {s.doc_type}: {s.accepted}/{s.informed}
                                            {s.rejected ? ` · ${s.rejected} rechazados` : ""}
                                            {s.flagged ? ` · ${s.flagged} con reparos` : ""}
                                          </div>
                                        ))}
                                      </div>
                                    )}
                                  </>
                                ) : (
                                  <span className="muted">sin consultar</span>
                                )}
                              </td>
                              <td>
                                <div className="actions">
                                  {writable && !e.track_id && (
                                    <button
                                      className="btn-link"
                                      type="button"
                                      disabled={busy}
                                      onClick={() =>
                                        correr(
                                          () => api.certSend(cid, e.id),
                                          "Sobre enviado al SII.",
                                        )
                                      }
                                    >
                                      <Icon name="upload" />
                                      Enviar al SII
                                    </button>
                                  )}
                                  {writable && !e.track_id && (
                                    <button
                                      className="btn-link danger"
                                      type="button"
                                      disabled={busy}
                                      title="Lo borra sin enviarlo. Los folios que gastó no vuelven."
                                      onClick={() => setDescartando(e.id)}
                                    >
                                      <Icon name="trash" />
                                      Descartar
                                    </button>
                                  )}
                                  {writable && e.track_id && (
                                    <button
                                      className="btn-link"
                                      type="button"
                                      disabled={busy}
                                      onClick={() =>
                                        correr(
                                          () => api.certRefresh(cid, e.id),
                                          "Estado actualizado.",
                                        )
                                      }
                                    >
                                      <Icon name="search" />
                                      Consultar
                                    </button>
                                  )}
                                  {writable && e.track_id && (
                                    <button
                                      className="btn-link"
                                      type="button"
                                      disabled={busy}
                                      title="Pregunta al SII documento por documento: es donde dice el motivo del reparo"
                                      onClick={() =>
                                        correr(async () => {
                                          const filas = await api.certDocStatuses(cid, e.id);
                                          setDocsSii({ sid: e.id, filas });
                                        }, "")
                                      }
                                    >
                                      <Icon name="audit" />
                                      Ver cada documento
                                    </button>
                                  )}
                                  <button
                                    className="btn-link neutral"
                                    type="button"
                                    onClick={() =>
                                      correr(async () => {
                                        const env = await api.certEnvelope(cid, e.id);
                                        descargar(env.filename, env.xml_base64);
                                      }, "Sobre descargado.")
                                    }
                                  >
                                    <Icon name="download" />
                                    Sobre
                                  </button>
                                </div>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    {/* La guía del SII, una sola vez por código y no una por envío.
                    Es instructivo del catálogo —depende de la respuesta, no del
                    envío—, así que repetirlo bajo cada fila sólo alejaba la
                    tabla. Se abre sola cuando hay algo que corregir; el
                    instructivo de un envío correcto se queda plegado. */}
                    {guias(s.submissions).length > 0 && (
                      <details className="guia-nota" open={guias(s.submissions).some((c) => !c.ok)}>
                        <summary>
                          <Icon name="info" />
                          Qué dice el SII de estas respuestas
                        </summary>
                        {guias(s.submissions).map((c) => (
                          <div className="guia-caja" key={c.label}>
                            <strong>{c.label}.</strong> {c.meaning}
                            {c.usually && (
                              <>
                                {" "}
                                <em>{c.usually}</em>
                              </>
                            )}
                            {c.check.length > 0 && (
                              <ol className="guia-pasos">
                                {c.check.map((paso) => (
                                  <li key={paso}>{paso}</li>
                                ))}
                              </ol>
                            )}
                          </div>
                        ))}
                      </details>
                    )}

                    {writable && (
                      <div className="actions" style={{ marginTop: "0.8rem" }}>
                        {/* Sólo se ofrece declarar lo que el SII ya aceptó. Declarar un set
                  que aún no respondió —o que rechazó— es informar al Servicio un
                  avance que no ocurrió, y eso no se deshace desde aquí. */}
                        {!s.declared_at &&
                          (s.state === "aceptado" ? (
                            <button
                              type="button"
                              onClick={() => {
                                setDeclarando(s);
                                setNotaSet(null);
                              }}
                            >
                              <Icon name="check" />
                              Marcar declarado en Mi SII
                            </button>
                          ) : (
                            <span className="muted" style={{ fontSize: "0.85rem" }}>
                              {s.state === "rechazado"
                                ? "El SII rechazó el último envío: corrige y reenvía antes de declarar."
                                : "Consulta el estado en el SII antes de declarar el avance."}
                            </span>
                          ))}
                        {/* Un solo camino a emitir, y pasa por revisar. Emitir quema
                    folios y no se deshace, así que el botón que lo dispara está
                    dentro de la previa, junto a lo que se va a emitir. */}
                        <button
                          className="secondary"
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            correr(async () => {
                              setVista({ setId: s.id, datos: await api.certPreview(cid, s.id) });
                            }, "")
                          }
                        >
                          <Icon name="search" />
                          Revisar y emitir
                        </button>
                        <button
                          className="secondary"
                          type="button"
                          onClick={async () => {
                            setEditando(s.id);
                            setPayload("");
                            try {
                              const d = await api.certDefinition(cid, s.id);
                              setEndpoint(d.endpoint);
                              setPayload(JSON.stringify(d.payload, null, 2));
                            } catch {
                              setEndpoint("issue-batch");
                              setPayload("{}");
                            }
                          }}
                        >
                          <Icon name="settings" />
                          Editar definición
                        </button>
                        <button
                          className="secondary"
                          type="button"
                          onClick={() => setNotaSet(s.id)}
                        >
                          <Icon name="edit" />
                          Anotar
                        </button>
                      </div>
                    )}

                    {vista?.setId === s.id && (
                      <Modal
                        wide
                        title={`Qué se emite — Set ${s.code} · ${s.kind}`}
                        onClose={() => setVista(null)}
                        footer={
                          <>
                            <button
                              className="secondary"
                              type="button"
                              onClick={() => setVista(null)}
                            >
                              <Icon name="x" />
                              Cerrar
                            </button>
                            {/* Reemitir gasta folios nuevos: sólo se ofrece cuando el
                        API ya dijo que hay un sobre sin enviar, y dice el
                        precio en el propio botón. */}
                            {actionError.includes("sin enviar") && (
                              <button
                                className="secondary"
                                type="button"
                                disabled={busy}
                                onClick={() =>
                                  correr(
                                    () => api.certEmit(cid, s.id, true),
                                    `Set ${s.code} emitido de nuevo.`,
                                  ).then((ok) => ok && setVista(null))
                                }
                              >
                                <Icon name="plus" />
                                Emitir de nuevo (gasta folios)
                              </button>
                            )}
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                correr(
                                  () => api.certEmit(cid, s.id),
                                  `Set ${s.code} emitido. Revísalo y envíalo cuando esté bien.`,
                                ).then((ok) => ok && setVista(null))
                              }
                            >
                              <Icon name="plus" />
                              Emitir{" "}
                              {vista.datos.kind === "libro"
                                ? "el libro"
                                : `los ${vista.datos.documents.length} documentos`}
                            </button>
                          </>
                        }
                      >
                        {/* Donde se decide gastar folios es donde tiene que verse que la
                    configuración no está lista, no sólo arriba en la página. */}
                        {/* Lo que completó el sistema, o por qué no pudo: emisor sin
                    configurar, receptores que se repiten, sets sin aceptar
                    para armar un libro. */}
                        {(vista.datos.system_notes ?? []).length > 0 && (
                          <div className="notice warn">
                            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                              {(vista.datos.system_notes ?? []).map((n) => (
                                <li key={n}>{n}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {verificacion.data && !verificacion.data.ready && (
                          <div className="notice error">
                            <strong>
                              La verificación tiene {verificacion.data.errors} problema(s).
                            </strong>{" "}
                            Emitir ahora gastaría folios en documentos que el SII rechazaría.
                            Resuélvelos en «Verificación antes de emitir», al principio del
                            expediente.
                          </div>
                        )}
                        {actionError && <p className="error">{actionError}</p>}

                        <div className="previa-cabecera">
                          <strong>{vista.datos.summary}</strong>
                          <span className="muted">{vista.datos.detail}</span>
                        </div>
                        <div className="tabla-scroll">
                          <table>
                            <thead>
                              <tr>
                                <th>#</th>
                                <th>Documento</th>
                                <th>Receptor</th>
                                <th>{vista.datos.kind === "libro" ? "Folio" : "Ítems"}</th>
                                <th>Afecto</th>
                                <th>Exento</th>
                                {vista.datos.kind === "libro" && <th>Total</th>}
                              </tr>
                            </thead>
                            <tbody>
                              {vista.datos.documents.map((doc, i) => {
                                const d = doc as Record<string, unknown>;
                                const items = (d.items ?? []) as {
                                  name: string;
                                  quantity: number | null;
                                  unit_price: number | null;
                                  discount_pct: number | null;
                                  exempt: boolean;
                                  amount: number | null;
                                }[];
                                const refs = (d.references ?? []) as string[];
                                const globales = (d.global_discounts ?? []) as string[];
                                const libro = vista.datos.kind === "libro";
                                return (
                                  <tr key={i}>
                                    <td className="num">{String(d.position ?? i + 1)}</td>
                                    <td>
                                      {String(d.doc_label)}
                                      {refs.map((r) => (
                                        <div className="previa-ref" key={r}>
                                          ↳ {r}
                                        </div>
                                      ))}
                                      {globales.map((g) => (
                                        <div className="previa-ref" key={g}>
                                          ◆ {g}
                                        </div>
                                      ))}
                                    </td>
                                    <td>
                                      {String(d.receiver ?? "")}
                                      {d.receiver_rut ? (
                                        <div className="muted">{String(d.receiver_rut)}</div>
                                      ) : null}
                                    </td>
                                    <td>
                                      {libro ? (
                                        String(d.folio ?? "—")
                                      ) : (
                                        <div className="previa-items">
                                          {items.map((it) => (
                                            <div key={it.name}>
                                              {it.name}
                                              {/* Una liquidación factura no tiene precio
                                          unitario: sus líneas traen la cantidad de
                                          documentos liquidados y el monto. Pintarlas
                                          con el molde de "cantidad × precio" daba
                                          "4 × 0", que no significa nada. */}
                                              {it.unit_price != null ? (
                                                <span className="muted">
                                                  {" · "}
                                                  {it.quantity} × {money(it.unit_price)}
                                                  {it.discount_pct
                                                    ? " −" + it.discount_pct + "%"
                                                    : ""}
                                                </span>
                                              ) : it.amount != null ? (
                                                <span className="muted">
                                                  {" · "}
                                                  {it.quantity != null
                                                    ? `${it.quantity} doc · `
                                                    : ""}
                                                  {money(it.amount)}
                                                </span>
                                              ) : null}
                                              {it.exempt ? (
                                                <span className="badge neutral"> exento</span>
                                              ) : null}
                                            </div>
                                          ))}
                                        </div>
                                      )}
                                    </td>
                                    <td className="num">{money(libro ? d.net : d.lines_affect)}</td>
                                    <td className="num">
                                      {money(libro ? d.exempt : d.lines_exempt)}
                                    </td>
                                    {libro ? <td className="num">{money(d.total)}</td> : null}
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                        {vista.datos.note ? (
                          <p className="muted previa-nota">{vista.datos.note}</p>
                        ) : null}
                      </Modal>
                    )}

                    {editando === s.id && (
                      <Modal
                        wide
                        title={`Qué emite el set ${s.code}`}
                        onClose={() => setEditando(null)}
                        footer={
                          <>
                            <input
                              value={clonarDe}
                              onChange={(ev) => setClonarDe(ev.target.value)}
                              placeholder="id de otro cliente"
                              style={{ maxWidth: "11rem" }}
                              aria-label="Cliente del que copiar la definición"
                            />
                            <button
                              className="secondary"
                              type="button"
                              disabled={busy || !clonarDe.trim()}
                              title="Copia la definición del mismo tipo de set desde otro contribuyente ya probado"
                              onClick={() =>
                                correr(async () => {
                                  const d = await api.certCloneDefinition(
                                    cid,
                                    s.id,
                                    Number(clonarDe),
                                  );
                                  setEndpoint(d.endpoint);
                                  setPayload(JSON.stringify(d.payload, null, 2));
                                }, "Definición copiada. Revísala antes de emitir.")
                              }
                            >
                              <Icon name="copy" />
                              Clonar
                            </button>
                            <span className="spacer" />
                            <button
                              className="secondary"
                              type="button"
                              onClick={() => setEditando(null)}
                            >
                              <Icon name="x" />
                              Cancelar
                            </button>
                            <button type="submit" form="definicion-form" disabled={busy}>
                              <Icon name="check" />
                              Guardar
                            </button>
                          </>
                        }
                      >
                        {actionError && <p className="error">{actionError}</p>}
                        <form
                          id="definicion-form"
                          className="form-grid"
                          onSubmit={(ev: FormEvent) => {
                            ev.preventDefault();
                            let cuerpo: unknown;
                            try {
                              cuerpo = JSON.parse(payload);
                            } catch {
                              setActionError("El contenido no es JSON válido.");
                              return;
                            }
                            correr(
                              () => api.certSaveDefinition(cid, s.id, endpoint, cuerpo),
                              "Definición guardada.",
                            ).then((ok) => ok && setEditando(null));
                          }}
                        >
                          <div className="field">
                            <label>Endpoint de emisión</label>
                            <select
                              value={endpoint}
                              onChange={(ev) => setEndpoint(ev.target.value)}
                            >
                              <option value="issue-batch">
                                Documentos en lote (33, 34, 52, 56, 61, 46)
                              </option>
                              <option value="issue-export-batch">
                                Exportación (110, 111, 112)
                              </option>
                              <option value="issue-settlement-batch">
                                Liquidación factura (43)
                              </option>
                              <option value="books">Libro de compras / ventas</option>
                              <option value="books/guides">Libro de guías</option>
                            </select>
                          </div>
                          <div className="field">
                            <label>
                              Cuerpo de la emisión — se guarda tal cual, así que esto es exactamente
                              lo que se enviará
                            </label>
                            <textarea
                              value={payload}
                              onChange={(ev) => setPayload(ev.target.value)}
                              rows={12}
                              spellCheck={false}
                              className="json"
                            />
                          </div>
                        </form>
                      </Modal>
                    )}

                    {notaSet === s.id && (
                      <Modal
                        title={`Anotar en el set ${s.code}`}
                        onClose={() => setNotaSet(null)}
                        footer={
                          <>
                            <button
                              className="secondary"
                              type="button"
                              onClick={() => setNotaSet(null)}
                            >
                              <Icon name="x" />
                              Cancelar
                            </button>
                            <button type="submit" form="nota-form" disabled={busy || !nota.trim()}>
                              <Icon name="check" />
                              Guardar
                            </button>
                          </>
                        }
                      >
                        {actionError && <p className="error">{actionError}</p>}
                        <form
                          id="nota-form"
                          className="form-grid"
                          onSubmit={(ev: FormEvent) => {
                            ev.preventDefault();
                            correr(() => api.certAddNote(cid, s.id, nota), "Anotado.").then(
                              (ok) => {
                                if (!ok) return;
                                setNota("");
                                setNotaSet(null);
                              },
                            );
                          }}
                        >
                          <div className="field">
                            <label>Qué se probó o se descartó</label>
                            <input
                              value={nota}
                              onChange={(ev) => setNota(ev.target.value)}
                              placeholder="No es la firma: xmlsec valida los sobres enviados"
                            />
                          </div>
                        </form>
                      </Modal>
                    )}

                    {(data?.notes ?? [])
                      .filter((n) => n.set_id === s.id)
                      .map((n) => (
                        <p className="muted" key={n.id} style={{ marginBottom: "0.3rem" }}>
                          <span className="code">{fecha(n.created_at)}</span> {n.text}{" "}
                          <em>— {n.author}</em>
                        </p>
                      ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </div>

      {sueltos.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Envíos sin clasificar</h2>
            <span className="spacer" />
            <span className="badge warn">{sueltos.length}</span>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            Se guardaron sin saber a qué set del SII pertenecen. Preferimos capturarlos así antes
            que perderlos; asígnalos para que entren en el expediente.
          </p>
          <div className="tabla-scroll">
            <table>
              <thead>
                <tr>
                  <th>TrackID</th>
                  <th>Enviado</th>
                  <th>Sobre</th>
                  <th>Docs</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sueltos.map((e) => (
                  <tr key={e.id}>
                    <td>
                      <span className="code">{e.track_id}</span>
                    </td>
                    <td className="nowrap">{fecha(e.sent_at)}</td>
                    <td className="muted">{e.envelope_kind}</td>
                    <td>{e.documents.length}</td>
                    <td>
                      {writable && (
                        <button
                          className="btn-link"
                          type="button"
                          onClick={() => {
                            setAsignando(e);
                            setCodigo("");
                          }}
                        >
                          <Icon name="settings" />
                          Asignar a un set
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {asignando && (
            <form
              className="form-grid"
              style={{ marginTop: "0.8rem" }}
              onSubmit={(ev: FormEvent) => {
                ev.preventDefault();
                correr(
                  () => api.certAssign(cid, asignando.id, codigo, ""),
                  `Envío ${asignando.track_id} asignado al set ${codigo}.`,
                ).then((ok) => ok && setAsignando(null));
              }}
            >
              <div className="field">
                <label>
                  N.º de atención del set para <span className="code">{asignando.track_id}</span>
                </label>
                <input
                  value={codigo}
                  onChange={(ev) => setCodigo(ev.target.value)}
                  placeholder="5038170"
                  autoFocus
                />
              </div>
              <div className="actions">
                <button disabled={busy || !codigo.trim()}>
                  <Icon name="check" />
                  Asignar
                </button>
                <button className="secondary" type="button" onClick={() => setAsignando(null)}>
                  <Icon name="x" />
                  Cancelar
                </button>
              </div>
            </form>
          )}
        </div>
      )}
      {/* A nivel de página y no dentro del set: ahora se dispara también desde la
          fila plegada, y ahí dentro no se renderizaría nada. `declarando` ya
          lleva el set entero, así que no necesita el contexto del bucle. */}
      {declarando && (
        <Modal
          title={`Declarar el avance del set ${declarando.code}`}
          onClose={() => setDeclarando(null)}
          footer={
            <>
              <button className="secondary" type="button" onClick={() => setDeclarando(null)}>
                <Icon name="x" />
                Cancelar
              </button>
              <button type="submit" form="declarar-form" disabled={busy}>
                <Icon name="check" />
                Confirmar
              </button>
            </>
          }
        >
          {actionError && <p className="error">{actionError}</p>}
          <form
            id="declarar-form"
            className="form-grid"
            onSubmit={(ev: FormEvent) => {
              ev.preventDefault();
              const sid = declarando.id;
              if (sid === null) return;
              correr(
                () => api.certDeclare(cid, sid, fechaDecl),
                `Set ${declarando.code} marcado como declarado.`,
              ).then((ok) => ok && setDeclarando(null));
            }}
          >
            <div className="field">
              <label>Fecha en que se declaró el avance</label>
              <input
                type="date"
                value={fechaDecl}
                onChange={(ev) => setFechaDecl(ev.target.value)}
              />
            </div>
          </form>
        </Modal>
      )}
      {descartando !== null && (
        <ConfirmModal
          title="Descartar el sobre sin enviar"
          confirmLabel="Descartar"
          danger
          busy={busy}
          onClose={() => setDescartando(null)}
          onConfirm={() => {
            const sid = descartando;
            setDescartando(null);
            void correr(() => api.certDiscard(cid, sid), "Sobre descartado.");
          }}
          message={
            <>
              <p>
                El sobre se borra y el set queda libre para emitir de nuevo. El SII nunca lo vio,
                así que no hay nada que corregir de su lado.
              </p>
              <p className="muted">
                Los folios que gastó no vuelven: el siguiente sobre usará folios nuevos. Para el SII
                eso da igual, porque un folio que nunca le llegó lo sigue contando como disponible.
              </p>
            </>
          }
        />
      )}
      {docsSii && (
        <Modal
          wide
          title={`Situación registral de cada documento — sobre #${docsSii.sid}`}
          onClose={() => setDocsSii(null)}
        >
          <p className="muted" style={{ marginTop: 0 }}>
            Esto es lo que el SII tiene registrado de cada documento. <strong>No</strong> es el
            motivo de un reparo.
          </p>
          <table>
            <thead>
              <tr>
                <th>Tipo</th>
                <th>Folio</th>
                <th>Estado registral</th>
                <th>Respuesta del SII</th>
              </tr>
            </thead>
            <tbody>
              {[...docsSii.filas]
                // Primero lo que pide acción; lo normal, al final. Con tres o
                // cuatro filas el orden por folio no aporta nada y esconde la
                // única que hay que mirar.
                .sort((a, b) => orden(a.status) - orden(b.status))
                .map((f) => {
                  const e = ESTADO_DOC[f.status] ?? {
                    color: "neutral",
                    texto: "estado no catalogado",
                  };
                  return (
                    <tr key={`${f.doc_type}-${f.folio}`}>
                      <td>{f.doc_type}</td>
                      <td className="num">{f.folio}</td>
                      <td>
                        <span className={`badge ${e.color}`}>{f.status}</span>{" "}
                        <span>{e.texto}</span>
                      </td>
                      {/* La glosa del Servicio va de dato secundario, no de
                          explicación: es la que hacía leer un MMC como un fallo.
                          Se conserva porque es el texto que el operador cita
                          cuando llama al SII. */}
                      <td className="muted">{f.error_label || f.label || "—"}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
          <p className="muted">
            El motivo de un reparo llega por <strong>correo del SII</strong>, no por esta consulta.
            Pídelo en la página de estado del envío en Mi SII, con «Enviar Correo».
          </p>
        </Modal>
      )}
    </>
  );
}
