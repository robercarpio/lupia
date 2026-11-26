import { useEffect, useState } from 'react'
import { X, Sparkles, CalendarDays, Lightbulb, Eye, ClipboardCheck, History, Store } from 'lucide-react'
import { useApi, } from '../api.js'
import { useWidth } from '../components/ui.jsx'
import { CHANNEL_LABEL, dateLong, dateShort, eur, num, pct, score, PRIORIDAD_LABEL } from '../format.js'

const MOTIVO = {
  festivo: 'Festivo', vispera_festivo: 'Víspera de festivo', fecha_especial: 'Fecha especial', navidad: 'Navidad',
  semana_santa: 'Semana Santa', apertura: 'Periodo de apertura', cierre: 'Periodo previo a cierre', reforma: 'Reforma / cierre temporal',
  canal_pequeno: 'Canal pequeño', dia_neutro: 'Evento general', sin_historico: 'Sin histórico suficiente', fuera_periodo: 'Fuera del periodo evaluado',
}

/** Histórico ±8 semanas: comportamiento normal, días explicados y la desviación detectada. */
export function DetailChart({ serie, fecha, height = 220, onOpen }) {
  const [ref, W] = useWidth(480)
  const [hover, setHover] = useState(null)
  if (!serie?.length) return null
  const padL = 50, padR = 10, top = 12, padB = 30
  const plotW = Math.max(80, W - padL - padR), plotH = height - top - padB
  const first = new Date(serie[0].fecha + 'T00:00:00Z'), last = new Date(serie[serie.length - 1].fecha + 'T00:00:00Z')
  const span = Math.max(1, (last - first) / 864e5)
  const x = (iso) => padL + (((new Date(iso + 'T00:00:00Z') - first) / 864e5) / span) * plotW
  const max = Math.max(...serie.map((d) => Math.max(d.ventas, d.esperadas || 0))) * 1.08
  const y = (v) => top + plotH - (v / max) * plotH
  const line = (k) => {
    let d = '', prev = null
    for (const p of serie) {
      if (p[k] == null) { prev = null; continue }
      const gap = prev && (new Date(p.fecha + 'T00:00:00Z') - new Date(prev + 'T00:00:00Z')) / 864e5 > 1
      d += `${!prev || gap ? 'M' : 'L'} ${x(p.fecha).toFixed(1)} ${y(p[k]).toFixed(1)} `
      prev = p.fecha
    }
    return d
  }
  const ticks = [0, max / 2, max].map((v) => Math.round(v / 100) * 100)
  const h = hover != null ? serie[hover] : null
  return (
    <div className="combo-wrap" ref={ref}>
      <svg width={W} height={height} className="combo" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}><line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} className="grid-line" />
            <text x={padL - 6} y={y(t) + 4} className="axis" textAnchor="end">{eur(t, { compact: true })}</text></g>
        ))}
        <rect x={x(fecha) - 7} y={top} width={14} height={plotH} rx="4" className="focus-band" />
        <path d={line('esperadas')} fill="none" stroke="var(--teal)" strokeWidth="1.8" strokeDasharray="4 3" />
        <path d={line('ventas')} fill="none" stroke="var(--purple)" strokeWidth="2" strokeLinejoin="round" />
        {serie.map((p, i) => {
          const isAnom = !!p.anomalia, isFocus = p.fecha === fecha
          const explained = p.motivo && p.motivo !== 'normal'
          return (
            <g key={p.fecha}>
              <rect x={x(p.fecha) - plotW / serie.length / 2} y={top} width={plotW / serie.length} height={plotH} fill="transparent" onMouseEnter={() => setHover(i)}
                    onClick={() => isAnom && !isFocus && onOpen?.(p.anomalia_id)} style={{ cursor: isAnom && !isFocus ? 'pointer' : 'default' }} />
              {isAnom ? <circle cx={x(p.fecha)} cy={y(p.ventas)} r={isFocus ? 6 : 4} fill={p.anomalia === 'caida' ? 'var(--down)' : 'var(--up)'} stroke="var(--surface)" strokeWidth="1.5" pointerEvents="none" />
                : explained ? <circle cx={x(p.fecha)} cy={y(p.ventas)} r="3" fill="var(--surface)" stroke="var(--muted)" strokeWidth="1.3" pointerEvents="none" />
                : hover === i ? <circle cx={x(p.fecha)} cy={y(p.ventas)} r="3.5" fill="var(--purple)" pointerEvents="none" /> : null}
            </g>
          )
        })}
        {[serie[0], serie[Math.floor(serie.length / 2)], serie[serie.length - 1]].map((p, k) => (
          <text key={p.fecha} x={x(p.fecha)} y={height - 10} className="axis" textAnchor={['start', 'middle', 'end'][k]}>{dateShort(p.fecha, { year: false })}</text>
        ))}
      </svg>
      {h && (
        <div className="tooltip" style={{ left: Math.min(Math.max(x(h.fecha), 90), W - 90), top: y(h.ventas) }}>
          <b>{dateShort(h.fecha, { weekday: true })}</b>
          <span>Reales: {eur(h.ventas)} · {num(h.tickets)} tickets</span>
          {h.esperadas != null && <span>Esperadas: {eur(h.esperadas)} ({pct(h.ventas / h.esperadas - 1)})</span>}
          {h.anomalia ? <span>{h.anomalia === 'caida' ? 'Anomalía de caída' : 'Anomalía de subida'}</span>
            : h.motivo !== 'normal' ? <span className="tt-muted">No evaluado: {MOTIVO[h.motivo] || h.motivo}</span> : <span className="tt-muted">Comportamiento normal</span>}
        </div>
      )}
    </div>
  )
}

function Fact({ label, value, sub, tone }) {
  return (
    <div className="fact">
      <small>{label}</small>
      <b className={tone}>{value}</b>
      {sub && <small className="muted">{sub}</small>}
    </div>
  )
}

export default function AnomalyDrawer({ id, onClose, onOpen, onAsk }) {
  const { data: a, error, loading } = useApi(id ? `/api/anomalies/${encodeURIComponent(id)}` : null)
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  const ready = a && a.id === id
  const caida = ready && a.tipo === 'caida'
  return (
    <div className="drawer-layer" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Detalle de la anomalía">
        <header className="drawer-head">
          <div>
            <small className="muted">Detalle de la anomalía</small>
            <h2>{ready ? `${a.restaurante_id} · ${CHANNEL_LABEL[a.canal]}` : id?.split('-').slice(0, 2).join(' · ')}</h2>
            {ready && <p className="muted">{dateLong(a.fecha)} · {a.provincia ? `${a.provincia}, ${a.ccaa}` : 'Sin provincia en el maestro'}</p>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Cerrar"><X size={18} /></button>
        </header>
        {error && <div className="empty error">{error.message}</div>}
        {!ready && loading && <div className="drawer-body"><div className="skeleton" /><div className="skeleton tall" /></div>}
        {ready && (
          <div className="drawer-body">
            <div className="drawer-badges">
              <span className={`pill ${a.tipo}`}>{caida ? 'Caída' : 'Subida'} en {a.metrica}</span>
              <span className={`score-chip p-${a.prioridad}`}>Score {score(a.score)}</span>
              <span className={`prio p-${a.prioridad}`}>Prioridad {PRIORIDAD_LABEL[a.prioridad].toLowerCase()}</span>
              {a.racha > 1 && <span className="tag">{a.racha} días seguidos</span>}
            </div>
            <p className="lead">{a.detalle.resumen}</p>

            <div className="facts">
              <Fact label="Ventas reales" value={eur(a.ventas)} sub={`Esperadas ${eur(a.ventas_esperadas)}`} />
              <Fact label="Desviación" value={pct(a.desviacion_pct)} sub={eur(a.desviacion, { sign: true })} tone={caida ? 't-down' : 't-up'} />
              <Fact label="Impacto económico" value={eur(a.impacto, { sign: true })} sub="Reales − esperadas" tone={a.impacto < 0 ? 't-down' : 't-up'} />
              <Fact label="Tickets reales" value={num(a.tickets)} sub={`Esperados ${num(a.tickets_esperados)} (${pct(a.desviacion_tickets_pct)})`} />
              <Fact label="Ticket medio" value={eur(a.ticket_medio, { decimals: 2 })} sub={a.tickets_esperados ? `Habitual ${eur(a.ventas_esperadas / a.tickets_esperados, { decimals: 2 })}` : null} />
              <Fact label="Intensidad" value={`${num(a.z, 1)}×`} sub={`su variabilidad habitual (±${pct(a.variabilidad_habitual.ventas, { sign: false })})`} />
            </div>

            <section className="d-sec">
              <h3>Comportamiento histórico</h3>
              <DetailChart serie={a.serie} fecha={a.fecha} onOpen={onOpen} />
              <div className="legend small">
                <span><i className="ln" />Reales</span><span><i className="dash" />Esperadas</span>
                <span><i className="sq" style={{ background: 'var(--down)' }} />Caída</span><span><i className="sq" style={{ background: 'var(--up)' }} />Subida</span>
                <span><i className="sq hollow" />Día con explicación (no evaluado)</span>
              </div>
              {a.referencia.length > 0 && (
                <p className="muted small">Referencia: mismo día de la semana en las semanas previas → {a.referencia.map((r) => eur(r.ventas, { compact: true })).join(' · ')}</p>
              )}
            </section>

            <section className="d-sec">
              <h3><Eye size={15} />Hechos observados</h3>
              <ul className="d-list">{a.detalle.hechos.map((t) => <li key={t}>{t}</li>)}</ul>
            </section>
            <section className="d-sec">
              <h3><CalendarDays size={15} />Contexto</h3>
              <ul className="d-list">{a.detalle.contexto.map((t) => <li key={t}>{t}</li>)}</ul>
              <div className="compare-grid">
                {a.otros_canales.map((o) => (
                  <div key={o.canal} className="cmp"><small>{CHANNEL_LABEL[o.canal]} ese día</small><b className={Math.abs(o.desv) >= 0.25 ? (o.desv < 0 ? 't-down' : 't-up') : ''}>{pct(o.desv)}</b></div>
                ))}
                <div className="cmp"><small>Su zona en {CHANNEL_LABEL[a.canal].toLowerCase()}</small><b>{pct(a.cohorte)}</b></div>
                <div className="cmp"><small>Otras alertas en {a.ccaa || 'su zona'} ese día</small><b>{num(a.zona_mismo_dia.total)}</b></div>
              </div>
            </section>
            <section className="d-sec hypo">
              <h3><Lightbulb size={15} />Posibles factores <em>hipótesis, no confirmadas</em></h3>
              <ul className="d-list">{a.detalle.hipotesis.map((t) => <li key={t}>{t}</li>)}</ul>
              <p className="muted small">Los datos no incluyen la causa real de las desviaciones: estas hipótesis solo orientan la revisión.</p>
            </section>
            <section className="d-sec reco">
              <h3><ClipboardCheck size={15} />Recomendación de revisión</h3>
              <p>{a.detalle.recomendacion}</p>
            </section>
            <section className="d-sec">
              <h3><History size={15} />Historial del restaurante en {CHANNEL_LABEL[a.canal].toLowerCase()}</h3>
              {a.historial.total ? (
                <ul className="mini-list">
                  {a.historial.recientes.map((h) => (
                    <li key={h.id}><button onClick={() => onOpen(h.id)}>
                      <i className={`dot ${h.tipo}`} /><span>{dateShort(h.fecha, { weekday: true })}</span>
                      <span className={h.impacto < 0 ? 't-down' : 't-up'}>{pct(h.desviacion_pct)}</span><small>{score(h.score)}</small>
                    </button></li>
                  ))}
                  {a.historial.total > a.historial.recientes.length && <li className="muted small">y {a.historial.total - a.historial.recientes.length} más</li>}
                </ul>
              ) : <p className="muted small">Es la única alerta de este restaurante y canal en todo el histórico.</p>}
            </section>
            <section className="d-sec">
              <h3><Store size={15} />Restaurante</h3>
              <div className="kv">
                {[['Tipología', a.restaurante.tipologia], ['Tamaño', a.restaurante.tamano], ['Ubicación', a.restaurante.ubicacion], ['Imagen', a.restaurante.imagen],
                  ['Terraza', a.restaurante.terraza], ['Autoking', a.restaurante.autoking], ['Radio de reparto', a.restaurante.distancia_reparto], ['Estado', a.restaurante.estado],
                  ['Primera venta', dateShort(a.restaurante.primera_venta)], ['Última venta', dateShort(a.restaurante.ultima_venta)]].map(([k, v]) => (
                  <div key={k}><small>{k}</small><span>{v || '—'}</span></div>
                ))}
              </div>
            </section>
            <button className="btn-ask" onClick={() => onAsk(a)}><Sparkles size={16} />Preguntar al AI Assistant sobre esta anomalía</button>
          </div>
        )}
      </aside>
    </div>
  )
}
