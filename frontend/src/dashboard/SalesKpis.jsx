import { Delta, Sparkline } from '../components/ui.jsx'
import { dateShort, eur, num } from '../format.js'

const pctDelta = (v) => (v == null ? null : v * 100)

/**
 * KPIs de ventas reales (series_summary en Databricks), con el mismo estilo que las tarjetas de anomalías.
 * La variación compara con el periodo anterior de igual duración; subir es bueno (no se invierte el color).
 * Los filtros propios de anomalías (tipo, score, impacto, prioridad) no se aplican a las ventas.
 */
export default function SalesKpis({ k, totalRestaurants, compact = false }) {
  const p = k.prev
  const v = k.variacion || {}
  const vs = (txt) => (p ? `vs ${txt} (periodo anterior)` : 'Sin periodo anterior comparable')
  const cards = [
    { key: 'ventas', label: 'Ventas totales', value: eur(k.ventas, { compact: true }), d: pctDelta(v.ventas),
      sub: vs(eur(p?.ventas, { compact: true })), spark: k.spark?.ventas, color: 'var(--teal)' },
    { key: 'tickets', label: 'Tickets', value: num(k.tickets), d: pctDelta(v.tickets),
      sub: vs(num(p?.tickets)), spark: k.spark?.tickets, color: 'var(--purple)' },
    { key: 'tm', label: 'Ticket medio', value: eur(k.ticket_medio, { decimals: 2 }), d: pctDelta(v.ticket_medio),
      sub: vs(eur(p?.ticket_medio, { decimals: 2 })), spark: k.spark?.ticket_medio, color: 'var(--teal)' },
    { key: 'dia', label: 'Venta media diaria', value: eur(k.venta_media_dia, { compact: true }), d: pctDelta(v.venta_media_dia),
      sub: `${num(k.dias)} días con ventas de ${num(k.dias_periodo)}`, spark: k.spark?.ventas, color: 'var(--purple)' },
    { key: 'rest', label: 'Restaurantes con ventas', value: num(k.restaurantes), d: pctDelta(v.restaurantes),
      sub: totalRestaurants ? `de ${num(totalRestaurants)} restaurantes` : vs(num(p?.restaurantes)), spark: null },
    { key: 'best', label: 'Mejor día', value: k.mejor_dia ? dateShort(k.mejor_dia.fecha, { weekday: true, year: false }) : '—', d: null,
      sub: k.mejor_dia ? `${eur(k.mejor_dia.ventas, { compact: true })} vendidos` : 'Sin ventas en el periodo', spark: null },
  ]
  return cards.map((c) => (
    <section key={c.key} className={`card kpi-card ${compact ? 'kpi-compact' : 'span-2'}`}>
      <span className="kpi-label">{c.label}</span>
      <div className="kpi-row">
        <b className="kpi-value">{c.value}</b>
        {!compact && c.d != null && <Delta value={c.d} decimals={1} />}
      </div>
      <small className="muted">{c.sub}</small>
      {!compact && c.spark?.length > 1 && <Sparkline data={c.spark} color={c.color} height={44} />}
    </section>
  ))
}
