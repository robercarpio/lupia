# Backend · Ventas y anomalías BK España (IndesIAhack 2026 · RBE)

API en **FastAPI** que conecta la capa Gold de Databricks (`dat_hack_rb06.rbe`) con el frontend (`../frontend`) y con el copiloto (chat y voz, **Azure OpenAI**).
Es el único backend del proyecto: el frontend no habla nunca con Databricks ni recibe credenciales.

Análisis completo del frontend, del esquema de Databricks y de las decisiones: [`../BACKEND_ANALYSIS.md`](../BACKEND_ANALYSIS.md).

## Estructura

```
backend/
├── app.yaml                 # Databricks Apps: uvicorn en el puerto 8000 + variables de entorno
├── requirements.txt         # dependencias de ejecución (versiones fijadas) · requirements-dev.txt (+ pytest, httpx)
├── .env.example             # variables para desarrollo local (sin secretos)
├── app/
│   ├── main.py              # FastAPI: routers, CORS, GZip, errores JSON, frontend estático opcional
│   ├── config.py            # variables de entorno (catálogo/esquema y HTTP path validados)
│   ├── database.py          # conexión (token / perfil OAuth / Service Principal), pool, timeouts, solo lectura, caché TTL
│   ├── schemas.py           # modelos Pydantic de entrada/salida
│   ├── routers/             # HTTP: meta · kpis · sales · alerts · episodes · series · restaurants · export · chat
│   └── services/
│       ├── repository.py    # TODO el SQL, parametrizado (ventas agregadas en Databricks, alertas, vistas, huella)
│       ├── dataset.py       # snapshot en memoria de alertas + maestro; se recarga solo si el modelo escribe datos
│       ├── sales.py         # ventas reales: KPIs, canales, ranking, geografía, desgloses, comparativas
│       ├── analytics.py     # anomalías: KPIs, timeline, canales, geo, ranking, tabla, detalle
│       ├── episodes.py      # episodios multi-día y series con bandas P05/P95
│       ├── narrative.py     # explicaciones deterministas (sin causas inventadas)
│       ├── geo.py           # slugs de la BD ('pais-vasco') ↔ nombres del mapa ('País Vasco')
│       └── copilot.py       # Azure OpenAI + 15 herramientas cerradas + memoria controlada + modo básico
└── tests/                   # 40 tests con un repositorio falso (sin red)
```

## Puesta en marcha (local)

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # rellena DATABRICKS_HTTP_PATH y, si queréis el copiloto completo, Azure OpenAI
databricks auth login --host https://<workspace>.azuredatabricks.net   # una vez (o DATABRICKS_TOKEN en .env)
.venv/bin/uvicorn app.main:app --reload --port 8787
```

- Documentación interactiva (OpenAPI/Swagger): http://localhost:8787/docs
- Frontend en desarrollo: `cd ../frontend && npm run dev` (Vite redirige `/api` a `:8787`).
- Tests: `.venv/bin/python -m pytest -q`

### Variables de entorno

| Variable | Obligatoria | Para qué |
|---|---|---|
| `DATABRICKS_HOST` | sí, salvo que el perfil del CLI la aporte | URL del workspace |
| `DATABRICKS_HTTP_PATH` | sí (o `DATABRICKS_WAREHOUSE_ID`) | SQL Warehouse, `/sql/1.0/warehouses/<id>` |
| `DATABRICKS_TOKEN` | no | token personal. Sin él se usa OAuth (`databricks auth login`) o el Service Principal de Databricks Apps. Nunca se registra en logs |
| `DATABRICKS_CATALOG` + `DATABRICKS_SCHEMA` | no (por defecto `dat_hack_rb06` + `rbe`) | capa Gold. Equivalente: `RBE_SCHEMA=catalogo.esquema` |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` | para el copiloto completo | sin ellas, chat y voz funcionan en **modo básico** (respuestas deterministas con datos) |
| `CACHE_TTL_SECONDS` | no (900) | caché de consultas de ventas |
| `CACHE_STALE_SECONDS` | no (21600) | ventana en la que un resultado caducado se sirve al instante y se refresca detrás |
| `WARM_UP_ON_START` | no (1) | precalienta conexiones, snapshot y dashboard por defecto al arrancar |
| `ANOMALY_REFRESH_SECONDS` | no (60) | cada cuánto se comprueba si hay anomalías nuevas |
| `SQL_STATEMENT_TIMEOUT_SECONDS` | no (120) | límite de coste por consulta en el warehouse |
| `CORS_ORIGINS`, `FRONTEND_DIST`, `RBE_PCT_UNITS`, `SQL_POOL_SIZE` | no | ver `.env.example` |

## Despliegue en Databricks Apps

1. Frontend en la misma app (sin CORS): `cd ../frontend && npm run build:backend` (copia `dist` a `backend/static`).
2. Crea la app y declara los recursos de `app.yaml`: `sql-warehouse` (Can use) y los secretos `azure-openai-endpoint`, `azure-openai-key` y `azure-openai-deployment`.
3. Permisos de lectura para el Service Principal de la app:
   ```sql
   GRANT USE CATALOG ON CATALOG dat_hack_rb06 TO `<client_id_de_la_app>`;
   GRANT USE SCHEMA, SELECT ON SCHEMA dat_hack_rb06.rbe TO `<client_id_de_la_app>`;
   ```
4. `databricks sync . /Workspace/Users/<usuario>/rbe-backend` y `databricks apps deploy <app> --source-code-path /Workspace/Users/<usuario>/rbe-backend`.

## Endpoints

**Filtros (todos los listados).** Se aceptan como `?f={"from":"2026-07-01","to":"2026-09-30","canales":["sala"],"ccaa":"País Vasco"}` (lo que envía el frontend) o como parámetros sueltos: `from`, `to`, `canal` (repetible), `restaurante` (`R001` o `1`), `ccaa`, `provincia` (nombre con o sin tildes, o slug), `tipologia`, `ubicacion`, `tipo`, `prioridad` (alta/media/baja o P1/P2/P3), `min_score`, `min_impact`.
- Sin fechas, se usan los últimos 90 días con datos. Las fechas se ajustan al rango disponible y el ajuste se avisa en `warnings`.
- Los filtros **tipo, score, impacto y prioridad** solo afectan a las anomalías, no a las ventas.

### Ventas (`series_summary`, agregado en Databricks)
| Método | Ruta | Devuelve |
|---|---|---|
| GET | `/api/sales/summary` | `ventas, tickets, ticket_medio, dias, venta_media_dia, restaurantes, mejor_dia{fecha,ventas}, prev{…}, prevRange{from,to}, variacion{ventas,tickets,ticket_medio,…}, spark{ventas,tickets,ticket_medio}, baseline` |
| GET | `/api/sales/channels` | `rows[{canal, ventas, tickets, ticket_medio, share, ventas_prev, variacion, …}]` |
| GET | `/api/sales/ranking?sort=ventas\|ventas_asc\|tickets\|ticket_medio\|variacion\|variacion_asc&limit=1..200` | `total, prevRange, rows[{restaurante_id, provincia, ccaa, ventas, tickets, ticket_medio, ventas_prev, variacion, dias}]` |
| GET | `/api/sales/breakdown/{canal\|comunidad\|provincia\|tipologia\|ubicacion\|dia_semana\|mes}` | `rows[{clave, ventas, tickets, share, variacion, …}]` |
| GET | `/api/sales/compare?ids=R001&ids=R002` | `rows[{restaurante_id, ventas_periodo{…}, anomalias{total,caidas,subidas,impacto}}], prevRange` |
| GET | `/api/sales/compare-periods?a_from&a_to&b_from&b_to` | `a{…}, b{…}, variacion{…}` |

La variación se calcula siempre frente al **periodo anterior de igual duración**. Si ese periodo cae fuera de los datos, `prev`/`variacion` son `null`, nunca cero.

### Dashboard, anomalías y metadatos
| Método | Ruta | Para qué |
|---|---|---|
| GET | `/api/health` | estado de datos, warehouse e IA |
| GET | `/api/meta` | rango de fechas, dimensiones, `modelStatus`, `modelRun`, `config` (parámetros publicados por el modelo), motivos de exclusión |
| GET | `/api/status` | `version` de los datos del modelo (el frontend la consulta cada 60 s) |
| GET | `/api/filters` | opciones de los selectores |
| GET | `/api/dashboard` | KPIs de anomalías, `timeline` (`ventas_totales`, `tickets_totales`, `esperadas`, `baseline`), canales, geo y **`sales`** {kpis, channels, geo}, `modelStatus` |
| GET | `/api/anomalies` | alertas diarias (`q`, `sort`, `dir`, `page`, `size`) |
| GET | `/api/anomalies/{R001-sala-2026-09-01}` | detalle completo |
| GET | `/api/episodes` · `/api/episodes/{id}` | episodios multi-día (`v_alert_episodes`) |
| GET | `/api/series?restaurante=R001&canal=sala&from=&to=` | serie diaria con bandas P05/P95 y marcadores |
| GET | `/api/timeline` · `/api/breakdown/{dim}` · `/api/day-context/{fecha}` | agregados de anomalías |
| GET | `/api/restaurants` · `/catalog` · `/{id}` | ranking por anomalías, maestro, ficha (con ventas del periodo) |
| GET | `/api/kpis` | `v_kpi_global` + ejecución activa |
| GET | `/api/anomalias.csv` · `/api/export.csv` | entrega oficial (`v_anomalias_export`) · alertas filtradas |
| GET | `/api/model-runs` · `/active` | trazabilidad |
| POST | `/api/cache/clear` | invalida cachés (no hace falta tras una ejecución del modelo: se detecta sola) |
| POST | `/api/chat` | copiloto con streaming SSE (texto y voz) |
| POST | `/api/copilot/chat` | `{question, episode_id?, anomaly_id?, restaurante_id?, filters?}` → `{answer, source, context, warnings}` |

### Estados de respuesta
| Situación | Respuesta |
|---|---|
| Datos disponibles | 200 con filas |
| Consulta correcta sin coincidencias (filtros) | 200, `total: 0` / `rows: []` |
| Tablas del modelo vacías | 200 con `modelStatus.anomalias = "pendiente"` (o `"sin_alertas"` si hay ejecución pero no alertas) y `baseline = "pendiente"`. El frontend muestra estados vacíos informativos |
| Parámetro no válido | 400 `BAD_REQUEST` |
| No existe (restaurante, anomalía, episodio) | 404 `NOT_FOUND` |
| Warehouse arrancando / inalcanzable | 503 `WAREHOUSE_STARTING` |
| Falta configuración de conexión | 503 `NOT_CONFIGURED` |
| `series_summary` vacía | 503 `NO_DATA` |
| Error de consulta en Databricks | 500 `DB_ERROR`: nunca se devuelve como lista vacía |

Formato de error: siempre `{"error": "...", "code": ...}`.

## Rendimiento

- **Precalentado al arrancar:** abre conexiones, carga el snapshot y calcula el dashboard por defecto y los rankings. Tarda unos 8 s tras arrancar (casi todo es abrir la sesión con Databricks) y despierta el warehouse serverless si estaba parado.
- **Menos consultas, en paralelo:**
  - El dashboard hace 4 consultas simultáneas. Una sola serie diaria cubre el periodo actual y el anterior, y la comparten los KPIs, las sparklines y el gráfico temporal. Comunidad y provincia salen de la misma consulta.
  - El snapshot hace 3 bloques en paralelo.
  - El ranking hace 1 consulta, con el total calculado mediante `count(*) OVER ()`.
- **Caché stale-while-revalidate:** dentro de `CACHE_TTL_SECONDS` se sirve tal cual. Después, y hasta `CACHE_STALE_SECONDS`, se sirve al instante y se recalcula en segundo plano. Las anomalías nuevas invalidan todo.
- **Refresco del snapshot sin bloqueo:** la huella del modelo se comprueba en segundo plano y solo cuando hay peticiones, para que el warehouse pueda apagarse si nadie usa la app.
- **`/api/health`** guarda el estado del warehouse 30 s. Los ficheros `/assets/*` del build se sirven con caché inmutable.
- **Medido contra Databricks:**

  | Situación | Antes | Ahora |
  |---|---|---|
  | `/api/meta` en frío | 16,4 s | 0,04 s tras el precalentado (7,3 s sin él) |
  | `/api/dashboard` en frío | 6,9 s | 0,04 s tras el precalentado (0,9 s sin él) |
  | Filtro nuevo, sin caché | 5–7 s | ≈ 1,2 s |
  | Dashboard completo en el navegador | — | 1,7 s con `npm run dev`, 0,8 s con el build de producción |

- **Lo que no depende del código:** si el warehouse serverless se ha apagado por inactividad, la primera consulta tarda lo que tarde en arrancar (unos 5–20 s). Se ajusta en Databricks → SQL Warehouses → *Auto stop* (más minutos = menos esperas, más coste).

## Anomalías: integración con el modelo externo

- Las anomalías se leen de `alerts_prioritized` (ejecución activa de `model_runs`). No hay listas estáticas ni datos de ejemplo.
- **Detección automática:** cada `ANOMALY_REFRESH_SECONDS` el backend consulta una huella barata: `count(*)` y `max(created_at)` de `alerts_prioritized` y `model_runs`. Si cambia, invalida las cachés y recarga. El frontend pregunta `/api/status` cada 60 s y, si cambia la versión, vuelve a pedir todo. Un registro nuevo aparece en ≤ ~2 min, sin desplegar ni tocar código. **No es tiempo real.**
- Contrato que espera el backend:
  - Columnas `*_pct` en puntos porcentuales (si no, `RBE_PCT_UNITS=fraction`).
  - `exclusion_reason` con las claves `festivo`, `vispera_festivo`, `fecha_especial`, `navidad`, `semana_santa`, `apertura`, `cierre`, `reforma`, `canal_pequeno`, `sin_historico`, `fuera_periodo`.
  - `evidencia_json` opcional (`resumen`, `hechos`, `contexto`, `hipotesis`, `recomendacion`, `otros_canales`).
  - `params_json` con los parámetros que quiera mostrar el modelo.

## Copiloto (chat y voz)

- **Flujo:**
  1. El frontend envía la pregunta, los filtros del dashboard y la anomalía en foco.
  2. Azure OpenAI elige entre **15 herramientas cerradas** con parámetros validados:
     - ventas: `resumen_ventas`, `ranking_ventas`, `desglose_ventas`, `comparar_restaurantes`, `serie_temporal`, `comparar_periodos`;
     - anomalías: `resumen_anomalias`, `listar_anomalias`, `listar_episodios`, `ranking_restaurantes`, `desglose`, `detalle_anomalia`, `contexto_dia`;
     - otras: `info_restaurante`, `aplicar_filtros_dashboard`.
  3. Las herramientas son las mismas funciones del dashboard, así que las cifras coinciden siempre.
  4. El modelo responde con streaming y bloques visuales.
- **Seguridad:** el modelo no escribe SQL. Toda consulta es parametrizada, la capa de datos rechaza cualquier sentencia que no sea de lectura y cada consulta tiene un tiempo máximo (`STATEMENT_TIMEOUT`). Los resultados están limitados a 25 filas y a 14 KB por herramienta.
- **Reglas del prompt:**
  - No inventar cifras ni causas externas.
  - Indicar siempre el periodo exacto.
  - Distinguir los datos reales de las estimaciones del modelo.
  - Pedir aclaración si la pregunta es ambigua.
  - Avisar de que las tablas de anomalías están vacías, en vez de decir que «no hay anomalías».
- **Contexto controlado:**
  - Como mucho, los últimos 8 turnos.
  - Los resultados de herramientas de turnos antiguos se resumen.
  - Hay una memoria corta (últimos restaurantes y periodo consultados) para seguimientos del tipo «¿y respecto al mes anterior?».
- **Voz:** el frontend transcribe con la Web Speech API (navegador) y llama al mismo `POST /api/chat` con `voice: true`; la respuesta se lee con `speechSynthesis`. Mismos servicios y mismos datos. No necesita credenciales de voz aparte (Chrome, Edge o Safari de escritorio).
- **Sin Azure OpenAI** (o si falla): **modo básico** determinista. Responde con datos reales a preguntas de ventas, de restaurantes y de anomalías concretas, y lo indica en la respuesta. No interpreta fechas libres: usa los filtros del dashboard.
