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

export interface Customer {
  id: number;
  name: string;
  key: string;
  rut: string;
  environment: string;
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
