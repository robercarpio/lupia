import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, AudioLines, Square, Sparkles, TrendingDown, Store, Truck, Car, MapPin, Search, Scale, CalendarClock, AlertCircle, RotateCcw } from 'lucide-react'
import Markdown from './Markdown.jsx'
import Block from './Blocks.jsx'
import { ModelBanner } from '../dashboard/Dashboard.jsx'
import { describeFilters } from '../format.js'

export const SUGGESTIONS = [
  { icon: CalendarClock, text: '¿Cuáles han sido las ventas totales de esta semana?' },
  { icon: Store, text: '¿Qué restaurante ha vendido más durante el último mes?' },
  { icon: TrendingDown, text: '¿Qué restaurantes han tenido una mayor caída de ventas este mes?' },
  { icon: Scale, text: '¿Cómo han evolucionado las ventas respecto al mes anterior?' },
  { icon: MapPin, text: '¿Qué provincia vende más?' },
  { icon: Truck, text: '¿Cuánto pesa domicilio en las ventas de cada comunidad?' },
  { icon: Car, text: '¿Qué restaurantes presentan anomalías?' },
  { icon: Search, text: 'Explícame la anomalía con mayor impacto económico.' },
]

const greeting = () => {
  const h = new Date().getHours()
  return h < 6 ? 'Buenas noches' : h < 14 ? 'Buenos días' : h < 21 ? 'Buenas tardes' : 'Buenas noches'
}

function Composer({ onSend, onStop, onCall, busy, autoFocus, big }) {
  const [text, setText] = useState('')
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(180, el.scrollHeight)}px`
  }, [text])
  useEffect(() => { if (autoFocus) ref.current?.focus() }, [autoFocus])
  const submit = () => { if (text.trim() && !busy) { onSend(text); setText('') } }
  return (
    <div className={`composer ${big ? 'big' : ''}`}>
      <textarea ref={ref} rows={1} value={text} placeholder="Pregunta sobre anomalías, restaurantes, canales o impacto…"
                onChange={(e) => setText(e.target.value)} aria-label="Mensaje para el asistente"
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() } }} />
      <div className="composer-actions">
        <button className="c-btn call" onClick={onCall} title="Iniciar conversación por voz" aria-label="Iniciar conversación por voz">
          <AudioLines size={18} /><span>Voz</span>
        </button>
        {busy ? (
          <button className="c-btn send stop" onClick={onStop} aria-label="Detener respuesta"><Square size={14} fill="currentColor" /></button>
        ) : (
          <button className="c-btn send" onClick={submit} disabled={!text.trim()} aria-label="Enviar"><ArrowUp size={18} strokeWidth={2.4} /></button>
        )}
      </div>
    </div>
  )
}

function AssistantMessage({ m, onOpen, onDashboard, meta }) {
  const empty = !m.parts.length
  return (
    <div className="msg assistant">
      <span className={`ai-avatar ${!m.done ? 'live' : ''}`} aria-hidden />
      <div className="msg-body">
        {m.parts.map((p, i) => (p.type === 'text'
          ? p.text.trim() && <Markdown key={i} text={p.text} />
          : <Block key={p.block.key || i} block={p.block} onOpen={onOpen} onDashboard={onDashboard} meta={meta} />))}
        {!m.done && (m.status || empty) && (
          <div className="ai-status" aria-live="polite"><span className="shimmer">{m.status || 'Pensando'}</span><i className="dots"><b /><b /><b /></i></div>
        )}
        {m.error && <div className="ai-error"><AlertCircle size={15} /><span>{m.error}</span></div>}
        {m.interrupted && !m.error && <small className="muted">Respuesta interrumpida</small>}
      </div>
    </div>
  )
}

export default function Assistant({ chat, meta, filters, onOpen, onDashboard, onCall, aiStatus }) {
  const scroller = useRef(null)
  const endRef = useRef(null)
  const stick = useRef(true)
  const { messages, busy } = chat
  useEffect(() => {
    if (stick.current) endRef.current?.scrollIntoView({ block: 'end', behavior: messages.length < 3 ? 'auto' : 'smooth' })
  }, [messages])
  const onScroll = () => {
    const el = scroller.current
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140
  }
  const send = (t) => { stick.current = true; chat.send(t) }

  if (!messages.length) {
    return (
      <div className="ai-view">
        <div className="ai-welcome">
          <ModelBanner meta={meta} />
          <div className="ai-orb" aria-hidden><i /></div>
          <h2 className="ai-hello">{greeting()}.</h2>
          <p className="ai-q">¿Qué quieres saber de tus ventas?</p>
          <p className="ai-desc">
            Soy tu copiloto de ventas y anomalías. Consulto en Databricks los mismos datos que el dashboard —{' '}
            {meta.restaurantes} restaurantes, sala, domicilio y autoking— para darte rankings, comparaciones y explicaciones. Nunca invento cifras.
          </p>
          {aiStatus && !aiStatus.configured && (
            <div className="ai-notice"><AlertCircle size={15} /><span><b>Modo básico.</b> El backend no tiene Azure OpenAI configurado (<code>AZURE_OPENAI_ENDPOINT</code>, <code>AZURE_OPENAI_API_KEY</code>, <code>AZURE_OPENAI_DEPLOYMENT</code> en <code>backend/.env</code>): las respuestas se generan directamente de los datos, sin lenguaje natural libre.</span></div>
          )}
          <Composer onSend={send} onCall={onCall} busy={busy} autoFocus big />
          <div className="ai-context"><Sparkles size={13} />Contexto: {describeFilters(filters, meta)}</div>
          <div className="suggestions">
            {SUGGESTIONS.map(({ icon: Icon, text }) => (
              <button key={text} className="suggestion" onClick={() => send(text)}>
                <Icon size={16} /><span>{text}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="ai-view">
      <div className="ai-scroll" ref={scroller} onScroll={onScroll}>
        <div className="ai-thread">
          <ModelBanner meta={meta} />
          {messages.map((m) => (m.role === 'user'
            ? <div key={m.id} className="msg user"><p>{m.text}</p>{m.voice && <small>por voz</small>}</div>
            : <AssistantMessage key={m.id} m={m} onOpen={onOpen} onDashboard={onDashboard} meta={meta} />))}
          {!busy && messages.length > 0 && (
            <div className="followups">
              {SUGGESTIONS.slice(0, 3).map(({ text }) => <button key={text} className="btn-chip" onClick={() => send(text)}>{text}</button>)}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>
      <div className="ai-dock">
        <div className="ai-dock-inner">
          <Composer onSend={send} onStop={chat.abort} onCall={onCall} busy={busy} autoFocus />
          <div className="ai-dock-foot">
            <span className="ai-context"><Sparkles size={13} />{describeFilters(filters, meta)}</span>
            <button className="btn-link" onClick={chat.reset}><RotateCcw size={12} />Nueva conversación</button>
          </div>
        </div>
      </div>
    </div>
  )
}
