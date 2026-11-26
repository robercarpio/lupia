# Lupia: frontend (React/Vite) compilado + backend (FastAPI) en una sola imagen.
# Pensado para Render (o cualquier hosting de contenedores). Puerto: $PORT (Render lo define).

# 1) Compilar el frontend
FROM node:20-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2) Backend con el frontend compilado en ./static
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /frontend/dist ./static
ENV FRONTEND_DIST=static PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
