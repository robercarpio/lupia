import { useEffect, useRef, useState } from 'react'
import { Mic, MicOff, PhoneOff, Volume1, Volume2, VolumeX, Wifi, WifiOff, RotateCcw, ArrowUp, AlertTriangle, Download, ChevronDown } from 'lucide-react'
import Block from './Blocks.jsx'

const PHASE = {
  connecting: ['Conectando', 'Preparando micrófono y voz…'],
  listening: ['Escuchando', 'Habla con naturalidad'],
  processing: ['Pensando', 'Consultando los datos reales…'],
  speaking: ['Hablando', 'Puedes interrumpir hablando'],
  error: ['No disponible', ''],
  ended: ['Llamada finalizada', ''],
}

/** Orbe animado: reacciona al micrófono al escuchar y a la voz del asistente al hablar. */
function Orb({ call }) {
  const ref = useRef(null)
  const phaseRef = useRef(call.phase)
  phaseRef.current = call.phase
  const callRef = useRef(call)
  callRef.current = call
  useEffect(() => {
    const cv = ref.current
    const g = cv.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const S = 280
    cv.width = S * dpr; cv.height = S * dpr
    g.scale(dpr, dpr)
    let raf, lvl = 0, t0 = performance.now()
    const css = getComputedStyle(document.documentElement)
    const teal = css.getPropertyValue('--teal').trim() || '#12c3a8'
    const purple = css.getPropertyValue('--purple').trim() || '#7b6cf6'
    const draw = (now) => {
      const t = (now - t0) / 1000
      const p = phaseRef.current
      let target = 0.05
      if (p === 'listening') target = callRef.current.getLevel()
      else if (p === 'speaking') target = 0.35 + 0.25 * Math.sin(t * 7) * Math.sin(t * 2.3) + (now - callRef.current.getPulse() < 160 ? 0.25 : 0)
      else if (p === 'processing') target = 0.12 + 0.06 * Math.sin(t * 3)
      lvl += (Math.max(0, target) - lvl) * 0.18
      g.clearRect(0, 0, S, S)
      const cx = S / 2, cy = S / 2, R = 78 + lvl * 26
      // halo
      const halo = g.createRadialGradient(cx, cy, R * 0.6, cx, cy, R * 1.75)
      halo.addColorStop(0, `${purple}55`); halo.addColorStop(1, `${purple}00`)
      g.fillStyle = halo; g.beginPath(); g.arc(cx, cy, R * 1.75, 0, Math.PI * 2); g.fill()
      // ondas
      for (let k = 0; k < 3; k++) {
        g.beginPath()
        const amp = 3 + lvl * (16 - k * 4)
        for (let a = 0; a <= Math.PI * 2 + 0.01; a += Math.PI / 90) {
          const rr = R + 6 + k * 9 + Math.sin(a * (3 + k) + t * (1.6 + k * 0.7)) * amp * (p === 'processing' ? 0.4 : 1)
          const x = cx + Math.cos(a) * rr, y = cy + Math.sin(a) * rr
          a === 0 ? g.moveTo(x, y) : g.lineTo(x, y)
        }
        g.strokeStyle = k % 2 ? `${teal}66` : `${purple}55`
        g.lineWidth = 1.6
        g.stroke()
      }
      // esfera
      const sp = g.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.1, cx, cy, R)
      sp.addColorStop(0, '#ffffff')
      sp.addColorStop(0.28, teal)
      sp.addColorStop(1, purple)
      g.fillStyle = sp; g.beginPath(); g.arc(cx, cy, R, 0, Math.PI * 2); g.fill()
      if (p === 'processing') {
        g.strokeStyle = '#ffffffaa'; g.lineWidth = 2.5; g.lineCap = 'round'
        g.beginPath(); g.arc(cx, cy, R - 10, t * 3, t * 3 + 1.2); g.stroke()
      }
      raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(raf)
  }, [])
  return <canvas ref={ref} className={`orb-canvas ${call.phase}`} style={{ width: 280, height: 280 }} aria-hidden />
}

function Timer({ since }) {
  const [, tick] = useState(0)
  useEffect(() => { const i = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(i) }, [])
  if (!since) return null
  const s = Math.floor((Date.now() - since) / 1000)
  return <span className="call-timer">{String(Math.floor(s / 60)).padStart(2, '0')}:{String(s % 60).padStart(2, '0')}</span>
}

const REC = { idle: 'Reconocimiento en pausa', starting: 'Activando el micrófono…', ready: 'Micrófono activo · te escucho', speech: 'Detectando voz…' }

export default function CallOverlay({ call, onClose, onOpen, onDashboard, meta }) {
  const [title, baseHint] = PHASE[call.phase] || ['', '']
  // En móvil la escucha se pausa mientras habla el asistente (si no, se oiría a sí mismo): se interrumpe con el botón.
  const hint = call.mobile && call.phase === 'speaking' ? 'Pulsa «Interrumpir y hablar» para cortarle' : baseHint
  const logRef = useRef(null)
  // El reconocedor del móvil se reinicia solo cada pocos segundos (≈200 ms en pausa): el botón
  // «Toca para hablar» solo aparece si lleva más de 1,2 s parado, para que no parpadee.
  const [stalled, setStalled] = useState(false)
  useEffect(() => {
    if (call.recStatus !== 'idle') { setStalled(false); return }
    const t = setTimeout(() => setStalled(true), 1200)
    return () => clearTimeout(t)
  }, [call.recStatus])
  // Scroll instantáneo y como mucho una vez por frame: con «smooth» en cada fragmento de texto
  // las animaciones se acumulaban y el móvil iba a tirones durante la respuesta.
  useEffect(() => {
    const id = requestAnimationFrame(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight })
    return () => cancelAnimationFrame(id)
  }, [call.transcript, call.interim])
  // Al llegar una tabla nueva, mostrarla desde arriba (tiene su propio scroll).
  const blocksRef = useRef(null)
  const firstBlock = call.blocks[0]?.key
  useEffect(() => { if (firstBlock) blocksRef.current?.scrollTo({ top: 0, behavior: 'smooth' }) }, [firstBlock])
  const conn = {
    checking: ['Comprobando conexión', 'warn'], online: ['Conectado', 'ok'], offline: ['Sin conexión con el servicio de voz', 'bad'],
    'no-ai': ['Modo básico: backend sin Azure OpenAI configurado', 'warn'],
  }[call.connection] || ['', '']
  const VolIcon = call.volume === 0 ? VolumeX : call.volume < 0.5 ? Volume1 : Volume2
  const live = !['error', 'ended', 'idle'].includes(call.phase)
  const canSend = live && !call.muted && !!call.interim.trim()
  // Atajo: Intro envía lo dicho hasta ahora.
  useEffect(() => {
    const k = (e) => { if (e.key === 'Enter' && canSend) { e.preventDefault(); call.sendNow() } }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [canSend, call])

  return (
    <div className={`call-layer ${call.blocks.length ? 'has-blocks' : ''}`} role="dialog" aria-modal="true" aria-label="Llamada con el AI Copilot">
      <header className="call-top">
        <span className={`conn ${conn[1]}`}>{conn[1] === 'bad' ? <WifiOff size={14} /> : <Wifi size={14} />}{conn[0]}</span>
        <span className="call-name">AI Copilot · Anomalías de ventas</span>
        <div className="call-top-right">
          {live && call.devices.length > 0 && (
            <label className="mic-select" title="Micrófono">
              <Mic size={13} />
              <select value={call.deviceId} onChange={(e) => call.selectDevice(e.target.value)} aria-label="Elegir micrófono">
                {call.devices.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
              </select>
              <ChevronDown size={12} />
            </label>
          )}
          {live ? <Timer since={call.startedAt} /> : <span />}
        </div>
      </header>

      <div className="call-main">
        <div className="call-center">
          <Orb call={call} />
          <h2 className={`call-phase ${call.phase}`} aria-live="polite">{title}{call.phase === 'processing' && <i className="dots"><b /><b /><b /></i>}</h2>
          {live && <span className={`rec-pill ${call.muted ? 'off' : call.recStatus}`}>{call.muted ? 'Micrófono silenciado' : call.hearing && call.recStatus !== 'idle' ? 'Te oigo…' : REC[call.recStatus]}</span>}
          {/* Móvil: si el reconocedor no está escuchando (iOS a veces no deja reiniciarlo solo), un toque lo arranca. */}
          {live && call.mobile && !call.muted && (stalled || call.phase === 'speaking') && call.recStatus === 'idle' && call.phase !== 'processing' && (
            <button className="call-talk" onClick={call.listen}><Mic size={16} />{call.phase === 'speaking' ? 'Interrumpir y hablar' : 'Toca para hablar'}</button>
          )}
          {call.interim
            ? <p className="call-caption" aria-live="polite">“{call.interim}”<i className="caret" /></p>
            : call.hearing && call.phase === 'listening'
            ? <p className="call-caption hearing">Te oigo<i className="dots"><b /><b /><b /></i></p>
            : <p className="call-hint">{call.error || (call.muted ? 'Pulsa «Activar» para seguir hablando' : hint)}</p>}
          {call.notice && !call.error && (
            <div className="call-notice" role="status">
              <AlertTriangle size={15} /><span>{call.notice}</span>
              {call.localStatus === 'downloadable' && <button onClick={call.installLocal}><Download size={13} />Descargar voz local</button>}
              {call.localStatus === 'installing' && <i className="dots"><b /><b /><b /></i>}
            </div>
          )}
          {call.phase === 'error' && <p className="call-dep">Requisitos: navegador con Web Speech API (Chrome, Edge o Safari), permiso de micrófono y backend en marcha (con Azure OpenAI para respuestas conversacionales).</p>}
        </div>
        {(live || call.transcript.length > 0) && (
          <div className="call-side">
            <div className="call-log" ref={logRef} aria-label="Transcripción">
              <h3>Transcripción</h3>
              {!call.transcript.length && !call.interim && <p className="placeholder">Lo que digas aparecerá aquí en tiempo real.</p>}
              {call.transcript.map((t, i) => (
                <p key={i} className={t.role}><small>{t.role === 'user' ? 'Tú' : 'Copiloto'}</small>{t.text.replace(/[*_`#]/g, '')}</p>
              ))}
              {!call.interim && call.hearing && call.phase === 'listening' && <p className="user interim"><small>Tú · transcribiendo</small>…</p>}
              {call.interim && <p className="user interim"><small>Tú · transcribiendo</small>{call.interim}</p>}
            </div>
            {call.blocks.length > 0 && (
              <div className="call-blocks" ref={blocksRef}>
                {call.blocks.slice(0, 2).map((b) => <Block key={b.key} block={b} onOpen={onOpen} onDashboard={onDashboard} meta={meta} />)}
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="call-controls">
        {live ? (
          <>
            <button className={`call-btn ${call.muted ? 'on' : ''}`} onClick={call.toggleMute} aria-pressed={call.muted} aria-label={call.muted ? 'Activar micrófono' : 'Silenciar micrófono'}>
              {call.muted ? <MicOff size={22} /> : <Mic size={22} />}<span>{call.muted ? 'Activar' : 'Silenciar'}</span>
            </button>
            <button className="call-btn end" onClick={() => { call.end(); onClose() }} aria-label="Finalizar llamada"><PhoneOff size={24} /><span>Colgar</span></button>
            <button className={`call-btn send ${canSend ? 'ready' : ''}`} onClick={call.sendNow} disabled={!canSend}
                    aria-label="Enviar ahora lo que has dicho" title="Enviar ahora, sin esperar al silencio (Intro)">
              <ArrowUp size={22} strokeWidth={2.4} /><span>Enviar ahora</span>
            </button>
            <label className="call-vol" title="Volumen de la voz (se aplica a la siguiente frase)">
              <VolIcon size={18} />
              <input type="range" min="0" max="1" step="0.05" value={call.volume} onChange={(e) => call.setVolume(+e.target.value)} aria-label="Volumen" />
            </label>
          </>
        ) : (
          <>
            {call.phase === 'error' && <button className="call-btn" onClick={call.start}><RotateCcw size={22} /><span>Reintentar</span></button>}
            <button className="call-btn end" onClick={onClose} aria-label="Cerrar"><PhoneOff size={24} /><span>Cerrar</span></button>
          </>
        )}
      </footer>
    </div>
  )
}
