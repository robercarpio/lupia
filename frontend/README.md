# Frontend · Ventas y anomalías BK España

React 18 + Vite. Una sola aplicación con dos modos (selector del sidebar):

- **Dashboard:**
  - KPIs de ventas reales y de anomalías, con comparación frente al periodo anterior.
  - Evolución temporal.
  - Ventas o anomalías por canal.
  - Ranking de restaurantes (por ventas, caída/crecimiento, ticket medio o anomalías).
  - Mapa por provincia.
  - Tabla de anomalías con filtros, orden, paginación y CSV.
  - Detalle de cada anomalía e informe del modelo.
- **AI Assistant:** chat en lenguaje natural sobre los mismos datos, con tablas, gráficos y tarjetas, filtros sincronizados con el dashboard y **modo llamada** por voz.

No contiene backend ni datos: todo llega de la API de [`../backend`](../backend) (FastAPI + Databricks). El navegador nunca habla con Databricks ni recibe credenciales.

## Puesta en marcha

```bash
# 1) Backend (otra terminal): ver ../backend/README.md
cd ../backend && .venv/bin/uvicorn app.main:app --reload --port 8787

# 2) Frontend
cd frontend
npm install
npm run dev            # http://localhost:5173 · Vite redirige /api → http://localhost:8787
```

Variables opcionales (`.env`, ver `.env.example`):
- `VITE_BACKEND_URL`: destino del proxy en desarrollo.
- `VITE_API_BASE`: URL del backend si se publica en otro dominio. En ese caso hay que configurar también `CORS_ORIGINS` en el backend.

Producción en la misma app que el backend: `npm run build:backend` (compila y copia `dist` a `../backend/static`).

## Cómo llegan los datos

- `src/api.js` centraliza toda la comunicación HTTP:
  - `useApi(path)` cancela peticiones obsoletas y mantiene el último dato mientras carga.
  - `streamChat` gestiona el SSE del chat y de la voz.
  - `apiUrl` construye las URL (también las de descarga).
- **Refresco:** `useDataStatus` consulta `/api/status` cada 60 s mientras la pestaña está visible. Cuando el modelo externo escribe anomalías nuevas, la versión cambia y todas las vistas se vuelven a pedir solas. No es tiempo real: el retraso máximo es de unos 2 minutos.
- **Estados:**
  - Carga: esqueletos y opacidad.
  - Error: mensaje del backend, con instrucciones si está apagado, si el warehouse está arrancando o si falta configuración.
  - Vacío: «No hay ventas/anomalías con estos filtros», o bien «El modelo todavía no ha escrito anomalías», según `modelStatus`.
- Los filtros de tipo, score, impacto y prioridad solo afectan a las anomalías; las ventas usan el periodo, el restaurante, los canales y la geografía.

## Modo llamada (voz)

- Captura con `getUserMedia` (con cancelación de eco).
- Voz a texto con la **Web Speech API** (`es-ES`).
- Texto a voz con `speechSynthesis`, frase a frase.
- Si hablas mientras responde, se corta la respuesta.
- El razonamiento es el mismo `POST /api/chat` que el chat de texto (con `voice: true`): mismas herramientas y mismos datos.
- **Requisitos:** Chrome, Edge o Safari de escritorio y permiso de micrófono. Sin Azure OpenAI en el backend, la llamada funciona en modo básico (el indicador lo muestra).

## Diseño

Se conserva el diseño original: tokens, tarjetas, sidebar, topbar, `Panel`, `Delta`, `Sparkline` y `Select`. Los componentes nuevos (`SalesKpis` y los modos de ventas de canal, ranking y mapa) reutilizan esas mismas piezas.
