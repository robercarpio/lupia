// Cliente de la API del backend (carpeta ../backend, FastAPI conectado a Databricks).
// Toda la comunicación HTTP del frontend pasa por aquí; el navegador nunca habla con Databricks.
import { createContext, useContext, useEffect, useRef, useState } from 'react'

export class ApiError extends Error {
  constructor(message, code, status) { super(message); this.code = code; this.status = status }
}

/** Base del backend: vacía = mismo origen (proxy de Vite en desarrollo o build servido por el backend). */
export const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')
export const apiUrl = (path) => `${API_BASE}${path}`

export const filtersQuery = (f) => `f=${encodeURIComponent(JSON.stringify(f || {}))}`

const OFFLINE_MSG = 'El backend no responde. Arráncalo en la carpeta backend/ con «.venv/bin/uvicorn app.main:app --reload --port 8787».'

export async function getJSON(path, signal) {
  let res
  try {
    res = await fetch(apiUrl(path), { signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError(OFFLINE_MSG, 'OFFLINE')
  }
  const body = await res.json().catch(() => null)
  // Sin cuerpo JSON en un 5xx = el proxy de Vite no llega al backend (no está arrancado).
  if (!res.ok && !body?.error && res.status >= 500) throw new ApiError(OFFLINE_MSG, 'OFFLINE', res.status)
  if (!res.ok) throw new ApiError(body?.error || `Error ${res.status}`, body?.code || res.status, res.status)
  return body
}

export async function postJSON(path, body, signal) {
  const res = await fetch(apiUrl(path), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  return res.json().catch(() => null)
}

/**
 * Versión de los datos del modelo (cambia cuando el modelo externo escribe anomalías nuevas).
 * Todas las llamadas de useApi dependen de ella: al cambiar, se vuelven a pedir solas.
 */
export const DataVersion = createContext('')

/** Carga una ruta y la vuelve a pedir cuando cambian `path`, `retryKey` o la versión de los datos. Mantiene el último dato mientras carga. */
export function useApi(path, retryKey = 0) {
  const version = useContext(DataVersion)
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  useEffect(() => {
    if (!path) return
    const ac = new AbortController()
    setState((s) => ({ ...s, loading: true, error: null }))
    getJSON(path, ac.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => { if (error.name !== 'AbortError') setState((s) => ({ ...s, error, loading: false })) })
    return () => ac.abort()
  }, [path, retryKey, version])
  return state
}

/**
 * Consulta periódica de /api/status (ligera: el backend solo mira una huella de las tablas del modelo).
 * Devuelve la versión vigente y el estado del modelo. No es tiempo real: el retardo máximo es el
 * intervalo de consulta más ANOMALY_REFRESH_SECONDS del backend.
 */
export function useDataStatus(enabled, intervalMs = 60000) {
  const [status, setStatus] = useState(null)
  useEffect(() => {
    if (!enabled) return
    let alive = true
    const tick = () => getJSON('/api/status').then((s) => alive && setStatus(s)).catch(() => {})
    tick()
    const t = setInterval(() => { if (document.visibilityState === 'visible') tick() }, intervalMs)
    return () => { alive = false; clearInterval(t) }
  }, [enabled, intervalMs])
  return status
}

/** Retrasa un valor (p. ej. texto de búsqueda) para no lanzar una petición por tecla. */
export function useDebounced(value, ms = 250) {
  const [v, setV] = useState(value)
  const first = useRef(true)
  useEffect(() => {
    if (first.current) { first.current = false; return }
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** POST /api/chat con respuesta en streaming (Server-Sent Events). Lo usan el chat de texto y la llamada de voz. */
export async function streamChat(body, onEvent, signal) {
  let res
  try {
    res = await fetch(apiUrl('/api/chat'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  } catch (e) {
    if (e.name === 'AbortError') throw e
    throw new ApiError('No se puede conectar con el backend del asistente.', 'OFFLINE')
  }
  if (!res.ok || !res.body) {
    const b = await res.json().catch(() => null)
    if (!b?.error && res.status >= 500) throw new ApiError(OFFLINE_MSG, 'OFFLINE')
    throw new ApiError(b?.error || `Error ${res.status}`, b?.code)
  }
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      let event = 'message', data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      try { onEvent(event, data ? JSON.parse(data) : {}) } catch (e) { console.error(e) }
    }
  }
}
