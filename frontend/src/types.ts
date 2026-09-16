export interface Token {
  access_token: string;
  token_type: string;
  role: string;
  customer_id: number | null;
}

export interface Me {
  id: number;
  email: string;
  role: string;
  customer_id: number | null;
  totp_enabled: boolean;
}

export interface TotpStatus {
  enabled: boolean;
  recovery_codes_left: number;
}

export interface TotpSetup {
  otpauth_uri: string;
  secret: string;
}

/** Datos del emisor: van en el encabezado de cada documento. */
export interface IssuerProfile {
  legal_name: string | null;
  activity: string | null;
  economic_activity: number | null;
  address: string | null;
  commune: string | null;
  city: string | null;
  branch_name: string | null;
  branch_code: number | null;
}

export interface Customer {
  id: number;
  name: string;
  key: string;
  rut: string;
  environment: string;
  /** Resolución del SII que autoriza a emitir. Va en la carátula de cada DTE. */
  resolution_number: number;
  resolution_date: string;
  issuer: IssuerProfile;
  /** Lo que falta para poder emitir, en palabras. Vacío = completo. */
  issuer_missing: string[];
  deleted_at?: string | null;
}

export interface ServiceInfo {
  code: string;
  name: string;
}

export interface GrantedService {
  service_code: string;
  name: string;
}

export interface ServiceGrantResult {
  service_code: string;
  granted: boolean;
  apikey: string | null; // presente solo cuando el servidor la generó
}

export interface CertificateInfo {
  id: number;
  due_date: string;
  created_at: string;
  expired: boolean;
  /** RUT del FIRMANTE, no el de la empresa: es el que necesita «Enviar Doctos». */
  rut: string | null;
  holder: string | null;
  issuer: string | null;
}

export interface CafInfo {
  id: number;
  doc_type: number;
  folio_from: number;
  folio_to: number;
  exhausted: boolean;
  last_folio: number;
}

export interface User {
  id: number;
  email: string;
  role: string;
  customer_id: number | null;
  is_active: boolean;
  created_at: string;
  last_login: string | null;
  deleted_at?: string | null;
}

export interface RequestLog {
  id: number;
  principal_type: string;
  principal_id: number | null;
  principal_role: string | null;
  service_code: string | null;
  method: string;
  path: string;
  request_id: string;
  ip: string | null;
  status_code: number;
  outcome: string;
  latency_ms: number;
  created_at: string;
}

export interface AdminAudit {
  id: number;
  actor_user_id: number | null;
  action: string;
  target_type: string;
  target_id: string | null;
  summary: string;
  created_at: string;
}

export interface RcvDocument {
  operation: string;
  state: string;
  doc_type: number;
  folio: number;
  counterpart_rut: string;
  counterpart_name: string;
  date: string;
  exempt_amount: number;
  net_amount: number;
  vat_amount: number;
  total_amount: number;
}

export interface RcvResponse {
  issuer_rut: string;
  period: string;
  operation: string;
  count: number;
  documents: RcvDocument[];
}

export interface BheDocument {
  issuer_rut: string;
  issuer_name: string;
  folio: number;
  issue_date: string | null;
  gross_amount: number;
  retention_amount: number;
  net_amount: number;
  status: string;
  cancel_date: string | null;
}

export interface BheResponse {
  receiver_rut: string;
  period: string;
  count: number;
  documents: BheDocument[];
}

// --- Expediente de certificación ---

export interface CertDocument {
  doc_type: number;
  folio: number;
}

/** Qué significa la respuesta del SII y qué revisar. Sale del catálogo del
 *  servicio, no de la base: es conocimiento del dominio. */
export interface CertCause {
  label: string;
  meaning: string;
  usually: string;
  check: string[];
  ok: boolean;
}

/** Cuántos documentos de un tipo aceptó y rechazó el SII dentro del sobre. */
export interface CertDocStats {
  doc_type: number;
  informed: number;
  accepted: number;
  rejected: number;
  flagged: number;
}

export interface CertSubmission {
  id: number;
  set_id: number | null;
  /** Nulos mientras el sobre está emitido y sin enviar. */
  track_id: string | null;
  sent_at: string | null;
  envelope_kind: string;
  sii_state: string | null;
  sii_detail: string | null;
  checked_at: string | null;
  documents: CertDocument[];
  cause: CertCause | null;
  /** Desglose del SII. Vacío en libros y en envíos sin consultar. */
  stats: CertDocStats[];
}

/** Una etapa con su semáforo. `state` es el color; `detail`, el porqué. */
export interface CertStage {
  key: "requisitos" | "emision" | "envio" | "estado" | "declaracion";
  label: string;
  state: "ok" | "pendiente" | "atencion" | "error";
  detail: string;
}

export interface CertSet {
  /** null en un set que el trámite pide pero que aún no se dio de alta. */
  id: number | null;
  code: string;
  kind: string;
  state: string;
  declared_at: string | null;
  stages: CertStage[];
  submissions: CertSubmission[];
}

export interface CertStep {
  key: string;
  label: string;
  detail: string;
  automatic: boolean;
  state: "ok" | "pendiente" | "atencion";
  done_at: string | null;
  note: string;
}

export interface CertProgress {
  sets_total: number;
  sets_declared: number;
  sets_accepted: number;
  sets_pending: number;
}

/** Un contribuyente en certificación tal como lo lista el índice. */
export interface CertCustomer {
  customer_id: number;
  name: string;
  rut: string;
  key: string;
  progress: CertProgress;
  last_activity: string | null;
}

export interface CertDossier {
  customer_id: number;
  progress: CertProgress;
  steps: CertStep[];
  sets: CertSet[];
  /** Envíos capturados que aún no se atribuyeron a un set. */
  unassigned: CertSubmission[];
}

export interface CertNote {
  id: number;
  set_id: number;
  author: string;
  text: string;
  created_at: string;
}

export interface CertDefinition {
  set_id: number;
  endpoint: string;
  payload: Record<string, unknown>;
  updated_at: string;
}

export interface CertEnvelope {
  submission_id: number;
  track_id: string;
  filename: string;
  xml_base64: string;
}

/** Qué se va a emitir, legible. `note` avisa de que la suma de líneas no es el
 *  total del documento: ese lo calcula el motor y se ve tras emitir. */
export interface CertPreview {
  /** Qué completó el sistema o qué falta para poder hacerlo. */
  system_notes?: string[];
  kind: string;
  summary: string;
  detail: string;
  note: string;
  documents: Record<string, unknown>[];
}

/** Qué contiene de verdad un sobre emitido, leído de su XML firmado. */
export interface CertContents {
  submission_id: number;
  track_id: string | null;
  documents: Record<string, string | number | null>[];
}

/** Una comprobación de la verificación previa: qué pasa y qué hacer. */
export interface CertCheck {
  key: string;
  label: string;
  state: "ok" | "atencion" | "error";
  detail: string;
  fix: string;
}

export interface CertCheckGroup {
  key: string;
  label: string;
  state: "ok" | "atencion" | "error";
  checks: CertCheck[];
}

/** Si el cliente puede emitir sus sets, y si no, qué falta. */
export interface CertReadiness {
  ready: boolean;
  errors: number;
  warnings: number;
  checked_at: string;
  groups: CertCheckGroup[];
}

/** Un cliente real del contribuyente que recibe documentos del set de pruebas. */
export interface CertReceiver {
  rut: string;
  business_name: string;
  activity: string;
  address: string;
  commune: string;
  city: string;
}

/** Un archivo del SII para subir: nombre y contenido en base64. */
export interface CertSheetFile {
  name: string;
  content_base64: string;
}

/** Lo que se leyó de los archivos del set de pruebas del SII. */
export interface CertSheet {
  sets: { kind: string; code: string; items: number }[];
  /** Texto de la hoja que no se interpreta, para que alguien lo lea. */
  notes: string[];
  loaded: boolean;
}

/** Un set de la plantilla del SII: qué es y si hay que transcribirlo. */
export interface CertTemplateSet {
  kind: string;
  label: string;
  endpoint: string;
  help: string;
  /** Falso en los libros cuyas líneas arma el sistema: sólo piden su número. */
  transcribe: boolean;
}

/** Lo que el SII dice de UN documento del sobre. */
export interface CertDocStatus {
  doc_type: number;
  folio: number;
  status: string;
  label: string;
  /** Glosa del reparo o del rechazo: el dato que el recuento no da. */
  error_label: string;
}
