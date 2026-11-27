import { useCallback, useEffect, useRef, useState } from 'react'
import { postJSON, streamChat } from '../api.js'

let seq = 0
const uid = () => `m${Date.now().toString(36)}${(seq++).toString(36)}`

// La conversación se guarda en sessionStorage: si el navegador recarga la pestaña
// (en iOS lo hace al quedarse sin memoria) se recupera al volver.
const STORE = 'lupia-chat'
const MAX_STORED = 60 // mensajes como máximo que se guardan

function load() {
  try {
    const raw = sessionStorage.getItem(STORE)
    if (!raw) return { messages: [], conv: null }
    const { messages = [], conv = null } = JSON.parse(raw)
    // Una respuesta que se estaba generando al recargar queda como interrumpida.
    return { conv, messages: messages.map((m) => (m.role === 'assistant' && !m.done ? { ...m, done: true, status: null, interrupted: true } : m)) }
  } catch {
    return { messages: [], conv: null }
  }
}

function save(messages, conv) {
  try {
    if (!messages.length) { sessionStorage.removeItem(STORE); return }
    sessionStorage.setItem(STORE, JSON.stringify({ conv, messages: messages.slice(-MAX_STORED) }))
  } catch {
    // Sin almacenamiento o cuota llena: se sigue sin guardar.
  }
}

/**
 * Estado de la conversación con el AI Copilot. Vive en App para que la charla
 * se conserve al cambiar entre Dashboard y AI Assistant, y la comparte el modo llamada.
 */
export function useChat({ getContext, onFilters, onFocus }) {
  const [initial] = useState(load)
  const [messages, setMessages] = useState(initial.messages)
  const [busy, setBusy] = useState(false)
  const conv = useRef(initial.conv)
  const ctrl = useRef(null)

  const patch = (id, fn) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)))

  // Se guarda al terminar cada respuesta, no en cada fragmento del streaming.
  useEffect(() => { if (!busy) save(messages, conv.current) }, [messages, busy])

  const send = useCallback(async (text, { voice = false, onText, onBlock } = {}) => {
    const q = text.trim()
    if (!q) return
    ctrl.current?.abort()
    const ac = new AbortController()
    ctrl.current = ac
    const aid = uid()
    setMessages((ms) => [...ms, { id: uid(), role: 'user', text: q, voice }, { id: aid, role: 'assistant', parts: [], status: 'Pensando', voice, done: false }])
    setBusy(true)
    let full = ''
    // Los fragmentos de texto se acumulan y se pintan como mucho una vez por frame:
    // repintar en cada token con una conversación larga satura el móvil.
    let pending = '', frame = 0
    const flushText = () => {
      frame = 0
      if (!pending) return
      const delta = pending
      pending = ''
      patch(aid, (m) => {
        const parts = [...m.parts]
        const last = parts[parts.length - 1]
        if (last?.type === 'text') parts[parts.length - 1] = { ...last, text: last.text + delta }
        else parts.push({ type: 'text', text: delta })
        return { ...m, parts, status: null }
      })
    }
    const ctx = getContext?.() || {}
    try {
      await streamChat({ conversationId: conv.current, message: q, voice, dashboardFilters: ctx.filters, focusId: ctx.focusId }, (event, data) => {
        switch (event) {
          case 'session': conv.current = data.id; break
          case 'status': patch(aid, (m) => ({ ...m, status: data.text })); break
          case 'text':
            if (data.soft && !full) break
            full += data.delta
            onText?.(data.delta)
            pending += data.delta
            if (!frame) frame = requestAnimationFrame(flushText)
            break
          case 'block':
            onBlock?.(data.block)
            if (frame) cancelAnimationFrame(frame)
            flushText()
            patch(aid, (m) => ({ ...m, parts: [...m.parts, { type: 'block', block: data.block }], status: m.status }))
            break
          case 'filters': onFilters?.(data.filters); break
          case 'focus': onFocus?.(data.id); break
          case 'error': if (frame) cancelAnimationFrame(frame); flushText(); patch(aid, (m) => ({ ...m, error: data.message, status: null })); break
          case 'done': break
        }
      }, ac.signal)
      if (frame) cancelAnimationFrame(frame)
      flushText()
      patch(aid, (m) => ({ ...m, done: true, status: null }))
    } catch (e) {
      if (frame) cancelAnimationFrame(frame)
      flushText()
      if (e.name === 'AbortError') patch(aid, (m) => ({ ...m, done: true, status: null, interrupted: true }))
      else patch(aid, (m) => ({ ...m, done: true, status: null, error: e.message }))
    } finally {
      if (ctrl.current === ac) { ctrl.current = null; setBusy(false) }
    }
    return full
  }, [getContext, onFilters, onFocus])

  const abort = useCallback(() => ctrl.current?.abort(), [])
  const reset = useCallback(() => {
    ctrl.current?.abort()
    if (conv.current) postJSON('/api/chat/reset', { conversationId: conv.current }).catch(() => {})
    conv.current = null
    setMessages([])
    setBusy(false)
  }, [])

  return { messages, busy, send, abort, reset }
}
