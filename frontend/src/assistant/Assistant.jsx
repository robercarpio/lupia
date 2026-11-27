import { Component, memo, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowUp, AudioLines, Square, Sparkles, TrendingDown, Store, Truck, Car, MapPin, Search, Scale, CalendarClock, AlertCircle, RotateCcw, ArrowDown } from 'lucide-react'
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

const PAGE = 16 // mensajes que se pintan de cada vez

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

// Si un bloque (tabla, gráfico…) falla al pintarse, solo se pierde ese mensaje, no toda la app.
class MessageBoundary extends Component {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(e) { console.error('Error al mostrar un mensaje del asistente', e) }
  render() {
    if (this.state.failed) {
      return <div className="msg assistant"><span className="ai-avatar" aria-hidden /><div className="msg-body"><div className="ai-error"><AlertCircle size={15} /><span>No se ha podido mostrar esta respuesta.</span></div></div></div>
    }
    return this.props.children
  }
}

// Memorizado: al llegar texto nuevo solo se repinta el mensaje que cambia, no toda la conversación.
const AssistantMessage = memo(function AssistantMessage({ m, onOpen, onDashboard, meta }) {
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
})

export default function Assistant({ chat, meta, filters, onOpen, onDashboard, onCall, aiStatus }) {
  const scroller = useRef(null)
  const endRef = useRef(null)
  const stick = useRef(true)
  const { messages, busy } = chat
  // Solo se pintan los últimos mensajes: con conversaciones largas (tablas y gráficos
  // en cada respuesta) el DOM crece sin límite y Safari en iOS acaba cerrando la pestaña.
  const [shown, setShown] = useState(PAGE)
  const hidden = Math.max(0, messages.length - shown)
  const visible = hidden ? messages.slice(hidden) : messages
  const keepPos = useRef(null)
  useEffect(() => { if (!messages.length) setShown(PAGE) }, [messages.length])
  // Al mostrar mensajes anteriores se mantiene la posición de lectura.
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && keepPos.current != null) { el.scrollTop = el.scrollHeight - keepPos.current; keepPos.current = null }
  }, [shown])
  const showMore = () => {
    const el = scroller.current
    if (el) keepPos.current = el.scrollHeight - el.scrollTop
    follow(false)
    setShown((n) => n + PAGE)
  }
  // Seguimiento del final de la conversación mientras se genera la respuesta.
  // En cuanto el usuario toca la pantalla, usa la rueda o arrastra la barra, deja de seguir:
  // la respuesta se sigue generando, pero la vista se queda donde está para poder leer.
  // Vuelve a seguir si baja él mismo hasta el final o pulsa la flecha.
  const [following, setFollowing] = useState(true)
  const follow = (on) => { stick.current = on; setFollowing(on) }
  // Solo importa mientras llega una respuesta: con la conversación quieta un toque no cambia nada.
  const stopFollowing = () => { if (stick.current && busy) follow(false) }
  const toBottom = (smooth) => {
    const el = scroller.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }
  // Durante el streaming el scroll es instantáneo: una animación «smooth» por cada
  // fragmento de texto se acumula y bloquea el móvil en conversaciones largas.
  useEffect(() => {
    if (!stick.current) return
    const id = requestAnimationFrame(() => toBottom(!busy && messages.length >= 3))
    return () => cancelAnimationFrame(id)
  }, [messages, busy])
  const onScroll = () => {
    const el = scroller.current
    // Mientras no se sigue, la vista solo se mueve si la mueve el usuario: si llega al final, se reanuda.
    if (el && !stick.current && el.scrollHeight - el.scrollTop - el.clientHeight < 8) follow(true)
  }
  const onKeyDown = (e) => { if (['ArrowUp', 'PageUp', 'Home'].includes(e.key)) stopFollowing() }
  const resume = () => { follow(true); toBottom(true) }
  const send = (t) => { follow(true); setShown(PAGE); chat.send(t) }

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
      <div className="ai-scroll" ref={scroller} onScroll={onScroll} onTouchStart={stopFollowing}
           onWheel={stopFollowing} onPointerDown={stopFollowing} onKeyDown={onKeyDown}>
        <div className="ai-thread">
          <ModelBanner meta={meta} />
          {hidden > 0 && (
            <button className="btn-chip ai-more" onClick={showMore}>Mostrar {Math.min(PAGE, hidden)} mensajes anteriores</button>
          )}
          {visible.map((m) => (m.role === 'user'
            ? <div key={m.id} className="msg user"><p>{m.text}</p>{m.voice && <small>por voz</small>}</div>
            : <MessageBoundary key={m.id}><AssistantMessage m={m} onOpen={onOpen} onDashboard={onDashboard} meta={meta} /></MessageBoundary>))}
          {!busy && messages.length > 0 && (
            <div className="followups">
              {SUGGESTIONS.slice(0, 3).map(({ text }) => <button key={text} className="btn-chip" onClick={() => send(text)}>{text}</button>)}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>
      <div className="ai-dock">
        {!following && (
          <button className="ai-to-bottom" onClick={resume} aria-label="Ir al final de la conversación">
            <ArrowDown size={16} />{busy && <span>Ir al final</span>}
          </button>
        )}
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
