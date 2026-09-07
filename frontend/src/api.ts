import type {
  AdminAudit,
  BheResponse,
  CafInfo,
  CertificateInfo,
  Customer,
  GrantedService,
  Me,
  RcvResponse,
  RequestLog,
  ServiceGrantResult,
  ServiceInfo,
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
class ApiError extends Error {}

/** Saca el mensaje del cuerpo, venga del handler de errores o de FastAPI. */
async function mensajeDe(res: Response, porDefecto: string): Promise<string> {
  const body = await res.json().catch(() => ({}) as Record<string, unknown>);
  return (
    (body as { error?: { message?: string } }).error?.message ??
    (body as { detail?: string }).detail ??
    porDefecto
  );
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
    throw new ApiError(await mensajeDe(res, "no autenticado"));
  }
  if (!res.ok) {
    throw new ApiError(await mensajeDe(res, `HTTP ${res.status}`));
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
  totpActivate: (code: string) =>
    req<{ recovery_codes: string[] }>("/auth/totp/activate", body({ code })),
  totpDisable: (password: string) => req<void>("/auth/totp/disable", body({ password })),
  resetUserTotp: (id: number) => req<User>(`/users/${id}/totp/reset`, { method: "POST" }),
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
