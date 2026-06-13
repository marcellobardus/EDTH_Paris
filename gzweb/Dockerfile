# Stage 1: build Vite app (gzweb requires Node 24)
FROM node:24-slim AS builder

WORKDIR /app

COPY package.json ./
RUN npm install

COPY index.html ./
COPY src/ ./src/
RUN npx vite build

# Stage 2: pull model meshes from the base image
FROM edth-base:latest AS models

# Stage 3: serve with nginx
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=models /opt/px4-gazebo-models/models /usr/share/nginx/html/opt/px4-gazebo-models/models
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
