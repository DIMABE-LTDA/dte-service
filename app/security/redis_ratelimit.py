"""Ventana deslizante compartida en Redis, para límites exactos entre procesos.

El limitador en memoria (``ratelimit.SlidingWindowLimiter``) cuenta por proceso:
con N workers el límite efectivo es ~N x, que sirve como freno de fuerza bruta
pero no como cuota. Con ``DTE_REDIS_URL`` configurado, el estado pasa a Redis y
el límite vale para todo el despliegue, réplicas incluidas.

Es **opcional**: sin esa variable no se importa ni se conecta nada.

Cada llave es un ZSET cuyos miembros son eventos puntuales y cuyo score es el
instante en que ocurrieron. Recortar la ventana es un ZREMRANGEBYSCORE, y contar
un ZCARD. Los tres scripts son Lua para que comprobar-y-registrar sea atómico:
hacerlo en dos viajes deja pasar de más justo cuando hay concurrencia, que es
cuando importa.

Si Redis no responde, se degrada al limitador en memoria en vez de fallar el
request: perder exactitud es preferible a tumbar el servicio, y queda el freno
por proceso, que es lo que había antes.
"""

from __future__ import annotations

import logging
import time
import uuid

from app.security.ratelimit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

# Comprueba y registra en una sola operación. Devuelve 1 si YA estaba excedido
# (y entonces no registra), 0 si registró.
_HIT = """
local key, now, window, maxn = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) >= maxn then return 1 end
redis.call('ZADD', key, now, ARGV[4])
redis.call('PEXPIRE', key, math.ceil(window * 1000))
return 0
"""

# Consulta sin registrar.
_IS_LIMITED = """
local key, now, window, maxn = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) >= maxn then return 1 end
return 0
"""

# Registra un evento (p. ej. un fallo de autenticación) sin comprobar.
_RECORD = """
local key, now, window = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
redis.call('ZADD', key, now, ARGV[3])
redis.call('PEXPIRE', key, math.ceil(window * 1000))
"""


class RedisSlidingWindow:
    """Mismo contrato que ``SlidingWindowLimiter``, con el estado en Redis."""

    def __init__(self, client, name: str, max_events: int, window_s: float) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._name = name
        self._redis = client
        self._fallback = SlidingWindowLimiter(max_events, window_s)
        self._hit = client.register_script(_HIT)
        self._is_limited = client.register_script(_IS_LIMITED)
        self._record = client.register_script(_RECORD)

    def _key(self, key: str) -> str:
        return f"rl:{self._name}:{key}"

    def hit(self, key: str) -> bool:
        try:
            return bool(
                self._hit(
                    keys=[self._key(key)],
                    args=[time.time(), self.window_s, self.max_events, uuid.uuid4().hex],
                )
            )
        except Exception:
            logger.warning("rate limit: Redis no responde, se cuenta por proceso", exc_info=True)
            return self._fallback.hit(key)

    def is_limited(self, key: str) -> bool:
        try:
            return bool(
                self._is_limited(
                    keys=[self._key(key)], args=[time.time(), self.window_s, self.max_events]
                )
            )
        except Exception:
            logger.warning("rate limit: Redis no responde, se cuenta por proceso", exc_info=True)
            return self._fallback.is_limited(key)

    def record(self, key: str) -> None:
        try:
            self._record(keys=[self._key(key)], args=[time.time(), self.window_s, uuid.uuid4().hex])
        except Exception:
            logger.warning("rate limit: Redis no responde, se cuenta por proceso", exc_info=True)
            self._fallback.record(key)

    def reset(self) -> None:
        """Sólo para tests: borra las llaves de este limitador."""
        try:
            keys = list(self._redis.scan_iter(match=f"rl:{self._name}:*"))
            if keys:
                self._redis.delete(*keys)
        except Exception:
            logger.warning("rate limit: no se pudo limpiar Redis", exc_info=True)
        self._fallback.reset()
