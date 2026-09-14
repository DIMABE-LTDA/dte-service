"""Cabeceras de seguridad para toda respuesta del API.

``frame-ancestors 'none'`` (con ``X-Frame-Options`` para navegadores viejos) es
lo que importa: sin ellas el portal se puede embeber en un iframe ajeno y ahí
hay acciones destructivas (retirar CAF, eliminar cliente) expuestas a
clickjacking. La CSP se limita a frame-ancestors a propósito: una
``default-src`` rompería el Swagger de /docs, que carga sus scripts de un CDN.

Middleware ASGI puro, no ``BaseHTTPMiddleware``: esa clase envuelve cada
request en un task group y mueve el cierre de las dependencias, que es mucho
más de lo que hace falta para agregar cuatro cabeceras a la respuesta.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = (
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("content-security-policy", "frame-ancestors 'none'"),
    ("referrer-policy", "no-referrer"),
)
# Sin ``preload``: entrar a esa lista de navegadores es difícil de revertir.
HSTS = "max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        """``hsts``: sólo donde hay TLS delante. En dev (http) fijaría el
        navegador a https contra localhost."""
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _HEADERS:
                    headers.setdefault(name, value)
                if self.hsts:
                    headers.setdefault("strict-transport-security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
