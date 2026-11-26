import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AudioLines, RotateCcw } from 'lucide-react'
import { DataVersion, filtersQuery, useApi, useDataStatus } from './api.js'
import { dateShort } from './format.js'
import Sidebar from './components/Sidebar.jsx'
import Topbar from './components/Topbar.jsx'
import { Select } from './components/ui.jsx'
import Dashboard from './dashboard/Dashboard.jsx'
import AnomalyDrawer from './dashboard/AnomalyDrawer.jsx'
import { PRESETS, detectPreset, emptyFilters, presetRange } from './dashboard/FilterBar.jsx'
import Assistant from './assistant/Assistant.jsx'
import CallOverlay from './assistant/CallOverlay.jsx'
import { useChat } from './assistant/useChat.js'
import { useVoiceCall } from './assistant/useVoiceCall.js'
import { useTheme } from './useTheme.js'
import { useIsMobile } from './useMedia.js'
import { MobileHeader, MobileTabBar } from './components/MobileNav.jsx'

const readMode = () => { try { return localStorage.getItem('mode') === 'assistant' ? 'assistant' : 'dashboard' } catch { return 'dashboard' } }

function PeriodPicker({ filters, meta, onApply }) {
  const current = detectPreset(filters, meta)
  const [draft, setDraft] = useState(current)
  useEffect(() => setDraft(current), [current])
  return (
    <>
      <Select value={draft} onChange={setDraft} options={PRESETS.filter((p) => p.value !== 'custom' || current === 'custom')} />
      <span className="sep" />
      <span className="muted small nowrap">vs periodo anterior</span>
      <button className="btn-apply" disabled={draft === current || draft === 'custom'} onClick={() => onApply(presetRange(draft, meta))}>Aplicar</button>
    </>
  )
}

export default function App() {
  const [theme, toggleTheme] = useTheme()
  const mobile = useIsMobile()
  const [retry, setRetry] = useState(0)
  // Versión de los datos del modelo: cuando el modelo externo escribe anomalías nuevas, cambia y
  // todo el dashboard (y meta) se vuelve a pedir solo. Se consulta cada 60 s mientras la pestaña está visible.
  const [booted, setBooted] = useState(false)
  const status = useDataStatus(booted)
  const version = status?.version || ''
  const metaRes = useApi('/api/meta', `${retry}|${version}`)
  const health = useApi('/api/health', retry)
  useEffect(() => { if (metaRes.data) setBooted(true) }, [metaRes.data])
  const offline = metaRes.error?.code === 'OFFLINE'
  // Si el backend aún no está arrancado, reintenta cada 3 s hasta que responda.
  useEffect(() => {
    if (!metaRes.error) return
    const t = setTimeout(() => setRetry((r) => r + 1), 3000)
    return () => clearTimeout(t)
  }, [metaRes.error, retry])
  const meta = metaRes.data
  const [mode, setModeState] = useState(readMode)
  const [filters, setFilters] = useState(null)
  const [synced, setSynced] = useState(false)
  const [drawer, setDrawer] = useState(null)
  const [focusId, setFocusId] = useState(null)
  const [callOpen, setCallOpen] = useState(false)
  const [navActive, setNavActive] = useState(0)
  const pendingScroll = useRef(null)

  useEffect(() => { if (meta && !filters) setFilters(emptyFilters(meta)) }, [meta, filters])
  const setMode = useCallback((m) => {
    setModeState(m)
    try { localStorage.setItem('mode', m) } catch { /* sin almacenamiento */ }
  }, [])

  const onFilter = useCallback((patch) => { setFilters((f) => ({ ...f, ...patch })); setSynced(false) }, [])
  const onClear = useCallback(() => { setFilters(emptyFilters(meta)); setSynced(false) }, [meta])
  const fromAssistant = useCallback((f) => { setFilters(f); setSynced(true) }, [])
  const openDashboard = useCallback((f) => { if (f) fromAssistant(f); setMode('dashboard'); window.scrollTo({ top: 0 }) }, [fromAssistant, setMode])
  const openAnomaly = useCallback((id) => { setDrawer(id); setFocusId(id) }, [])

  // El asistente recibe los filtros del dashboard y la anomalía en foco en cada pregunta.
  const ctxRef = useRef({})
  ctxRef.current = { filters, focusId }
  const getContext = useCallback(() => ctxRef.current, [])
  const chat = useChat({ getContext, onFilters: fromAssistant, onFocus: setFocusId })
  const call = useVoiceCall({ send: chat.send, abort: chat.abort })

  const startCall = () => { setCallOpen(true); call.start() }
  const closeCall = () => { if (!['ended', 'error', 'idle'].includes(call.phase)) call.end(); setCallOpen(false) }
  const askAbout = (a) => {
    setDrawer(null)
    setFocusId(a.id)
    setMode('assistant')
    chat.send(`Explícame la anomalía ${a.id}: qué ha pasado, cuánto impacto tiene y por qué merece revisión.`)
  }
  const navigate = (i, target) => {
    setNavActive(i)
    if (mode !== 'dashboard') { pendingScroll.current = target; setMode('dashboard') }
    else document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
  // Logo / nombre Lupia: vuelve al dashboard (al principio) desde cualquier vista.
  const goHome = useCallback(() => {
    setDrawer(null)
    setNavActive(0)
    setMode('dashboard')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [setMode])
  useEffect(() => {
    if (mode === 'dashboard' && pendingScroll.current) {
      const t = pendingScroll.current
      pendingScroll.current = null
      setTimeout(() => document.getElementById(t)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 350)
    }
  }, [mode])

  const highQuery = useMemo(() => (filters ? `/api/anomalies?${filtersQuery({ ...filters, prioridad: 'alta' })}&size=1` : null), [filters])
  const high = useApi(highQuery)

  // Aviso de la campana: el punto rojo indica alertas de prioridad alta que aún no se han visto.
  // Se marca como visto al pulsar la campana y vuelve a salir solo cuando llegan datos nuevos
  // (otra ejecución del modelo, otra fecha de corte u otro total de anomalías).
  const alerts = high.data?.total || 0
  const alertsKey = meta ? `${meta.modelRun?.run_id || ''}|${meta.dateMax || ''}|${meta.totalAnomalias ?? ''}` : ''
  const [seenKey, setSeenKey] = useState(() => { try { return localStorage.getItem('alertsSeen') } catch { return null } })
  const unseen = alerts > 0 && !!alertsKey && seenKey !== alertsKey
  const markSeen = useCallback(() => {
    if (!alertsKey) return
    setSeenKey(alertsKey)
    try { localStorage.setItem('alertsSeen', alertsKey) } catch { /* sin almacenamiento */ }
  }, [alertsKey])

  if (metaRes.error && !metaRes.data) {
    return (
      <div className="shell">
        <Sidebar mode={mode} onMode={setMode} onHome={goHome} />
        <main className="main"><div className="content"><div className="card panel span-12 setup">
          <h2>{offline ? 'El backend no está en marcha'
            : metaRes.error.code === 'WAREHOUSE_STARTING' ? 'El SQL Warehouse de Databricks está arrancando'
            : metaRes.error.code === 'NO_DATA' ? 'No hay datos en Databricks'
            : metaRes.error.code === 'NOT_CONFIGURED' ? 'Falta configurar la conexión con Databricks'
            : 'No se pueden cargar los datos'}</h2>
          <p>{metaRes.error.message}</p>
          {offline ? (
            <ol>
              <li>En <code>backend/</code>, ejecuta <code>.venv/bin/uvicorn app.main:app --reload --port 8787</code>.</li>
              <li>En <code>frontend/</code>, <code>npm run dev</code> arranca la web; Vite redirige <code>/api</code> al backend.</li>
            </ol>
          ) : metaRes.error.code === 'NOT_CONFIGURED' ? (
            <ol>
              <li>Revisa <code>backend/.env</code> (ver <code>backend/.env.example</code>): <code>DATABRICKS_HOST</code>, <code>DATABRICKS_HTTP_PATH</code> y <code>DATABRICKS_TOKEN</code> o un perfil de <code>databricks auth login</code>.</li>
              <li>Reinicia el backend.</li>
            </ol>
          ) : metaRes.error.code === 'WAREHOUSE_STARTING' ? (
            <p className="muted">Un warehouse serverless tarda unos segundos en arrancar tras un periodo de inactividad.</p>
          ) : null}
          <p className="muted small">Reintentando automáticamente cada 3 segundos…</p>
        </div></div></main>
      </div>
    )
  }
  if (!meta || !filters) return <div className="shell"><Sidebar mode={mode} onMode={setMode} onHome={goHome} /><main className="main"><div className="content"><div className="card panel span-12"><div className="skeleton tall" /></div></div></main></div>

  // Campana: con alertas sin ver, las muestra (filtro de prioridad alta en el dashboard o
  // pregunta en el asistente). Ya vistas, solo lleva a la sección de anomalías sin tocar filtros.
  const onBell = () => {
    if (!unseen) { navigate(1, 'sec-anomalias'); return }
    if (mode === 'assistant') chat.send('¿Qué anomalías de prioridad alta hay en el periodo actual?')
    else { onFilter({ prioridad: 'alta' }); navigate(1, 'sec-anomalias') }
  }

  const sourceLabel = 'Databricks'
  return (
    <DataVersion.Provider value={version}>
    <div className={`shell mode-${mode} ${mobile ? 'is-mobile' : ''}`}>
      {!mobile && <Sidebar mode={mode} onMode={setMode} onHome={goHome} active={navActive} onNavigate={navigate} />}
      <main className="main">
        {mobile ? (
          mode === 'dashboard' ? (
            <MobileHeader onHome={goHome} title="Anomalías de ventas" subtitle={`Datos hasta el ${dateShort(meta.dateMax)} · ${sourceLabel}`} theme={theme} onTheme={toggleTheme}
                          alerts={alerts} unseen={unseen} onSeen={markSeen} onAlerts={onBell} />
          ) : (
            <MobileHeader onHome={goHome} title="AI Assistant" subtitle="Texto o voz · mismos datos" theme={theme} onTheme={toggleTheme}
                          alerts={alerts} unseen={unseen} onSeen={markSeen} onAlerts={onBell}>
              <button className="btn-ghost" onClick={chat.reset} disabled={!chat.messages.length}><RotateCcw size={14} />Nueva</button>
              <button className="btn-apply as-link" onClick={startCall}><AudioLines size={14} />Llamar</button>
            </MobileHeader>
          )
        ) : mode === 'dashboard' ? (
          <Topbar title="Detección de anomalías de ventas" subtitle={`${meta.restaurantes} restaurantes · datos hasta el ${dateShort(meta.dateMax)} · ${sourceLabel}`}
                  theme={theme} onTheme={toggleTheme}
                  alerts={alerts} unseen={unseen} onSeen={markSeen} onAlerts={onBell}>
            <PeriodPicker filters={filters} meta={meta} onApply={(r) => onFilter(r)} />
          </Topbar>
        ) : (
          <Topbar title="AI Assistant" subtitle="Pregunta a tus datos por texto o por voz · mismos datos que el dashboard" theme={theme} onTheme={toggleTheme} alerts={alerts} unseen={unseen} onSeen={markSeen}
                  onAlerts={onBell}>
            <button className="btn-ghost" onClick={chat.reset} disabled={!chat.messages.length}><RotateCcw size={14} />Nueva conversación</button>
            <button className="btn-apply as-link" onClick={startCall}><AudioLines size={14} />Llamar</button>
          </Topbar>
        )}
        <div className="mode-view" key={mode}>
          {mode === 'dashboard'
            ? <Dashboard meta={meta} filters={filters} onFilter={onFilter} onClear={onClear} onOpen={openAnomaly} synced={synced} />
            : <Assistant chat={chat} meta={meta} filters={filters} onOpen={openAnomaly} onDashboard={openDashboard} onCall={startCall} aiStatus={health.data?.ai} />}
        </div>
      </main>
      {mobile && <MobileTabBar mode={mode} active={navActive} onNavigate={navigate} onMode={setMode} />}
      {drawer && <AnomalyDrawer id={drawer} onClose={() => setDrawer(null)} onOpen={openAnomaly} onAsk={askAbout} />}
      {callOpen && <CallOverlay call={call} meta={meta} onClose={closeCall} onOpen={(id) => { closeCall(); openAnomaly(id) }} onDashboard={(f) => { closeCall(); openDashboard(f) }} />}
    </div>
    </DataVersion.Provider>
  )
}
