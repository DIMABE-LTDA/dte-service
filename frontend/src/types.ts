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
