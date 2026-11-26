import { useId, useLayoutEffect, useRef, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { num } from '../format.js'

export function Panel({ title, actions, children, className = '' }) {
  return (
    <section className={`card panel ${className}`}>
      <div className="panel-head">
        <h2>{title}</h2>
        {actions && <div className="panel-actions">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

/** `invert`: cuando subir es malo (p. ej. más anomalías), el color se invierte. */
export function Delta({ value, invert = false, decimals = 2 }) {
  if (value == null || !Number.isFinite(value)) return <span className="delta flat">—</span>
  const up = value >= 0
  const good = invert ? !up : up
  return (
    <span className={`delta ${good ? 'up' : 'down'} ${up ? 'dir-up' : 'dir-down'}`}>
      <i className="tri" />
      {num(Math.abs(value), decimals)} %
    </span>
  )
}

export function Select({ value, onChange, options }) {
  return (
    <label className="select">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <ChevronDown size={14} />
    </label>
  )
}

/** Ancho real de un elemento, para que los SVG dibujen a tamaño real. */
export function useWidth(initial = 800) {
  const ref = useRef(null)
  const [w, setW] = useState(initial)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth)
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

/** Área con línea; `smooth` usa curvas, si no, segmentos rectos. */
export function Sparkline({ data, color = 'var(--teal)', height = 28, smooth = true, strokeWidth = 2 }) {
  const id = useId()
  const w = 100, h = height
  const max = Math.max(...data), min = Math.min(...data)
  const range = max - min || 1
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, h - 2 - ((v - min) / range) * (h - 6)])
  let line = `M ${pts[0][0]} ${pts[0][1]}`
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i]
    if (smooth) {
      const cx = (x0 + x1) / 2
      line += ` C ${cx} ${y0}, ${cx} ${y1}, ${x1} ${y1}`
    } else line += ` L ${x1} ${y1}`
  }
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="spark" style={{ height }}>
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.35" />
          <stop offset="1" stopColor={color} stopOpacity="0.04" />
        </linearGradient>
      </defs>
      <path d={`${line} L ${w} ${h} L 0 ${h} Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth={strokeWidth} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  )
}
