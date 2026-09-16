import type {
  AdminAudit,
  BheResponse,
  CafInfo,
  CertificateInfo,
  Customer,
  GrantedService,
  IssuerProfile,
  Me,
  RcvResponse,
  RequestLog,
  ServiceGrantResult,
  ServiceInfo,
  CertCheck,
  CertContents,
  CertDefinition,
  CertCustomer,
  CertDocStatus,
  CertDossier,
  CertEnvelope,
  CertNote,
  CertPreview,
  CertReceiver,
  CertReadiness,
  CertSet,
  CertSheet,
  CertSheetFile,
  CertSubmission,
  CertTemplateSet,
  Token,
  TotpSetup,
  TotpStatus,
  User,
} from "./types";

// El navegador habla con el API bajo /api (mismo sitio): así las rutas de
// navegación del SPA (/users, /audit) no colisionan con los endpoints del API.
// dev → proxy de Vite; prod → nginx. Override con VITE_API_BASE si hace falta.
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

// La sesión vive en una cookie HttpOnly que pone el servidor; JS no la maneja.
// `credentials: "include"` hace que el navegador la envíe en cada request.
/** Error del API, con la guía que el servidor haya adjuntado.
 *
 * `hints` es el `details` del cuerpo de error: qué revisar y en qué orden.
 * Existe porque hay fallos cuyo mensaje es exacto pero inútil —"el SII rechazó
 * la semilla firmada (estado=10)"— y la causa real está siempre en la misma
 * lista corta de sitios. Perderla al cruzar la frontera HTTP dejaba al
 * operador con un número.
 */
export class ApiError extends Error {
  readonly hints: string[];

  constructor(message: string, hints: string[] = []) {
    super(message);
    this.hints = hints;
  }
}

/** Saca el mensaje y la guía del cuerpo, vengan del handler o de FastAPI. */
async function errorDe(res: Response, porDefecto: string): Promise<ApiError> {
  const body = await res.json().catch(() => ({}) as Record<string, unknown>);
  const err = (body as { error?: { message?: string; details?: string[] } }).error;
  const mensaje = err?.message ?? (body as { detail?: string }).detail ?? porDefecto;
  return new ApiError(mensaje, err?.details ?? []);
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const res = await fetch(`${BASE}${path}`, { ...opts, headers, credentials: "include" });
  if (res.status === 401) {
    // /auth/* (me, login, logout) gestionan su propio estado: no redirigir aquí.
    if (!path.startsWith("/auth/")) {
      window.location.assign("/login");
      throw new ApiError("no autenticado");
    }
    // Y NECESITAN el detalle: el login distingue "falta el segundo factor"
    // ('totp_required') de una contraseña incorrecta, y con un mensaje genérico
    // nunca llegaría a pedir el código.
    throw await errorDe(res, "no autenticado");
  }
  if (!res.ok) {
    throw await errorDe(res, `HTTP ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function body(data: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(data) };
}

export const api = {
  login: (
    email: string,
    password: string,
    second?: { totp_code?: string; recovery_code?: string },
  ) => req<Token>("/auth/login", body({ email, password, ...second })),
  totpStatus: () => req<TotpStatus>("/auth/totp"),
  totpSetup: () => req<TotpSetup>("/auth/totp/setup", { method: "POST" }),
  totpActivate: (code: string, password: string) =>
    req<{ recovery_codes: string[] }>("/auth/totp/activate", body({ code, password })),
  totpDisable: (password: string) => req<void>("/auth/totp/disable", body({ password })),
  resetUserTotp: (id: number) => req<User>(`/users/${id}/totp/reset`, { method: "POST" }),

  // --- Expediente de certificación ---
  certIndex: () => req<CertCustomer[]>("/admin/certification"),
  certDossier: (cid: number) => req<CertDossier>(`/admin/customers/${cid}/certification`),
  certRefresh: (cid: number, sid: number) =>
    req<CertSubmission>(`/admin/customers/${cid}/certification/submissions/${sid}/refresh`, {
      method: "POST",
    }),
  certFolioReport: (cid: number, sid: number) =>
    req<CertSubmission>(`/admin/customers/${cid}/certification/submissions/${sid}/folio-report`, {
      method: "POST",
    }),
  certAssign: (cid: number, sid: number, code: string, kind: string) =>
    req<CertSubmission>(
      `/admin/customers/${cid}/certification/submissions/${sid}/assign`,
      body({ code, kind }),
    ),
  certDeclare: (cid: number, setId: number, declared_at: string) =>
    req<CertSet>(
      `/admin/customers/${cid}/certification/sets/${setId}/declare`,
      body({ declared_at }),
    ),
  certEnvelope: (cid: number, sid: number) =>
    req<CertEnvelope>(`/admin/customers/${cid}/certification/submissions/${sid}/envelope`),
  certSetup: (cid: number, codes: Record<string, string>) =>
    req<CertDossier>(`/admin/customers/${cid}/certification/setup`, body({ codes })),
  certStep: (cid: number, step: string, done_at: string | null, note: string) =>
    req<CertDossier>(
      `/admin/customers/${cid}/certification/steps/${step}`,
      body({ done_at, note }),
    ),
  certDefinition: (cid: number, setId: number) =>
    req<CertDefinition>(`/admin/customers/${cid}/certification/sets/${setId}/definition`),
  certSaveDefinition: (cid: number, setId: number, endpoint: string, payload: unknown) =>
    req<CertDefinition>(`/admin/customers/${cid}/certification/sets/${setId}/definition`, {
      method: "PUT",
      body: JSON.stringify({ endpoint, payload }),
    }),
  certCloneDefinition: (cid: number, setId: number, from_customer_id: number) =>
    req<CertDefinition>(
      `/admin/customers/${cid}/certification/sets/${setId}/definition/clone`,
      body({ from_customer_id }),
    ),
  certEmit: (cid: number, setId: number, force = false) =>
    req<CertSubmission>(`/admin/customers/${cid}/certification/sets/${setId}/emit?force=${force}`, {
      method: "POST",
    }),
  certDocStatuses: (cid: number, sid: number) =>
    req<CertDocStatus[]>(`/admin/customers/${cid}/certification/submissions/${sid}/documents`, {
      method: "POST",
    }),
  certDiscard: (cid: number, sid: number) =>
    req<CertDossier>(`/admin/customers/${cid}/certification/submissions/${sid}`, {
      method: "DELETE",
    }),
  certSend: (cid: number, sid: number) =>
    req<CertSubmission>(`/admin/customers/${cid}/certification/submissions/${sid}/send`, {
      method: "POST",
    }),
  certImport: (cid: number, sets: Record<string, unknown>) =>
    req<CertDossier>(`/admin/customers/${cid}/certification/import`, body({ sets })),
  certSheet: (cid: number, files: CertSheetFile[], dryRun: boolean) =>
    req<CertSheet>(
      `/admin/customers/${cid}/certification/sheet`,
      body({ files, dry_run: dryRun }),
    ),
  certTemplate: (cid: number) =>
    req<CertTemplateSet[]>(`/admin/customers/${cid}/certification/template`),
  certCreateFromTemplate: (cid: number, codes: Record<string, string>) =>
    req<CertDossier>(`/admin/customers/${cid}/certification/template`, body({ codes })),
  certReceivers: (cid: number) =>
    req<CertReceiver[]>(`/admin/customers/${cid}/certification/receivers`),
  certSaveReceivers: (cid: number, receivers: CertReceiver[]) =>
    req<CertReceiver[]>(`/admin/customers/${cid}/certification/receivers`, {
      method: "PUT",
      body: JSON.stringify({ receivers }),
    }),
  certChecks: (cid: number) => req<CertReadiness>(`/admin/customers/${cid}/certification/checks`),
  certCheckSii: (cid: number) =>
    req<CertCheck>(`/admin/customers/${cid}/certification/checks/sii`, { method: "POST" }),
  certPreview: (cid: number, setId: number) =>
    req<CertPreview>(`/admin/customers/${cid}/certification/sets/${setId}/preview`),
  certContents: (cid: number, sid: number) =>
    req<CertContents>(`/admin/customers/${cid}/certification/submissions/${sid}/contents`),
  certPrintSamples: (cid: number) =>
    req<{ documents: Record<string, unknown>[]; skipped: Record<string, string>[] }>(
      `/admin/customers/${cid}/certification/print-samples`,
      { method: "POST" },
    ),
  certNotes: (cid: number) => req<CertNote[]>(`/admin/customers/${cid}/certification/notes`),
  certAddNote: (cid: number, set_id: number, text: string) =>
    req<CertNote>(`/admin/customers/${cid}/certification/notes`, body({ set_id, text })),
  logout: () => req<void>("/auth/logout", { method: "POST" }),
  me: () => req<Me>("/auth/me"),

  customers: (includeDeleted = false) =>
    req<Customer[]>(`/admin/customers${includeDeleted ? "?include_deleted=true" : ""}`),
  customer: (id: number) => req<Customer>(`/admin/customers/${id}`),
  deleteCustomer: (id: number) => req<Customer>(`/admin/customers/${id}`, { method: "DELETE" }),
  restoreCustomer: (id: number) =>
    req<Customer>(`/admin/customers/${id}/restore`, { method: "POST" }),
  createCustomer: (data: {
    name: string;
    key?: string; // opcional: si se omite, el servidor genera el customerCode
    rut: string;
    environment: string;
    resolution_number?: number;
    resolution_date?: string;
  }) => req<Customer>("/admin/customers", body(data)),
  updateCustomer: (
    id: number,
    data: {
      name?: string;
      rut?: string;
      environment?: string;
      resolution_number?: number;
      resolution_date?: string;
      issuer?: IssuerProfile;
    },
  ) => req<Customer>(`/admin/customers/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  services: () => req<ServiceInfo[]>("/admin/services"),
  customerServices: (id: number) => req<GrantedService[]>(`/admin/customers/${id}/services`),
  customerCerts: (id: number) => req<CertificateInfo[]>(`/admin/customers/${id}/certificates`),
  customerCafs: (id: number) => req<CafInfo[]>(`/admin/customers/${id}/cafs`),
  // apikey opcional: si se omite, el servidor la genera y la devuelve una vez.
  grant: (id: number, service_code: string, apikey?: string) =>
    req<ServiceGrantResult>(`/admin/customers/${id}/services`, body({ service_code, apikey })),
  revokeService: (id: number, code: string) =>
    req(`/admin/customers/${id}/services/${code}`, { method: "DELETE" }),
  uploadCert: (id: number, file_base64: string, password: string) =>
    req(`/admin/customers/${id}/certificate`, body({ file_base64, password })),
  uploadCaf: (id: number, xml_base64: string) =>
    req(`/admin/customers/${id}/caf`, body({ xml_base64 })),
  deleteCertificate: (id: number, certId: number) =>
    req<void>(`/admin/customers/${id}/certificates/${certId}`, { method: "DELETE" }),
  retireCaf: (id: number, cafId: number) =>
    req(`/admin/customers/${id}/cafs/${cafId}/retire`, { method: "POST" }),
  rcv: (id: number, period: string, operation: string) =>
    req<RcvResponse>(`/admin/customers/${id}/rcv`, body({ period, operation })),
  bheReceived: (id: number, period: string) =>
    req<BheResponse>(`/admin/customers/${id}/bhe`, body({ period })),
  siiKeyStatus: (id: number) => req<{ configured: boolean }>(`/admin/customers/${id}/sii-key`),
  setSiiKey: (id: number, password: string) =>
    req<{ customer_id: number; configured: boolean }>(
      `/admin/customers/${id}/sii-key`,
      body({ password }),
    ),
  deleteSiiKey: (id: number) =>
    req<{ configured: boolean }>(`/admin/customers/${id}/sii-key`, { method: "DELETE" }),

  users: (includeDeleted = false) =>
    req<User[]>(`/users${includeDeleted ? "?include_deleted=true" : ""}`),
  createUser: (data: {
    email: string;
    password: string;
    role: string;
    customer_id: number | null;
  }) => req<User>("/users", body(data)),
  setUserActive: (id: number, is_active: boolean) =>
    req<User>(`/users/${id}/active`, { method: "PATCH", body: JSON.stringify({ is_active }) }),
  setUserPassword: (id: number, password: string) =>
    req<User>(`/users/${id}/password`, { method: "PATCH", body: JSON.stringify({ password }) }),
  deleteUser: (id: number) => req<User>(`/users/${id}`, { method: "DELETE" }),
  restoreUser: (id: number) => req<User>(`/users/${id}/restore`, { method: "POST" }),

  auditRequests: (params: Record<string, string>) =>
    req<RequestLog[]>(`/audit/requests?${new URLSearchParams(params)}`),
  auditChanges: () => req<AdminAudit[]>("/audit/changes"),

  async downloadAuditCsv(): Promise<void> {
    const res = await fetch(`${BASE}/audit/requests?format=csv`, {
      credentials: "include",
    });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "request_log.csv";
    a.click();
    URL.revokeObjectURL(url);
  },
};
