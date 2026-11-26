# Ventas y anomalías BK España

Dashboard de ventas y anomalías con asistente de IA.

| Carpeta | Contenido |
|---|---|
| [`frontend/`](frontend) | React 18 + Vite. Dashboard y AI Assistant ([README](frontend/README.md)) |
| [`backend/`](backend) | FastAPI conectado a Databricks; desplegado como Databricks App «backendoficial» ([README](backend/README.md)) |
| [`sql/`](sql) | Esquema SQL |
| [`databricks/`](databricks) | Dependencias para Databricks |

## Cómo arrancar la página

Hay dos opciones: usar el backend ya desplegado en Databricks (**A**, recomendada) o arrancar el backend en local (**B**).

### Requisitos (una vez)

- Node.js 18+ y `npm`.
- [CLI de Databricks](https://docs.databricks.com/dev-tools/cli/install.html).
- Dependencias del frontend:
  ```bash
  cd frontend
  npm install
  ```

### Opción A: frontend + backend desplegado en Databricks (recomendada)

No hace falta arrancar ningún backend en local.

1. Inicia sesión en Databricks (cuando caduque la sesión, repítelo):
   ```bash
   databricks auth login -p DEFAULT
   ```
2. Crea `frontend/.env` con estas líneas (están comentadas en `frontend/.env.example`):
   ```
   VITE_BACKEND_URL=https://backendoficial-7405605482140954.14.azure.databricksapps.com
   DATABRICKS_CONFIG_PROFILE=DEFAULT
   ```
3. Arranca el frontend:
   ```bash
   cd frontend
   npm run dev
   ```
   Al arrancar debe aparecer `➜  API: https://backendoficial-... (token OAuth del CLI de Databricks)`.
4. Abre **http://localhost:5173/**.

El proxy de Vite añade a cada petición `/api` el token OAuth que obtiene de `databricks auth token`; el token nunca llega al navegador.

### Opción B: frontend + backend local

Útil si estás cambiando el código del backend.

1. Prepara el backend (una vez):
   ```bash
   cd backend
   python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
   cp .env.example .env   # rellena al menos DATABRICKS_HTTP_PATH (y Azure OpenAI si quieres el copiloto completo)
   ```
2. Arranca el backend:
   ```bash
   .venv/bin/uvicorn app.main:app --reload --port 8787
   ```
3. En `frontend/.env`, comenta o borra `VITE_BACKEND_URL` (sin ella, el proxy va a `http://localhost:8787`).
4. En otra terminal: `cd frontend && npm run dev` y abre **http://localhost:5173/**.

### Problemas típicos

| Síntoma | Solución |
|---|---|
| «Sin sesión de Databricks» | `databricks auth login -p DEFAULT` y recarga la página |
| «El backend no responde» | A: revisa `frontend/.env` · B: arranca uvicorn en el puerto 8787 |
| Los cambios en `.env` no se aplican | Para `npm run dev` (Ctrl+C) y vuelve a lanzarlo |
| Puerto 5173 ocupado | Vite usa otro (5174…); mira la URL que imprime |
