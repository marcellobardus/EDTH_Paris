# Stage 1: build Vite app (gzweb requires Node 24)
FROM node:24-slim AS builder

WORKDIR /app

COPY package.json ./
RUN npm install

COPY index.html ./
COPY src/ ./src/
RUN npx vite build

# Stage 2: serve with nginx
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
