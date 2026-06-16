FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM caddy:2-alpine

# Default to production Caddyfile, override with CADDYFILE env var
ARG CADDYFILE=Caddyfile.prod
COPY ${CADDYFILE} /etc/caddy/Caddyfile
COPY --from=build /app/dist /usr/share/caddy
