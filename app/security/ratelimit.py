"""Rate limiting por ventana deslizante (anti fuerza bruta y cuotas).

Dos implementaciones tras el mismo contrato, y ``make_limiter`` elige:

- **En memoria** (por omisión). Estado por proceso: con N workers el límite
  efectivo es ~N x. Sirve de freno de fuerza bruta, no de cuota exacta.
- **En Redis**, si se configura ``DTE_REDIS_URL``. El estado es compartido, así
  que el límite vale para todo el despliegue, réplicas incluidas.

Nota: la llave es la IP del peer directo (``request.client.host``). Detrás de
un proxy, arrancar uvicorn con ``--proxy-headers`` para que sea la IP real.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

# Cota de llaves retenidas (IPs); al superarla se purgan las colas vacías.
_MAX_KEYS = 10_000


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_s: float) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._events.setdefault(key, deque())
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        return q

    def hit(self, key: str) -> bool:
        """Registra un intento y devuelve True si el límite ya estaba excedido."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            if len(q) >= self.max_events:
                return True
            q.append(now)
            return False

    def is_limited(self, key: str) -> bool:
        """Consulta sin registrar (para bloquear antes de hacer trabajo)."""
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.max_events

    def record(self, key: str) -> None:
        """Registra un evento (p. ej. un fallo de autenticación)."""
        now = time.monotonic()
        with self._lock:
            self._prune(key, now).append(now)
            if len(self._events) > _MAX_KEYS:
                self._events = {k: q for k, q in self._events.items() if q}

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


def make_limiter(name: str, max_events: int, window_s: float):
    """Devuelve el limitador configurado. ``name`` separa los espacios de llaves.

    Sin ``DTE_REDIS_URL`` no se importa el cliente de Redis siquiera: el modo por
    omisión no arrastra la dependencia. Si la URL está pero la conexión falla, se
    avisa y se sigue en memoria — arrancar sin límite exacto es preferible a no
    arrancar, y el freno por proceso sigue puesto.
    """
    from app.core.config import get_settings

    url = get_settings().redis_url
    if not url:
        return SlidingWindowLimiter(max_events, window_s)
    try:
        import redis

        from app.security.redis_ratelimit import RedisSlidingWindow

        client = redis.Redis.from_url(url, decode_responses=True)
        client.ping()
        return RedisSlidingWindow(client, name, max_events, window_s)
    except Exception:
        logger.warning(
            "DTE_REDIS_URL configurada pero Redis no responde: el límite '%s' "
            "queda en memoria (por proceso)",
            name,
            exc_info=True,
        )
        return SlidingWindowLimiter(max_events, window_s)
