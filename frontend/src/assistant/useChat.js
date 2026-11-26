import { useCallback, useRef, useState } from 'react'
import { postJSON, streamChat } from '../api.js'

let seq = 0
const uid = () => `m${Date.now().toString(36)}${(seq++).toString(36)}`

/**
 * Estado de la conversación con el AI Copilot. Vive en App para que la charla
 * se conserve al cambiar entre Dashboard y AI Assistant, y la comparte el modo llamada.
 */
export function useChat({ getContext, onFilters, onFocus }) {
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const conv = useRef(null)
  const ctrl = useRef(null)

  const patch = (id, fn) => setMessages((ms) => ms.map((m) => (m.id === id ? fn(m) : m)))

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
            patch(aid, (m) => {
              const parts = [...m.parts]
              const last = parts[parts.length - 1]
              if (last?.type === 'text') parts[parts.length - 1] = { ...last, text: last.text + data.delta }
              else parts.push({ type: 'text', text: data.delta })
              return { ...m, parts, status: null }
            })
            break
          case 'block':
            onBlock?.(data.block)
            patch(aid, (m) => ({ ...m, parts: [...m.parts, { type: 'block', block: data.block }], status: m.status }))
            break
          case 'filters': onFilters?.(data.filters); break
          case 'focus': onFocus?.(data.id); break
          case 'error': patch(aid, (m) => ({ ...m, error: data.message, status: null })); break
          case 'done': break
        }
      }, ac.signal)
      patch(aid, (m) => ({ ...m, done: true, status: null }))
    } catch (e) {
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
