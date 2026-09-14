"""Configuración del servicio (variables de entorno con prefijo ``DTE_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DTE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://dte:dte@localhost:5432/dte_service"
    # Pool de conexiones por worker. Dimensionar según workers x concurrencia.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Llaves Fernet coma-separadas (la primera cifra; todas descifran → rotación).
    fernet_keys: str = ""
    schemas_dir: str = "schemas"
    admin_api_key: str = "change-me"
    # La clave de bootstrap (entorno) tiene poder total sobre todos los clientes
    # y no es revocable ni deja identidad en la auditoría. Apagarla en cuanto
    # existan MachineKey, que sí son revocables y tienen rol propio.
    admin_bootstrap_key_enabled: bool = True
    request_timeout_s: int = 60
    log_level: str = "INFO"
    # Orígenes permitidos para la SPA (coma-separados), con esquema. Vacío = sin
    # CORS, que es lo normal: portal y API se sirven en el mismo sitio.
    cors_origins: str = ""

    # --- Rate limiting ---
    # Vacío = estado en memoria de cada proceso (con N workers el límite
    # efectivo es ~N x). Con una URL de Redis el estado se comparte y el
    # límite vale para todo el despliegue, réplicas incluidas.
    redis_url: str = ""
    login_attempts_per_minute: int = 10
    # Fallos de X-Admin-Key por IP. Más estrecho que el de clientes: es la
    # credencial con escritura sobre TODOS los clientes y nadie la teclea.
    admin_key_failures_per_5min: int = 10
    # Consulta pública de boletas: holgado para el comprador, estrecho para
    # quien quiera tantear montos por fuerza bruta.
    public_lookup_per_minute: int = 20
    # Sitio que se imprime en la boleta para que el consumidor la consulte.
    receipt_verification_url: str = ""
    tenant_auth_failures_per_5min: int = 30
    # Cuota de un cliente YA autenticado. Sin esto, el único freno es sobre
    # fallos de autenticación y un cliente puede acaparar el servicio: firmar
    # y hablar con el SII son caros y los paga todo el mundo. 0 = sin cuota.
    customer_requests_per_minute: int = 120

    # --- Portal (JWT + cookie) ---
    jwt_secret: str = "change-me-jwt"
    jwt_expire_minutes: int = 120  # 2 horas (la cookie HttpOnly reduce el riesgo de robo)
    # Cookie de sesión del portal: Secure exige HTTPS. En dev (http) poner false.
    cookie_secure: bool = True
    # Superadmin sembrado al arranque (idempotente) → nunca perder administración.
    superadmin_email: str = ""
    superadmin_password: str = ""

    @model_validator(mode="after")
    def _check_secrets(self) -> Settings:
        """Fail-fast: el servicio no arranca con los secretos de ejemplo.

        Un default funcional ("change-me") convierte una variable olvidada en
        compromiso total (JWT forjable / escritura admin abierta).
        """
        if self.jwt_secret.startswith("change-me") or len(self.jwt_secret) < 32:
            raise ValueError(
                "DTE_JWT_SECRET debe configurarse con un valor aleatorio de >=32 caracteres"
            )
        if self.admin_bootstrap_key_enabled and (
            self.admin_api_key.startswith("change-me") or len(self.admin_api_key) < 16
        ):
            raise ValueError(
                "DTE_ADMIN_API_KEY debe configurarse con un valor aleatorio de >=16 caracteres"
                " (o apagarse con DTE_ADMIN_BOOTSTRAP_KEY_ENABLED=false)"
            )
        # El comodín con allow_credentials=True es la combinación prohibida: el
        # navegador se niega a usarla, así que la SPA dejaría de funcionar y
        # alguien "arreglaría" el CORS quitando las credenciales. Y un origen sin
        # esquema no casa nunca con el header Origin, que siempre lo trae: falla
        # en silencio y cuesta horas de depuración.
        for origin in self.cors_origin_list:
            if origin == "*":
                raise ValueError(
                    "DTE_CORS_ORIGINS no acepta '*': el API se sirve con"
                    " allow_credentials=True. Enumera los orígenes de la SPA."
                )
            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    f"DTE_CORS_ORIGINS: '{origin}' debe incluir el esquema"
                    " (https://dte.dimabe.cl), que es como llega el header Origin"
                )
        return self

    @property
    def fernet_key_list(self) -> list[str]:
        return [k.strip() for k in self.fernet_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
