# Builds the ONE Vite project under frontend/ (it serves both the patient
# app and the admin app -- frontend/src/main.tsx picks between them at
# runtime by pathname, see its own comment; there is no separate admin
# build) and serves the resulting static files, reverse-proxying /api and
# /media to the app container so the whole deployment is same-origin from
# the browser's point of view. See ../../docker-compose.yml for how the
# two containers are wired together.

FROM node:22-alpine AS build
WORKDIR /frontend

# Dependencies in their own layer first, same cache-friendliness reason
# as the app Dockerfile.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /frontend/dist /usr/share/nginx/html
COPY docker/nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
