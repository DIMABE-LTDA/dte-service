# Sitio público de consulta de boletas: build con Node, servido por nginx.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# El RUT del emisor se fija al construir: cada despliegue publica un emisor.
ARG VITE_ISSUER_RUT
ENV VITE_ISSUER_RUT=$VITE_ISSUER_RUT
RUN npm run build

FROM nginx:1.27-alpine
# La plantilla se resuelve al arrancar, no al construir: así la misma imagen
# sirve para apuntar a distintos API sin volver a compilarla.
COPY default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV API_UPSTREAM=http://api:8000
ENV NGINX_ENVSUBST_FILTER=API_UPSTREAM
EXPOSE 80
