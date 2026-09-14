# Imagen Linux para el servicio. En Linux las deps nativas de firma del motor
# (xmlsec/libxml2) son paquetes del sistema.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2-dev libxmlsec1-dev libxmlsec1-openssl pkg-config build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# El motor dte_chile: vendorizar en ./vendor/dte_chile (cp -r ../dte_chile vendor/)
# o instalar desde un índice/git privado y ajustar estas dos líneas.
COPY vendor/dte_chile /opt/dte_chile
RUN pip install /opt/dte_chile

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY migrations ./migrations
RUN pip install .

# Esquemas XSD del SII (del motor) montados o copiados a /srv/schemas.
ENV DTE_SCHEMAS_DIR=/srv/schemas

# Detrás de un proxy, --proxy-headers hace que request.client.host sea la IP
# real del visitante y no la del proxy: de eso depende que el límite de tasa de
# la consulta pública se aplique por comprador y no a todos en un mismo balde.
# Sólo se confía en los proxys de DTE_FORWARDED_ALLOW_IPS; desde cualquier otro
# origen el X-Forwarded-For se ignora (si no, se falsearía para saltar el límite).
ENV DTE_FORWARDED_ALLOW_IPS=127.0.0.1

# Aplica migraciones (Alembic) y arranca.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips \"$DTE_FORWARDED_ALLOW_IPS\""]
