import { Delta, Sparkline } from '../components/ui.jsx'
import { eur, num, score } from '../format.js'

// Sin base de comparación (periodo anterior a cero) no se muestra variación.
const delta = (cur, prev) => (prev == null || prev === 0 ? null : ((cur - prev) / Math.abs(prev)) * 100)

/** Las seis tarjetas KPI, con el mismo estilo que las tarjetas originales. */
export default function AnomalyKpis({ k, totalRestaurants, compact = false }) {
  const p = k.prev
  const absImp = (x) => Math.abs(x.perdidas) + x.ganancias
  const cards = [
    {
      key: 'total', label: 'Anomalías detectadas', value: num(k.total), d: delta(k.total, p?.total), invert: true,
      sub: p ? `vs ${num(p.total)} (periodo anterior)` : 'Sin periodo anterior comparable', spark: k.spark.total, color: 'var(--teal)',
    },
    {
      key: 'impacto', label: 'Impacto económico', value: eur(k.impacto, { sign: true, compact: true }), d: p ? delta(absImp(k), absImp(p)) : null, invert: true,
      sub: <><span className="t-down">{eur(k.perdidas, { compact: true })}</span> pérdidas · <span className="t-up">{eur(k.ganancias, { sign: true, compact: true })}</span> positivas</>,
      spark: k.spark.impacto, color: 'var(--purple)',
    },
    {
      key: 'caidas', label: 'Anomalías de caída', value: num(k.caidas), d: delta(k.caidas, p?.caidas), invert: true,
      sub: p ? `vs ${num(p.caidas)} (periodo anterior)` : 'Ventas por debajo de lo esperado', spark: k.spark.caidas, color: 'var(--down)',
    },
    {
      key: 'subidas', label: 'Anomalías de subida', value: num(k.subidas), d: delta(k.subidas, p?.subidas), invert: true,
      sub: p ? `vs ${num(p.subidas)} (periodo anterior)` : 'Ventas por encima de lo esperado', spark: k.spark.subidas, color: 'var(--up)',
    },
    {
      key: 'rest', label: 'Restaurantes afectados', value: num(k.restaurantes), d: delta(k.restaurantes, p?.restaurantes), invert: true,
      sub: `de ${num(totalRestaurants)} restaurantes`, spark: k.spark.restaurantes, color: 'var(--teal)',
    },
    {
      key: 'score', label: 'Score medio', value: score(k.score_medio), d: p && p.score_medio ? delta(k.score_medio, p.score_medio) : null, invert: false,
      sub: `${num(k.altas)} de prioridad alta`, spark: k.spark.score, color: 'var(--purple)',
    },
  ]
  return cards.map((c) => (
    <section key={c.key} className={`card kpi-card ${compact ? 'kpi-compact' : 'span-2'}`}>
      <span className="kpi-label">{c.label}</span>
      <div className="kpi-row">
        <b className="kpi-value">{c.value}</b>
        {!compact && <Delta value={c.d} invert={c.invert} decimals={0} />}
      </div>
      <small className="muted">{c.sub}</small>
      {!compact && c.spark.length > 1 && <Sparkline data={c.spark} color={c.color} height={44} />}
    </section>
  ))
}
