# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS frontend-build

WORKDIR /src/frontend/vue3

COPY frontend/vue3/package.json frontend/vue3/package-lock.json ./
RUN npm ci

COPY frontend/vue3/ ./

ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run typecheck && npm run build

FROM nginx:1.28-alpine

COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /src/frontend/vue3/dist/ /usr/share/nginx/html/

EXPOSE 8080

