import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON } from '../api.js'

// Conversación por voz en tiempo real:
//  · Captura: una sola pista de micrófono (getUserMedia con cancelación de eco) que
//    comparten la onda animada y el reconocedor (SpeechRecognition.start(track)). Así no
//    se abre el micrófono dos veces ni se usa un dispositivo distinto del elegido.
//  · Voz → texto: Web Speech API (es-ES), continuo y con resultados parciales. Si el
//    servicio en la nube falla, se pasa a reconocimiento local (processLocally) cuando existe.
//  · Razonamiento: el mismo endpoint del chat (/api/chat con voice=true): mismo copiloto (Azure OpenAI)
//    y mismas herramientas sobre los datos reales de Databricks.
//  · Texto → voz: speechSynthesis, frase a frase mientras llega la respuesta.
//  · Interrupción: si hablas mientras responde, se corta la voz y la petición.
// No hay simulación: si algo falla se muestra qué es y cómo resolverlo.

const SR = typeof window !== 'undefined' ? window.SpeechRecognition || window.webkitSpeechRecognition : null
export const voiceSupport = () => ({
  recognition: !!SR,
  synthesis: typeof window !== 'undefined' && 'speechSynthesis' in window,
  mic: typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia,
})

// Móvil (Android/iOS): el reconocedor no puede compartir el micrófono con getUserMedia ni
// acepta una pista como argumento, el modo continuo de Android duplica/no cierra frases y
// la voz del asistente sale por el altavoz sin cancelación de eco. Ahí la llamada es
// semidúplex: se libera el micrófono tras pedir permiso, se reconoce frase a frase
// (continuous=false, reinicio en onend) y se pausa la escucha mientras habla el asistente.
const MOBILE = typeof navigator !== 'undefined' && (
  /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) ||
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1))

const LANG = 'es-ES'
const SILENCE_MS = 800 // pausa tras la que se da la frase por terminada (Chrome tarda mucho más en cerrarla)
const clean = (t) => t.replace(/[*_`#>|]/g, '').replace(/\s+/g, ' ').trim()
const words = (t) => t.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').match(/[a-z0-9ñ]+/g) || []
const dbg = (...a) => console.info('[voz]', ...a)

function pickVoice() {
  const vs = window.speechSynthesis.getVoices().filter((v) => v.lang?.toLowerCase().startsWith('es'))
  const pref = [/es-ES.*(Google|Natural|Neural|Online)/i, /Mónica|Monica|Paulina|Elvira|Alvaro|Álvaro/i, /es-ES/i]
  for (const re of pref) { const v = vs.find((x) => re.test(`${x.lang} ${x.name}`)); if (v) return v }
  return vs[0] || null
}

export function useVoiceCall({ send, abort }) {
  const [phase, setPhase] = useState('idle') // idle · connecting · listening · processing · speaking · error · ended
  const [muted, setMuted] = useState(false)
  const [volume, setVolumeState] = useState(1)
  const [interim, setInterim] = useState('')
  const [transcript, setTranscript] = useState([])
  const [blocks, setBlocks] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [connection, setConnection] = useState('checking') // checking · online · offline · no-ai
  const [recStatus, setRecStatus] = useState('idle') // idle · starting · ready · speech
  const [devices, setDevices] = useState([])
  const [deviceId, setDeviceId] = useState('')
  const [localStatus, setLocalStatus] = useState(null) // null · downloadable · installing · unavailable · active
  const [startedAt, setStartedAt] = useState(null)
  const [hearing, setHearing] = useState(false) // hay voz en el micrófono (inmediato, sin esperar al texto)

  const r = useRef({}).current // estado mutable de la llamada (no provoca renders)
  const phaseRef = useRef(phase)
  const setP = (p) => { phaseRef.current = p; setPhase(p) }

  // ── Texto a voz ─────────────────────────────────────────────────────
  const speakQueue = useCallback((text) => {
    const t = clean(text)
    if (!t || !r.active) return
    const u = new SpeechSynthesisUtterance(t)
    u.lang = LANG
    if (r.voice) u.voice = r.voice
    u.volume = r.volume ?? 1
    u.rate = 1.04
    u.onstart = () => {
      r.speaking = t
      if (r.active) setP('speaking')
      if (MOBILE) r.pauseRec() // semidúplex: no escuchar nuestra propia voz por el altavoz
    }
    u.onboundary = () => { r.pulse = performance.now() }
    u.onend = u.onerror = () => {
      // Locución ya descartada (cancelada al interrumpir o por el vigilante): su «onend» llega
      // tarde y no debe descontar las de la respuesta nueva.
      if (!r.queue.delete(u)) return
      r.spoken = `${r.spoken || ''} ${t}`.slice(-600)
      r.speakEndAt = performance.now()
      if (!r.queue.size) r.afterSpeech(r.speaking === t) // stopSpeaking() pone speaking=null antes de cancelar
    }
    // Se guarda la referencia: en Chrome, si la locución se recolecta, «onend» no llega nunca
    // y la llamada se queda en «hablando» sin volver a escuchar.
    r.queue.add(u)
    r.lastSpeakAt = performance.now()
    if (window.speechSynthesis.paused) window.speechSynthesis.resume() // Android a veces la deja en pausa
    window.speechSynthesis.speak(u)
  }, [r])

  /** Fin de todo lo que había en cola: vuelve a escuchar. */
  const afterSpeech = useCallback((natural) => {
    r.speaking = null
    if (r.replyDone && r.active) {
      setP('listening')
      // Sin reiniciar el reconocedor (perderíamos tus primeras palabras): lo que ya haya
      // transcrito del resultado en curso es eco y se recorta de lo que llegue después.
      if (!MOBILE && natural && r.curIdx != null) { r.echo = { idx: r.curIdx, text: r.curText || '' }; r.partial = ''; setInterim('') }
    }
    if (MOBILE && r.active && !r.muted && !r.rec) r.startRec()
  }, [r])
  r.afterSpeech = afterSpeech
  r.queue ??= new Set()

  const stopSpeaking = useCallback(() => {
    r.buf = ''
    r.queue.clear()
    r.speaking = null
    r.speakEndAt = performance.now()
    window.speechSynthesis?.cancel()
  }, [r])

  const feed = useCallback((delta) => {
    r.buf = (r.buf || '') + delta
    let m
    while ((m = r.buf.match(/^([\s\S]*?[.!?…:;\n])(\s+|$)/)) && m[1].trim().length > 1) {
      if (m[0].length === r.buf.length && !/\n$/.test(m[1])) break // espera por si sigue la frase
      speakQueue(m[1])
      r.buf = r.buf.slice(m[0].length)
    }
  }, [r, speakQueue])

  /** ¿El texto reconocido es la propia voz del asistente saliendo por los altavoces? */
  const isEcho = (text) => {
    const recent = r.speaking || performance.now() - (r.speakEndAt || 0) < 1500
    if (!recent) return false
    const w = words(text)
    if (!w.length) return true
    const spoken = new Set(words(`${r.spoken || ''} ${r.speaking || ''}`))
    return w.filter((x) => spoken.has(x)).length / w.length >= 0.7
  }

  // ── Conversación ────────────────────────────────────────────────────
  const handleUtterance = useCallback(async (text) => {
    const q = text.trim()
    if (!q || !r.active) return
    dbg('frase enviada:', q)
    const turn = (r.turn || 0) + 1
    r.turn = turn
    stopSpeaking()
    abort()
    r.replyDone = false
    r.spoken = ''
    setTranscript((t) => [...t, { role: 'user', text: q }])
    setP('processing')
    let reply = ''
    try {
      reply = await send(q, {
        voice: true,
        onText: (d) => { if (r.turn !== turn) return; reply += d; feed(d); setTranscript((t) => upsertAssistant(t, reply)) },
        onBlock: (b) => { if (r.turn === turn) setBlocks((bs) => [b, ...bs].slice(0, 4)) },
      })
    } catch { /* el error se refleja en el chat */ }
    // Si mientras tanto empezó otra pregunta (interrupción), esta respuesta ya no manda.
    if (!r.active || r.turn !== turn) return
    r.replyDone = true
    if (r.buf?.trim()) { speakQueue(r.buf); r.buf = '' }
    if (!r.queue.size) afterSpeech(false)
    if (!reply) setTranscript((t) => upsertAssistant(t, 'No he podido responder. Revisa el aviso en el chat del asistente.'))
  }, [r, send, abort, feed, speakQueue, stopSpeaking, afterSpeech])
  r.handle = handleUtterance // versión más reciente para los callbacks del reconocedor

  // ── Micrófono ───────────────────────────────────────────────────────
  const listDevices = useCallback(async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices()
      setDevices(all.filter((d) => d.kind === 'audioinput').map((d, i) => ({ id: d.deviceId, label: d.label || `Micrófono ${i + 1}` })))
    } catch { /* sin permiso aún */ }
  }, [])

  const openMic = useCallback(async (id) => {
    r.stream?.getTracks().forEach((t) => t.stop())
    r.stream = await navigator.mediaDevices.getUserMedia({
      audio: { deviceId: id ? { exact: id } : undefined, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    if (MOBILE) {
      // Solo para obtener el permiso: si la pista sigue abierta el reconocedor no recibe audio.
      r.micLabel = r.stream.getAudioTracks()[0]?.label || 'micrófono'
      r.stream.getTracks().forEach((t) => t.stop())
      r.stream = null; r.track = null
      dbg('micrófono (móvil, liberado para el reconocedor):', r.micLabel)
      return
    }
    r.track = r.stream.getAudioTracks()[0]
    r.track.enabled = !r.muted
    r.micLabel = r.track.label || 'micrófono'
    const Ctx = window.AudioContext || window.webkitAudioContext
    r.ctx ??= new Ctx()
    if (r.ctx.state === 'suspended') r.ctx.resume().catch(() => {})
    r.source?.disconnect()
    if (!r.analyser) { r.analyser = r.ctx.createAnalyser(); r.analyser.fftSize = 256 }
    r.source = r.ctx.createMediaStreamSource(r.stream)
    r.source.connect(r.analyser)
    r.peak = 0
    setDeviceId(r.track.getSettings?.().deviceId || id || '')
    dbg('micrófono:', r.micLabel)
    listDevices()
  }, [r, listDevices])

  // ── Reconocimiento de voz ───────────────────────────────────────────
  const startRecognition = useCallback(() => {
    if (!SR || !r.active || r.muted) return
    const old = r.rec
    r.rec = null
    clearTimeout(r.commitTimer)
    try { old?.abort() } catch { /* noop */ }
    const rec = new SR()
    r.rec = rec
    rec.lang = LANG
    rec.continuous = !MOBILE
    rec.interimResults = true
    rec.maxAlternatives = 1
    if (r.local) rec.processLocally = true
    setRecStatus('starting')

    r.curIdx = null; r.curText = ''; r.echo = null
    rec.onstart = () => { if (r.rec === rec) setRecStatus('ready'); dbg('reconocimiento iniciado', r.local ? '(local)' : '(nube)', r.useTrack ? '· pista compartida' : '· micrófono del sistema') }
    rec.onspeechstart = () => { if (r.rec === rec) { setRecStatus('speech'); if (MOBILE) setHearing(true) } }
    rec.onspeechend = () => { if (r.rec === rec) { setRecStatus('ready'); if (MOBILE) setHearing(false) } }
    rec.onresult = (e) => {
      if (r.rec !== rec) return
      r.failures = 0
      r.noSpeech = 0
      setNotice(null)
      setConnection((c) => (c === 'offline' ? 'online' : c))
      let fin = '', part = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        let tr = e.results[i][0].transcript
        if (!e.results[i].isFinal) { r.curIdx = i; r.curText = tr }
        if (r.echo) {
          if (i < r.echo.idx) continue
          if (i === r.echo.idx) {
            const pre = r.echo.text.trim()
            tr = tr.trim().startsWith(pre) ? tr.trim().slice(pre.length) : tr
            if (e.results[i].isFinal) r.echo = null
          }
        }
        if (e.results[i].isFinal) fin += tr; else part += tr
      }
      part = part.trim(); fin = fin.trim()
      if (part) {
        if (phaseRef.current === 'speaking') {
          if (isEcho(part) || words(part).length < 2) return // probablemente es su propia voz
          r.turn = (r.turn || 0) + 1; stopSpeaking(); abort(); r.replyDone = true; setP('listening') // interrupción por voz
        }
        r.partial = part
        setInterim(part)
        clearTimeout(r.commitTimer)
        r.commitTimer = setTimeout(() => {
          if (r.rec !== rec || !r.partial || phaseRef.current === 'speaking' || isEcho(r.partial)) return
          dbg('silencio detectado, envío sin esperar al final')
          r.sendNow()
        }, SILENCE_MS)
      }
      if (fin) {
        clearTimeout(r.commitTimer)
        r.partial = ''
        setInterim('')
        if (isEcho(fin)) { dbg('descartado como eco:', fin); return }
        // Ya enviado con «Enviar ahora»: el reconocedor puede cerrar la misma frase después.
        if (r.lastManual && performance.now() - r.lastManual.at < 4000 && words(fin).every((w) => r.lastManual.words.has(w))) return
        r.handle(fin)
      }
    }
    rec.onerror = (e) => {
      if (r.rec !== rec) return
      dbg('error de reconocimiento:', e.error, e.message || '')
      switch (e.error) {
        case 'aborted': break
        case 'no-speech':
          r.noSpeech = (r.noSpeech || 0) + 1
          if (MOBILE) { if (r.noSpeech >= 4) setNotice('No te oigo. Habla cerca del micrófono del móvil.'); break }
          if (r.noSpeech >= 2) setNotice((r.peak || 0) < 0.03
            ? `No llega sonido desde «${r.micLabel}». Elige otro micrófono arriba o revisa el volumen de entrada del sistema.`
            : 'Se detecta sonido pero no voz reconocible. Habla un poco más cerca del micrófono.')
          break
        case 'audio-capture':
          if (r.useTrack) { r.useTrack = false; setNotice('Reintentando con el micrófono predeterminado del sistema…') }
          else setNotice('No se puede capturar audio. Cierra otras aplicaciones que usen el micrófono o elige otro dispositivo.')
          break
        case 'network':
          r.failures = (r.failures || 0) + 1
          setConnection('offline')
          r.tryLocal('El servicio de reconocimiento de voz del navegador no responde (error de red).')
          break
        case 'service-not-allowed':
        case 'language-not-supported':
          r.tryLocal(e.error === 'language-not-supported'
            ? 'El reconocimiento en español no está disponible en la nube de este navegador.'
            : 'El navegador no permite usar su servicio de reconocimiento de voz.')
          break
        case 'not-allowed':
          setError('Permiso de micrófono denegado. Actívalo en el icono del candado de la barra de direcciones y vuelve a intentarlo.')
          setP('error'); r.active = false
          break
        default:
          r.failures = (r.failures || 0) + 1
          setNotice(`Error de reconocimiento de voz: ${e.error}.`)
      }
    }
    rec.onend = () => {
      if (r.rec !== rec) return
      setRecStatus('idle')
      if (MOBILE) setHearing(false)
      if (!r.active || r.muted || r.waitLocal) return
      if (MOBILE && r.speaking) { r.rec = null; return } // se reanuda al terminar de hablar
      const delay = Math.min(4000, 200 * 2 ** Math.min(4, r.failures || 0))
      setTimeout(() => { if (r.rec === rec && r.active && !r.muted) r.startRec() }, delay)
    }
    try {
      if (r.useTrack && r.track?.readyState === 'live') rec.start(r.track)
      else rec.start()
    } catch (err) {
      // Navegadores que no aceptan una pista como argumento: usar el micrófono predeterminado.
      dbg('no se pudo iniciar con la pista:', err.message)
      if (r.useTrack) { r.useTrack = false; try { rec.start() } catch { /* noop */ } }
    }
  }, [r, stopSpeaking, abort]) // eslint-disable-line react-hooks/exhaustive-deps
  r.startRec = startRecognition

  /** Detiene la escucha sin dar la llamada por silenciada (móvil, mientras habla el asistente). */
  const pauseRecognition = useCallback(() => {
    const rec = r.rec
    if (!rec) return
    r.rec = null
    clearTimeout(r.commitTimer)
    try { rec.abort() } catch { /* noop */ }
    r.partial = ''; setInterim(''); setRecStatus('idle'); setHearing(false)
  }, [r])
  r.pauseRec = pauseRecognition

  /** Alternativa sin servicio en la nube: reconocimiento local del navegador (Chrome reciente). */
  const tryLocal = useCallback(async (why) => {
    if (r.local) { setNotice(`${why} El reconocimiento local también ha fallado.`); return }
    if (!SR?.available) { setNotice(`${why} Este navegador no ofrece reconocimiento local: comprueba la conexión o usa Chrome/Edge actualizados.`); return }
    try {
      const st = await SR.available({ langs: [LANG], processLocally: true })
      dbg('reconocimiento local:', st)
      if (st === 'available') {
        r.local = true; r.failures = 0; setLocalStatus('active')
        setNotice('Usando el reconocimiento de voz local del navegador (sin conexión).')
        r.startRec()
      } else if (st === 'downloadable' || st === 'downloading') {
        r.waitLocal = true; setLocalStatus('downloadable')
        setNotice(`${why} Puedes descargar el reconocimiento local en español para usarlo sin conexión.`)
      } else {
        setLocalStatus('unavailable')
        setNotice(`${why} No hay reconocimiento local en español en este navegador.`)
      }
    } catch (e) { setNotice(`${why} (${e.message})`) }
  }, [r])
  r.tryLocal = tryLocal

  const installLocal = useCallback(async () => {
    if (!SR?.install) return
    setLocalStatus('installing')
    setNotice('Descargando el reconocimiento de voz local en español…')
    try {
      const ok = await SR.install({ langs: [LANG], processLocally: true })
      if (!ok) throw new Error('el navegador no lo ha instalado')
      r.local = true; r.waitLocal = false; r.failures = 0
      setLocalStatus('active')
      setNotice('Reconocimiento local listo.')
      r.startRec()
    } catch (e) {
      setLocalStatus('downloadable')
      setNotice(`No se pudo descargar el reconocimiento local: ${e.message}.`)
    }
  }, [r])

  // ── Ciclo de la llamada ─────────────────────────────────────────────
  const start = useCallback(async () => {
    const sup = voiceSupport()
    // iOS/Android solo dejan hablar a speechSynthesis si la primera locución nace del gesto
    // del usuario; tras los await siguientes ese gesto ya se ha perdido.
    if (sup.synthesis) {
      try { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; window.speechSynthesis.cancel(); window.speechSynthesis.speak(u) } catch { /* noop */ }
    }
    setError(null); setNotice(null); setTranscript([]); setBlocks([]); setInterim(''); setLocalStatus(null)
    if (!sup.recognition || !sup.synthesis || !sup.mic) {
      setError('Este navegador no soporta la conversación por voz (Web Speech API). Usa Chrome (Android), Safari (iOS), o Chrome/Edge en escritorio.')
      setP('error')
      return
    }
    setP('connecting')
    Object.assign(r, { useTrack: !MOBILE, local: false, waitLocal: false, failures: 0, noSpeech: 0, muted: false, partial: '', lastManual: null })
    try {
      await openMic(r.preferredDevice)
    } catch (e) {
      setError(e.name === 'NotAllowedError'
        ? 'Permiso de micrófono denegado. Actívalo en el icono del candado de la barra de direcciones y vuelve a intentarlo.'
        : `No se ha podido abrir el micrófono (${e.name}). Comprueba que hay uno conectado y que no lo usa otra aplicación.`)
      setP('error')
      return
    }
    r.voice = pickVoice()
    if (!r.voice) window.speechSynthesis.onvoiceschanged = () => { r.voice = pickVoice() }
    r.volume = volume
    try {
      const h = await getJSON('/api/health')
      setConnection(h.ai?.configured ? 'online' : 'no-ai')
    } catch { setConnection('offline') }
    // El reconocimiento en el dispositivo devuelve texto parcial bastante antes que el de la nube.
    if (SR.available && !MOBILE) {
      try {
        const st = await SR.available({ langs: [LANG], processLocally: true })
        dbg('reconocimiento local:', st)
        if (st === 'available') { r.local = true; setLocalStatus('active') }
        else if (st === 'downloadable') {
          setLocalStatus('downloadable')
          setNotice('Descarga el reconocimiento de voz local en español: transcribe más rápido y sin conexión.')
        }
      } catch { /* se usa el de la nube */ }
    }
    r.active = true
    setMuted(false)
    setStartedAt(Date.now())
    r.replyDone = true
    setP('listening')
    startRecognition()
    speakQueue('Hola. Soy tu copiloto de ventas y anomalías. ¿Qué quieres saber?')
  }, [r, volume, openMic, startRecognition, speakQueue])

  const end = useCallback(() => {
    r.active = false
    r.turn = (r.turn || 0) + 1
    clearTimeout(r.commitTimer)
    const rec = r.rec
    r.rec = null
    try { rec?.abort() } catch { /* noop */ }
    stopSpeaking()
    abort()
    r.stream?.getTracks().forEach((t) => t.stop())
    r.source?.disconnect()
    r.ctx?.close().catch(() => {})
    Object.assign(r, { ctx: null, analyser: null, source: null, stream: null, track: null })
    setInterim('')
    setRecStatus('idle')
    setP('ended')
  }, [r, stopSpeaking, abort])

  const toggleMute = useCallback(() => {
    const m = !r.muted
    r.muted = m
    setMuted(m)
    if (r.track) r.track.enabled = !m
    if (m) {
      clearTimeout(r.commitTimer)
      const rec = r.rec
      r.rec = null
      try { rec?.abort() } catch { /* noop */ }
      setInterim(''); r.partial = ''; setRecStatus('idle')
    } else startRecognition()
  }, [r, startRecognition])

  const setVolume = useCallback((v) => { r.volume = v; setVolumeState(v) }, [r])

  const selectDevice = useCallback(async (id) => {
    r.preferredDevice = id
    try {
      await openMic(id)
      r.useTrack = !MOBILE
      r.noSpeech = 0
      setNotice(null)
      if (r.active) startRecognition()
    } catch (e) { setNotice(`No se ha podido usar ese micrófono (${e.name}).`) }
  }, [r, openMic, startRecognition])

  /** Envía lo que llevas dicho sin esperar a que el reconocedor detecte el silencio. */
  const sendNow = useCallback(() => {
    const text = (r.partial || '').trim()
    if (!text || !r.active) return
    r.partial = ''
    r.lastManual = { at: performance.now(), words: new Set(words(text)) }
    setInterim('')
    r.handle(text)
    startRecognition() // reinicia para que la frase enviada no llegue otra vez como resultado final
  }, [r, startRecognition])
  r.sendNow = sendNow

  /** Nivel actual del micrófono (0-1) para la visualización; no provoca renders. */
  const getLevel = useCallback(() => {
    if (!r.analyser) return MOBILE && r.rec && !r.muted ? (r.partial ? 0.35 + Math.random() * 0.3 : 0.05) : 0
    const a = new Uint8Array(r.analyser.frequencyBinCount)
    r.analyser.getByteTimeDomainData(a)
    let s = 0
    for (const v of a) s += ((v - 128) / 128) ** 2
    const lvl = r.muted ? 0 : Math.min(1, Math.sqrt(s / a.length) * 4)
    r.peak = Math.max(lvl, (r.peak || 0) * 0.995)
    return lvl
  }, [r])
  const getPulse = useCallback(() => r.pulse || 0, [r])

  useEffect(() => () => { if (r.active) end() }, [r, end])

  // Vigilante de la síntesis de voz: iOS/Android a veces no disparan «onend» (audio interrumpido,
  // pestaña en segundo plano…) y la llamada se quedaba muda en «hablando» para siempre.
  useEffect(() => {
    if (!['speaking', 'processing', 'listening'].includes(phase)) return
    let idle = 0
    const i = setInterval(() => {
      const ss = window.speechSynthesis
      if (!r.active || !r.queue.size || !ss) { idle = 0; return }
      if (ss.speaking || ss.pending || performance.now() - (r.lastSpeakAt || 0) < 1500) { idle = 0; return }
      if (++idle < 2) return
      dbg('la síntesis no ha avisado del final: se recupera la escucha')
      idle = 0
      r.queue.clear()
      afterSpeech(false)
    }, 1000)
    return () => clearInterval(i)
  }, [phase, r, afterSpeech])

  // Detector de actividad de voz local: reacciona en ~50 ms, mucho antes que el reconocedor.
  useEffect(() => {
    if (!['listening', 'processing'].includes(phase)) { setHearing(false); return }
    let floor = 0.05, on = false, lastLoud = 0
    const a = new Uint8Array(128)
    const i = setInterval(() => {
      if (!r.analyser || r.muted) return
      r.analyser.getByteTimeDomainData(a)
      let s = 0
      for (const v of a) s += ((v - 128) / 128) ** 2
      const lvl = Math.sqrt(s / a.length) * 4
      floor = lvl < floor ? floor * 0.9 + lvl * 0.1 : floor * 0.998 + lvl * 0.002 // ruido de fondo
      const now = performance.now()
      if (lvl > Math.max(0.08, floor * 2.2)) lastLoud = now
      const next = now - lastLoud < 350
      if (next !== on) { on = next; setHearing(next) }
    }, 50)
    return () => { clearInterval(i); setHearing(false) }
  }, [phase, r])

  return {
    phase, muted, volume, interim, hearing, transcript, blocks, error, notice, connection, recStatus, devices, deviceId, localStatus, startedAt,
    start, end, toggleMute, setVolume, sendNow, selectDevice, installLocal, getLevel, getPulse,
  }
}

function upsertAssistant(t, text) {
  const last = t[t.length - 1]
  if (last?.role === 'assistant') return [...t.slice(0, -1), { ...last, text }]
  return [...t, { role: 'assistant', text }]
}
