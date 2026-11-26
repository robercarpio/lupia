import { useState } from 'react'
import { X } from 'lucide-react'
import { useWidth } from '../components/ui.jsx'
import { CHANNEL_LABEL, dateShort, eur, num, pct, score } from '../format.js'

const padL = 58, padR = 14, top = 14

function niceTicks(min, max, n = 4) {
  const span = max - min || 1
  const step = 10 ** Math.floor(Math.log10(span / n))
  const err = (span / n) / step
  const s = step * (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1)
  const out = []
  // La primera marca queda en o por debajo del mínimo: ningún valor cae fuera del gráfico.
  for (let v = Math.floor(min / s) * s; v <= max + 1e-9; v += s) out.push(v)
  return out
}

/**
 * Ventas reales vs esperadas con anomalías marcadas sobre la línea.
 * Rojo = caídas, verde = subidas; los puntos sin marca son comportamiento normal.
 */
export default function TimelineChart({ data, onOpen, height = 330 }) {
  const [ref, W] = useWidth()
  const [hover, setHover] = useState(null)
  const [pinned, setPinned] = useState(null)
  const b = data.buckets
  if (!b.length) return <div className="empty">Sin datos en el periodo</div>
  // Sin referencia del modelo se dibujan las ventas reales de todos los días (no hay «esperadas»).
  const noRef = data.baseline === 'pendiente'
  const RK = noRef ? 'ventas_totales' : 'ventas'
  if (!b.some((x) => x[RK] > 0)) return <div className="empty">No hay ventas registradas en el periodo con estos filtros</div>

  const stripH = 30, axisH = 22
  const plotH = height - top - stripH - axisH - 8
  const plotW = Math.max(100, W - padL - padR)
  const colW = plotW / b.length
  const vals = b.flatMap((x) => [x[RK], noRef ? 0 : x.esperadas]).filter((v) => v > 0)
  const vmax = Math.max(...vals, 1), vmin = Math.min(...vals, vmax)
  const lo = vmin > vmax * 0.35 ? vmin * 0.9 : 0
  const ticks = niceTicks(lo, vmax * 1.04)
  const yMin = ticks[0], yMax = ticks[ticks.length - 1] > vmax ? ticks[ticks.length - 1] : vmax * 1.04
  // Acotado al área del gráfico para que la línea nunca invada la franja de alertas.
  const y = (v) => top + plotH - ((Math.min(Math.max(v, yMin), yMax) - yMin) / (yMax - yMin)) * plotH
  const cx = (i) => padL + colW * i + colW / 2
  const path = (k) => b.map((d, i) => `${i ? 'L' : 'M'} ${cx(i).toFixed(1)} ${y(d[k] || yMin).toFixed(1)}`).join(' ')
  const maxCount = Math.max(1, ...b.map((d) => Math.max(d.caidas, d.subidas)))
  const stripTop = top + plotH + 8, mid = stripTop + stripH / 2
  const every = Math.max(1, Math.ceil(78 / colW))
  const h = hover != null ? b[hover] : null
  const p = pinned != null ? b[pinned] : null
  const label = (d) => (data.step === 7 ? `Semana del ${dateShort(d.from)}` : dateShort(d.from, { weekday: true }))
  const dev = (d) => (d.esperadas ? d.ventas / d.esperadas - 1 : null)

  return (
    <div className="combo-wrap timeline" ref={ref}>
      <svg width={W} height={height} className="combo" onMouseLeave={() => setHover(null)} role="img"
           aria-label="Ventas reales frente a esperadas con anomalías marcadas">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={padL - 8} y={y(t) + 4} textAnchor="end" className="axis">{eur(t, { compact: true })}</text>
          </g>
        ))}
        <line x1={padL} x2={W - padR} y1={mid} y2={mid} className="strip-base" />
        <text x={padL - 8} y={mid + 4} textAnchor="end" className="axis">alertas</text>
        {b.map((d, i) => (
          <g key={d.from} onMouseEnter={() => setHover(i)} onClick={() => (d.caidas || d.subidas) && setPinned(i)}
             style={{ cursor: d.caidas || d.subidas ? 'pointer' : 'default' }}>
            <rect x={padL + colW * i} y={top} width={colW} height={plotH + stripH + 8} fill={hover === i ? 'var(--hover)' : 'transparent'} />
            {d.subidas > 0 && <rect x={cx(i) - Math.max(1, colW * 0.3)} width={Math.max(2, colW * 0.6)} y={mid - (d.subidas / maxCount) * (stripH / 2 - 1)} height={(d.subidas / maxCount) * (stripH / 2 - 1)} rx="1" fill="var(--up)" />}
            {d.caidas > 0 && <rect x={cx(i) - Math.max(1, colW * 0.3)} width={Math.max(2, colW * 0.6)} y={mid} height={(d.caidas / maxCount) * (stripH / 2 - 1)} rx="1" fill="var(--down)" />}
            {i % every === 0 && <text x={cx(i)} y={height - 6} className="axis" textAnchor="middle">{dateShort(d.from, { year: b.length > 60 })}</text>}
          </g>
        ))}
        {!noRef && <path d={path('esperadas')} fill="none" stroke="var(--teal)" strokeWidth="2" strokeDasharray="5 4" pointerEvents="none" />}
        <path d={path(RK)} fill="none" stroke="var(--purple)" strokeWidth="2.2" strokeLinejoin="round" pointerEvents="none" />
        {hover != null && <line x1={cx(hover)} x2={cx(hover)} y1={top} y2={top + plotH} className="guide" />}
        {b.map((d, i) => {
          const n = d.caidas + d.subidas
          if (!n) return hover === i ? <circle key={i} cx={cx(i)} cy={y(d[RK])} r="4" fill="var(--purple)" stroke="var(--surface)" strokeWidth="2" pointerEvents="none" /> : null
          const down = d.caidas >= d.subidas
          return (
            <circle key={i} cx={cx(i)} cy={y(d[RK])} r={Math.min(8, 3.6 + Math.sqrt(n) * 1.2) + (hover === i ? 1.5 : 0)}
                    className="anom-dot" fill={down ? 'var(--down)' : 'var(--up)'} stroke="var(--surface)" strokeWidth="1.6"
                    onMouseEnter={() => setHover(i)} onClick={() => setPinned(i)} />
          )
        })}
      </svg>
      {h && pinned == null && (
        <div className="tooltip" style={{ left: Math.min(Math.max(cx(hover), 110), W - 110), top: y(h[RK]) }}>
          <b>{label(h)}</b>
          <span><i className="sq" style={{ background: 'var(--purple)' }} />Reales: {eur(h[RK])}{h.tickets_totales != null && ` · ${num(h.tickets_totales)} tickets`}</span>
          {noRef
            ? <span className="tt-muted">Esperadas: pendientes del modelo</span>
            : <span><i className="sq" style={{ background: 'var(--teal)' }} />Esperadas: {eur(h.esperadas)} ({pct(dev(h))})</span>}
          {(h.caidas > 0 || h.subidas > 0)
            ? <span><i className="sq" style={{ background: 'var(--down)' }} />{h.caidas} caídas · <i className="sq" style={{ background: 'var(--up)' }} />{h.subidas} subidas · clic para ver</span>
            : <span className="tt-muted">Sin anomalías</span>}
        </div>
      )}
      {p && (
        <div className="pop" style={{ left: Math.min(Math.max(cx(pinned), 160), W - 160), top: Math.max(10, y(p[RK]) - 12) }}>
          <div className="pop-head">
            <b>{label(p)}</b>
            <button className="icon-mini" onClick={() => setPinned(null)} aria-label="Cerrar"><X size={14} /></button>
          </div>
          <small className="muted">{p.caidas} caídas · {p.subidas} subidas · impacto {eur(p.impacto, { sign: true })}</small>
          <ul>
            {p.top.map((a) => (
              <li key={a.id}>
                <button onClick={() => { setPinned(null); onOpen?.(a.id) }}>
                  <i className={`dot ${a.tipo}`} />
                  <span><b>{a.restaurante_id}</b> · {CHANNEL_LABEL[a.canal]}{data.step === 7 ? ` · ${dateShort(a.fecha, { year: false })}` : ''}</span>
                  <span className={a.impacto < 0 ? 't-down' : 't-up'}>{pct(a.desviacion_pct)}</span>
                  <small>{score(a.score)}</small>
                </button>
              </li>
            ))}
          </ul>
          {p.caidas + p.subidas > p.top.length && <small className="muted">Se muestran las {p.top.length} de mayor score</small>}
        </div>
      )}
    </div>
  )
}
