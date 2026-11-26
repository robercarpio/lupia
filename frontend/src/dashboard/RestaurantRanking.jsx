import { useEffect, useState } from 'react'
import { Delta, Panel, Select } from '../components/ui.jsx'
import { filtersQuery, useApi } from '../api.js'
import { CHANNEL_LABEL, dateShort, eur, num, score } from '../format.js'

// Órdenes de ventas (series_summary) y de anomalías (alertas del modelo).
const SORTS = [
  { value: 'ventas', label: 'Ventas', kind: 'sales' },
  { value: 'variacion_asc', label: 'Mayor caída de ventas', kind: 'sales' },
  { value: 'variacion', label: 'Mayor crecimiento', kind: 'sales' },
  { value: 'ticket_medio', label: 'Ticket medio', kind: 'sales' },
  { value: 'anomalias', label: 'Nº de anomalías', kind: 'anom' },
  { value: 'impacto', label: 'Impacto de anomalías', kind: 'anom' },
  { value: 'score', label: 'Score medio', kind: 'anom' },
]

function SalesRows({ rows, filters, onFilter, mobile, loading, sort }) {
  const val = (r) => (sort === 'ticket_medio' ? r.ticket_medio : sort.startsWith('variacion') ? Math.abs(r.variacion || 0) : r.ventas)
  const max = Math.max(1e-9, ...rows.map(val))
  const pick = (r) => onFilter({ restaurante: filters.restaurante === r.restaurante_id ? '' : r.restaurante_id })
  if (mobile) {
    return (
      <ol className={`m-list ${loading ? 'loading' : ''}`}>
        {rows.map((r, i) => (
          <li key={r.restaurante_id}>
            <button className={`m-card ${filters.restaurante === r.restaurante_id ? 'sel' : ''}`} onClick={() => pick(r)}>
              <span className="rank-n">{i + 1}</span>
              <div className="m-card-main">
                <div className="m-card-top"><b>{r.restaurante_id}</b></div>
                <small className="muted">{r.provincia ? `${r.provincia} · ${r.ccaa}` : 'Sin maestro'} · {num(r.tickets)} tickets</small>
              </div>
              <div className="m-card-side">
                <b>{eur(r.ventas, { compact: true })}</b>
                {r.variacion != null ? <Delta value={r.variacion * 100} decimals={1} /> : <small className="muted">sin comparación</small>}
              </div>
            </button>
          </li>
        ))}
        {!rows.length && !loading && <li className="empty">No hay ventas con estos filtros</li>}
      </ol>
    )
  }
  return (
    <div className={`table-wrap scroll-x ${loading ? 'loading' : ''}`}>
      <table className="dtable ranking">
        <thead>
          <tr>
            <th>#</th><th>Restaurante</th><th>Provincia</th><th>Comunidad</th><th className="r">Ventas</th>
            <th className="r">Tickets</th><th className="r">Ticket medio</th><th className="r" title="Variación de ventas frente al periodo anterior de igual duración">vs anterior</th><th className="r">Días</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.restaurante_id} className="clickable" onClick={() => pick(r)} title="Filtrar el dashboard por este restaurante">
              <td className="muted">{i + 1}</td>
              <td><b>{r.restaurante_id}</b></td>
              <td>{r.provincia || <span className="muted">Sin maestro</span>}</td>
              <td className="muted">{r.ccaa || '—'}</td>
              <td className="r"><div className="cell-bar"><div style={{ width: `${(val(r) / max) * 100}%` }} /></div><b>{eur(r.ventas)}</b></td>
              <td className="r">{num(r.tickets)}</td>
              <td className="r">{eur(r.ticket_medio, { decimals: 2 })}</td>
              <td className="r">{r.variacion != null ? <Delta value={r.variacion * 100} decimals={1} /> : <span className="muted">—</span>}</td>
              <td className="r muted">{num(r.dias)}</td>
            </tr>
          ))}
          {!rows.length && !loading && <tr><td colSpan="9" className="empty">No hay ventas con estos filtros</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function AnomalyRows({ rows, filters, onFilter, mobile, loading, sort, anomalies }) {
  const max = Math.max(1, ...rows.map((r) => (sort === 'impacto' ? r.impacto_abs : sort === 'score' ? r.score_medio : r.anomalias)))
  const empty = anomalies ? 'No hay anomalías con estos filtros' : 'El modelo todavía no ha escrito anomalías en Databricks'
  const pick = (r) => onFilter({ restaurante: filters.restaurante === r.restaurante_id ? '' : r.restaurante_id })
  if (mobile) {
    return (
      <ol className={`m-list ${loading ? 'loading' : ''}`}>
        {rows.map((r, i) => (
          <li key={r.restaurante_id}>
            <button className={`m-card ${filters.restaurante === r.restaurante_id ? 'sel' : ''}`} onClick={() => pick(r)}>
              <span className="rank-n">{i + 1}</span>
              <div className="m-card-main">
                <div className="m-card-top"><b>{r.restaurante_id}</b><span className={`tag ch-${r.canal_principal}`}>{CHANNEL_LABEL[r.canal_principal]}</span></div>
                <small className="muted">{r.provincia ? `${r.provincia} · ${r.ccaa}` : 'Sin maestro'} · última {dateShort(r.ultima, { year: false })}</small>
              </div>
              <div className="m-card-side">
                <b>{num(r.anomalias)} <small className="muted">alertas</small></b>
                <small className={r.impacto < 0 ? 't-down' : 't-up'}>{eur(r.impacto, { sign: true, compact: true })} · {score(r.score_medio)}</small>
              </div>
            </button>
          </li>
        ))}
        {!rows.length && !loading && <li className="empty">{empty}</li>}
      </ol>
    )
  }
  return (
    <div className={`table-wrap scroll-x ${loading ? 'loading' : ''}`}>
      <table className="dtable ranking">
        <thead>
          <tr>
            <th>#</th><th>Restaurante</th><th>Provincia</th><th>Comunidad</th><th className="r">Anomalías</th>
            <th className="r">Impacto</th><th>Canal más afectado</th><th className="r">Score medio</th><th>Última</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const v = sort === 'impacto' ? r.impacto_abs : sort === 'score' ? r.score_medio : r.anomalias
            return (
              <tr key={r.restaurante_id} className="clickable" onClick={() => pick(r)} title="Filtrar el dashboard por este restaurante">
                <td className="muted">{i + 1}</td>
                <td><b>{r.restaurante_id}</b></td>
                <td>{r.provincia || <span className="muted">Sin maestro</span>}</td>
                <td className="muted">{r.ccaa || '—'}</td>
                <td className="r">
                  <div className="cell-bar"><div style={{ width: `${(v / max) * 100}%` }} /></div>
                  <b>{num(r.anomalias)}</b> <small className="muted"><span className="t-down">{r.caidas}↓</span> <span className="t-up">{r.subidas}↑</span></small>
                </td>
                <td className={`r ${r.impacto < 0 ? 't-down' : 't-up'}`}>{eur(r.impacto, { sign: true })}</td>
                <td><span className={`tag ch-${r.canal_principal}`}>{CHANNEL_LABEL[r.canal_principal]}</span></td>
                <td className="r">{score(r.score_medio)}</td>
                <td className="muted nowrap">{dateShort(r.ultima)}</td>
              </tr>
            )
          })}
          {!rows.length && !loading && <tr><td colSpan="9" className="empty">{empty}</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

export default function RestaurantRanking({ filters, onFilter, anomalies = true, className = '', mobile = false }) {
  const [sort, setSort] = useState(anomalies ? 'anomalias' : 'ventas')
  useEffect(() => { if (anomalies) setSort((s) => (s === 'ventas' ? 'anomalias' : s)) }, [anomalies])
  const [more, setMore] = useState(false)
  const kind = SORTS.find((s) => s.value === sort)?.kind || 'sales'
  const limit = more ? 30 : 10
  const path = kind === 'sales'
    ? `/api/sales/ranking?${filtersQuery(filters)}&sort=${sort}&limit=${limit}`
    : `/api/restaurants?${filtersQuery(filters)}&sort=${sort}&limit=${limit}`
  const { data, error, loading } = useApi(path)
  // Mientras llega la respuesta del otro tipo de ranking, no se pintan filas con columnas que no corresponden.
  const rows = (data && (kind === 'sales') === ('prevRange' in data) ? data.rows : null) || []
  const info = !data ? '' : kind === 'sales' ? `${num(data.total)} restaurantes con ventas` : `${num(data.total)} restaurantes con alertas`
  return (
    <Panel title={kind === 'sales' ? 'Ranking de restaurantes por ventas' : 'Restaurantes con más anomalías'} className={className}
           actions={<><span className="muted small">{info}</span><Select value={sort} onChange={setSort} options={SORTS} /></>}>
      {error && <div className="empty error">{error.message}</div>}
      {kind === 'sales'
        ? <SalesRows rows={rows} filters={filters} onFilter={onFilter} mobile={mobile} loading={loading} sort={sort} />
        : <AnomalyRows rows={rows} filters={filters} onFilter={onFilter} mobile={mobile} loading={loading} sort={sort} anomalies={anomalies} />}
      {data && data.total > 10 && <button className="btn-link" onClick={() => setMore(!more)}>{more ? 'Ver menos' : `Ver más (${Math.min(30, data.total)})`}</button>}
    </Panel>
  )
}
