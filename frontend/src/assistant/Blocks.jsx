import { ArrowUpRight, LayoutDashboard, SlidersHorizontal } from 'lucide-react'
import AnomalyKpis from '../dashboard/AnomalyKpis.jsx'
import SalesKpis from '../dashboard/SalesKpis.jsx'
import TimelineChart from '../dashboard/TimelineChart.jsx'
import { DetailChart } from '../dashboard/AnomalyDrawer.jsx'
import { CHANNEL_COLOR, CHANNEL_LABEL, dateShort, eur, num, pct, score } from '../format.js'

const DIM_LABEL = {
  dia_semana: (k) => k, canal: (k) => CHANNEL_LABEL[k] || k, tipo: (k) => (k === 'caida' ? 'Caídas' : 'Subidas'), prioridad: (k) => `Prioridad ${k}`, mes: (k) => dateShort(`${k}-01`).replace(/^1 /, '') }

function Shell({ block, children, onDashboard, wide }) {
  return (
    <section className={`card ai-block ${wide ? 'wide' : ''}`}>
      <header>
        <div><h4>{block.title}</h4>{block.subtitle && <small className="muted">{block.subtitle}</small>}</div>
        {block.filters && onDashboard && (
          <button className="btn-chip" onClick={() => onDashboard(block.filters)} title="Abrir estos resultados en el dashboard">
            <LayoutDashboard size={13} />Ver en el dashboard
          </button>
        )}
      </header>
      {children}
    </section>
  )
}

function AnomalyRows({ rows, onOpen }) {
  return (
    <div className="table-wrap scroll-x">
      <table className="dtable compact">
        <thead><tr><th>Restaurante</th><th>Fecha</th><th>Canal</th><th>Tipo</th><th className="r">Reales</th><th className="r">Esperadas</th><th className="r">Desv.</th><th className="r">Impacto</th><th className="r">Score</th></tr></thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id} className="clickable" onClick={() => onOpen(a.id)}>
              <td><b>{a.restaurante_id}</b><small className="muted block">{a.provincia || '—'}</small></td>
              <td className="nowrap">{dateShort(a.fecha)}</td>
              <td><span className={`tag ch-${a.canal}`}>{CHANNEL_LABEL[a.canal]}</span></td>
              <td><span className={`pill ${a.tipo}`}>{a.tipo === 'caida' ? 'Caída' : 'Subida'}</span></td>
              <td className="r">{eur(a.ventas)}</td>
              <td className="r muted">{eur(a.ventas_esperadas)}</td>
              <td className={`r ${a.desviacion_pct < 0 ? 't-down' : 't-up'}`}>{pct(a.desviacion_pct)}</td>
              <td className={`r ${a.impacto < 0 ? 't-down' : 't-up'}`}><b>{eur(a.impacto, { sign: true })}</b></td>
              <td className="r"><span className={`score-chip p-${a.prioridad}`}>{score(a.score)}</span></td>
            </tr>
          ))}
          {!rows.length && <tr><td colSpan="9" className="empty">Sin resultados</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

const Var = ({ v }) => (v == null ? <span className="muted">—</span> : <span className={v < 0 ? 't-down' : 't-up'}>{pct(v, { decimals: 1 })}</span>)

function SalesTable({ rows }) {
  return (
    <div className="table-wrap scroll-x">
      <table className="dtable compact">
        <thead><tr><th>#</th><th>Restaurante</th><th className="r">Ventas</th><th className="r">Tickets</th><th className="r">Ticket medio</th><th className="r">vs anterior</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.restaurante_id}>
              <td className="muted">{i + 1}</td>
              <td><b>{r.restaurante_id}</b><small className="muted block">{r.provincia || 'Sin maestro'}</small></td>
              <td className="r"><b>{eur(r.ventas)}</b></td>
              <td className="r">{num(r.tickets)}</td>
              <td className="r">{eur(r.ticket_medio, { decimals: 2 })}</td>
              <td className="r"><Var v={r.variacion} /></td>
            </tr>
          ))}
          {!rows.length && <tr><td colSpan="6" className="empty">Sin ventas con estos filtros</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function Block({ block, onOpen, onDashboard, meta }) {
  switch (block.type) {
    case 'sales_kpis':
      return <Shell block={block} onDashboard={onDashboard} wide><div className="ai-kpis"><SalesKpis k={block.data} compact /></div>
        <small className="muted">{block.data.prevRange ? `Variación frente a ${dateShort(block.data.prevRange.from)} – ${dateShort(block.data.prevRange.to)}: ` : ''}
          {block.data.variacion?.ventas != null ? pct(block.data.variacion.ventas, { decimals: 1 }) : 'sin periodo anterior comparable'}</small></Shell>
    case 'sales_ranking':
      return <Shell block={block} onDashboard={onDashboard} wide><SalesTable rows={block.rows} /></Shell>
    case 'sales_compare':
      return (
        <Shell block={block} wide>
          <SalesTable rows={block.rows.map((r) => ({ ...r, ...(r.ventas_periodo || { ventas: 0, tickets: 0, ticket_medio: null, variacion: null }) }))} />
          <small className="muted">Anomalías en el periodo: {block.rows.map((r) => `${r.restaurante_id} ${num(r.anomalias.total)}`).join(' · ')}</small>
        </Shell>
      )
    case 'sales_bars': {
      const max = Math.max(1, ...block.rows.map((r) => r.ventas))
      const lab = DIM_LABEL[block.dimension] || ((k) => k)
      return (
        <Shell block={block} onDashboard={onDashboard}>
          <div className="bars">
            {block.rows.map((r) => (
              <div key={r.clave} className="bar-row">
                <span className="bar-k">{lab(r.clave)}</span>
                <div className="bar-track"><div style={{ width: `${(r.ventas / max) * 100}%`, background: CHANNEL_COLOR[r.clave] || 'var(--teal)' }} /></div>
                <b>{eur(r.ventas, { compact: true })}</b>
                <small><Var v={r.variacion} /></small>
              </div>
            ))}
            {!block.rows.length && <span className="empty">Sin resultados</span>}
          </div>
          <div className="ch-legend muted small"><span>Ventas reales</span><span>Variación frente al periodo anterior</span></div>
        </Shell>
      )
    }
    case 'kpis':
      return <Shell block={block} onDashboard={onDashboard} wide><div className="ai-kpis"><AnomalyKpis k={block.data} totalRestaurants={meta?.restaurantes} compact /></div></Shell>
    case 'anomalies':
      return <Shell block={block} onDashboard={onDashboard} wide><AnomalyRows rows={block.rows} onOpen={onOpen} /></Shell>
    case 'ranking': {
      const max = Math.max(1, ...block.rows.map((r) => (block.sort === 'impacto' ? r.impacto_abs : block.sort === 'score' ? r.score_medio : r.anomalias)))
      return (
        <Shell block={block} onDashboard={onDashboard} wide>
          <ol className="rank">
            {block.rows.map((r, i) => {
              const v = block.sort === 'impacto' ? r.impacto_abs : block.sort === 'score' ? r.score_medio : r.anomalias
              return (
                <li key={r.restaurante_id}>
                  <span className="rank-n">{i + 1}</span>
                  <div className="rank-main">
                    <div className="rank-top"><b>{r.restaurante_id}</b><small className="muted">{r.provincia || 'Sin maestro'}</small></div>
                    <div className="imp-bar"><div style={{ width: `${(v / max) * 100}%`, background: CHANNEL_COLOR[r.canal_principal] }} /></div>
                  </div>
                  <div className="rank-stats">
                    <span><b>{num(r.anomalias)}</b> <small className="muted">alertas</small></span>
                    <span className={r.impacto < 0 ? 't-down' : 't-up'}>{eur(r.impacto, { sign: true, compact: true })}</span>
                    <span className={`tag ch-${r.canal_principal}`}>{CHANNEL_LABEL[r.canal_principal]}</span>
                    <span className={`pill ${r.tipo_predominante}`}>{r.tipo_predominante === 'caida' ? 'Caídas' : 'Subidas'}</span>
                  </div>
                </li>
              )
            })}
            {!block.rows.length && <li className="empty">Sin resultados</li>}
          </ol>
        </Shell>
      )
    }
    case 'bars': {
      const max = Math.max(1, ...block.rows.map((r) => r.total))
      const lab = DIM_LABEL[block.dimension] || ((k) => k)
      return (
        <Shell block={block} onDashboard={onDashboard}>
          <div className="bars">
            {block.rows.map((r) => (
              <div key={r.clave} className="bar-row">
                <span className="bar-k">{lab(r.clave)}</span>
                <div className="bar-track">
                  <div className="bg-down" style={{ width: `${(r.caidas / max) * 100}%` }} />
                  <div className="bg-up" style={{ width: `${(r.subidas / max) * 100}%` }} />
                </div>
                <b>{num(r.total)}</b>
                <small className={r.impacto < 0 ? 't-down' : 't-up'}>{eur(r.impacto, { sign: true, compact: true })}</small>
              </div>
            ))}
            {!block.rows.length && <span className="empty">Sin resultados</span>}
          </div>
          <div className="ch-legend muted small"><span><i className="sq bg-down" />Caídas</span><span><i className="sq bg-up" />Subidas</span><span>Impacto neto</span></div>
        </Shell>
      )
    }
    case 'timeline':
      return <Shell block={block} onDashboard={onDashboard} wide><TimelineChart data={block.data} onOpen={onOpen} height={260} /></Shell>
    case 'anomaly': {
      const a = block.anomaly
      return (
        <section className="card ai-block anomaly-card wide">
          <header>
            <div>
              <h4>{a.restaurante_id} · {CHANNEL_LABEL[a.canal]} · {dateShort(a.fecha, { weekday: true })}</h4>
              <small className="muted">{a.provincia ? `${a.provincia}, ${a.ccaa}` : 'Sin provincia en el maestro'}</small>
            </div>
            <button className="btn-chip" onClick={() => onOpen(a.id)}><ArrowUpRight size={13} />Ver detalle</button>
          </header>
          <div className="anom-stats">
            <div><small>Reales</small><b>{eur(a.ventas)}</b></div>
            <div><small>Esperadas</small><b>{eur(a.ventas_esperadas)}</b></div>
            <div><small>Desviación</small><b className={a.desviacion_pct < 0 ? 't-down' : 't-up'}>{pct(a.desviacion_pct)}</b></div>
            <div><small>Impacto</small><b className={a.impacto < 0 ? 't-down' : 't-up'}>{eur(a.impacto, { sign: true })}</b></div>
            <div><small>Score</small><b>{score(a.score)}</b></div>
          </div>
          <DetailChart serie={block.serie} fecha={a.fecha} height={170} onOpen={onOpen} />
          <p className="small">{block.detalle.resumen}</p>
          {block.detalle.hipotesis?.length > 0 && <p className="small muted"><b>Hipótesis (no confirmada):</b> {block.detalle.hipotesis[0]}</p>}
        </section>
      )
    }
    case 'compare': {
      const { a, b } = block.data
      const v = block.data.ventas
      const rows = [
        ...(v ? [['Ventas', v.a.ventas, v.b.ventas, (x) => eur(x)], ['Tickets', v.a.tickets, v.b.tickets, num],
          ['Ticket medio', v.a.ticket_medio, v.b.ticket_medio, (x) => eur(x, { decimals: 2 })]] : []),
        ['Anomalías', a.total, b.total, num], ['Caídas', a.caidas, b.caidas, num], ['Subidas', a.subidas, b.subidas, num],
        ['Impacto neto', a.impacto, b.impacto, (v) => eur(v, { sign: true })], ['Restaurantes', a.restaurantes, b.restaurantes, num], ['Score medio', a.score_medio, b.score_medio, score],
      ]
      return (
        <Shell block={block}>
          <table className="dtable compact cmp-table">
            <thead><tr><th /><th className="r">{dateShort(a.from)} – {dateShort(a.to)}</th><th className="r">{dateShort(b.from)} – {dateShort(b.to)}</th><th className="r">Cambio</th></tr></thead>
            <tbody>
              {rows.map(([l, x, y, f]) => (
                <tr key={l}><td>{l}</td><td className="r">{f(x)}</td><td className="r"><b>{f(y)}</b></td>
                  <td className="r muted">{x ? pct((y - x) / Math.abs(x)) : '—'}</td></tr>
              ))}
              {a.canales.map((c, i) => (
                <tr key={c.canal}><td><i className="sq" style={{ background: CHANNEL_COLOR[c.canal] }} /> {CHANNEL_LABEL[c.canal]}</td><td className="r">{num(c.total)}</td><td className="r"><b>{num(b.canales[i].total)}</b></td>
                  <td className="r muted">{c.total ? pct((b.canales[i].total - c.total) / c.total) : '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </Shell>
      )
    }
    case 'filters':
      return (
        <section className="card ai-block filters-card">
          <span className="fc-icon"><SlidersHorizontal size={16} /></span>
          <div><h4>{block.title}</h4><small className="muted">{block.subtitle} · {num(block.total)} anomalías</small></div>
          <button className="btn-apply" onClick={() => onDashboard(block.filters)}>Abrir en el dashboard</button>
        </section>
      )
    default:
      return null
  }
}
