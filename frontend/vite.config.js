import { execFile } from 'node:child_process'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// El frontend no habla nunca con Databricks directamente: todas las peticiones /api van al backend.
// Por defecto es el backend local (carpeta ../backend, puerto 8787). Para usar el desplegado como
// Databricks App, define en frontend/.env:
//   VITE_BACKEND_URL=https://backendoficial-7405605482140954.14.azure.databricksapps.com
// y el proxy le añadirá el token OAuth del CLI de Databricks.
const DEFAULT_BACKEND = 'http://localhost:8787'

/**
 * Las Databricks Apps exigen un token OAuth del usuario (sin él responden 401). El proxy de Vite
 * lo pide al CLI de Databricks (`databricks auth token`), lo guarda en memoria y lo renueva antes
 * de que caduque. El token nunca llega al navegador.
 */
function databricksToken(profile) {
  let cached = null
  let pending = null
  return () => {
    if (cached && cached.expires - Date.now() > 5 * 60_000) return Promise.resolve(cached.token)
    if (pending) return pending
    pending = new Promise((resolve, reject) => {
      execFile('databricks', ['auth', 'token', '-p', profile], { timeout: 30_000 }, (err, stdout, stderr) => {
        pending = null
        if (err) return reject(new Error(`databricks auth token -p ${profile} falló: ${(stderr || err.message).trim()}`))
        try {
          const d = JSON.parse(stdout)
          const expires = d.expiry ? Date.parse(d.expiry) : Date.now() + (d.expires_in || 3600) * 1000
          cached = { token: d.access_token, expires }
          resolve(cached.token)
        } catch (e) { reject(e) }
      })
    })
    return pending
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const target = (env.VITE_BACKEND_URL || DEFAULT_BACKEND).replace(/\/$/, '')
  const isDatabricksApp = /\.databricksapps\.com$/.test(new URL(target).hostname)
  const getToken = databricksToken(env.DATABRICKS_CONFIG_PROFILE || 'DEFAULT')

  return {
    plugins: [
      react(),
      // Añade el token OAuth a cada petición /api antes de que el proxy la reenvíe a la Databricks App.
      isDatabricksApp && {
        name: 'databricks-app-auth',
        configureServer(server) {
          server.config.logger.info(`  ➜  API: ${target} (token OAuth del CLI de Databricks)`)
          server.middlewares.use('/api', (req, res, next) => {
            getToken().then((token) => { req.headers.authorization = `Bearer ${token}`; next() }).catch((e) => {
              server.config.logger.error(e.message)
              res.statusCode = 502
              res.setHeader('Content-Type', 'application/json')
              res.end(JSON.stringify({ error: `Sin sesión de Databricks: ejecuta «databricks auth login -p ${env.DATABRICKS_CONFIG_PROFILE || 'DEFAULT'}».`, code: 'NO_AUTH' }))
            })
          })
        },
      },
    ].filter(Boolean),
    server: { proxy: { '/api': { target, changeOrigin: true, secure: true } } },
  }
})
