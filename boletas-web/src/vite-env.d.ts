/// <reference types="vite/client" />

/** Variables de configuración del despliegue (se fijan al construir la imagen). */
interface ImportMetaEnv {
  /** RUT del emisor cuyas boletas publica este sitio. */
  readonly VITE_ISSUER_RUT?: string;
  /** Base del API público; por defecto `/api`, que nginx redirige al servicio. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
