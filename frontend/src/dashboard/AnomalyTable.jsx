import { useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Download, Search } from 'lucide-react'
import { Panel, Select } from '../components/ui.jsx'
import { apiUrl, filtersQuery, useApi, useDebounced } from '../api.js'
import { CHANNEL_LABEL, CHANNELS, dateShort, eur, num, pct, score } from '../format.js'

const COLS = [
  { key: 'restaurante', label: 'Restaurante', sort: 'restaurante' },
  { key: 'fecha', label: 'Fecha', sort: 'fecha' },
  { key: 'canal', label: 'Canal' },
  { key: 'tipo', label: 'Tipo' },
  { key: 'ventas', label: 'Ventas reales', r: true, sort: 'ventas' },
  { key: 'esperadas', label: 'Ventas esperadas', r: true },
  { key: 'desv', label: 'Desviación', r: true },
  { key: 'desvp', label: 'Desviación %', r: true, sort: 'desviacion_pct' },
  { key: 'tickets', label: 'Tickets reales', r: true },
  { key: 'tickets_e', label: 'Tickets esperados', r: true },
  { key: 'tm', label: 'Ticket medio', r: true },
  { key: 'impacto', label: 'Impacto económico', r: true, sort: 'impacto' },
  { key: 'score', label: 'Score', r: true, sort: 'score' },
  { key: 'exp', label: 'Explicación' },
]

export default function AnomalyTable({ filters, meta, onFilter, onOpen, className = '', mobile = false }) {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState({ key: 'impacto', dir: 'desc' })
  const [page, setPage] = useState(1)
  const [size, setSize] = useState('15')
  const dq = useDebounced(q)
  const fq = filtersQuery(filters)
  useEffect(() => setPage(1), [fq, dq, sort.key, sort.dir, size])
  const { data, error, loading } = useApi(`/api/anomalies?${fq}&q=${encodeURIComponent(dq)}&sort=${sort.key}&dir=${sort.dir}&page=${page}&size=${size}`)
  const toggleSort = (key) => setSort((s) => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))
  const empty = meta.modelStatus?.anomalias === 'pendiente'
    ? 'El modelo externo todavía no ha escrito anomalías en Databricks. Aparecerán aquí automáticamente.'
    : meta.modelStatus?.anomalias === 'sin_alertas' ? 'La ejecución activa del modelo no ha registrado alertas.'
    : 'No hay anomalías con estos filtros'
  const provinces = meta.provinces.filter((p) => !filters.ccaa || p.ccaa === filters.ccaa).map((p) => p.provincia).sort((a, b) => a.localeCompare(b, 'es'))

  return (
    <Panel title="Tabla de anomalías" className={className}
           actions={<span className="muted small">{data ? `${num(data.total)} alertas · ordenadas por ${COLS.find((c) => c.sort === sort.key)?.label.toLowerCase()}` : ''}</span>}>
      <div className={`tbl-toolbar ${mobile ? 'm' : ''}`}>
        <div className="fb-input grow"><Search size={14} />
          <input placeholder="Buscar restaurante o provincia…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Buscar en la tabla" />
        </div>
        {!mobile && <>
        <Select value={filters.canales.length === 1 ? filters.canales[0] : ''} onChange={(c) => onFilter({ canales: c ? [c] : [] })}
                options={[{ value: '', label: 'Todos los canales' }, ...CHANNELS.map((c) => ({ value: c, label: CHANNEL_LABEL[c] }))]} />
        <Select value={filters.tipo} onChange={(tipo) => onFilter({ tipo })}
                options={[{ value: '', label: 'Caídas y subidas' }, { value: 'caida', label: 'Solo caídas' }, { value: 'subida', label: 'Solo subidas' }]} />
        <Select value={filters.provincia} onChange={(provincia) => onFilter({ provincia })}
                options={[{ value: '', label: 'Todas las provincias' }, ...provinces.map((p) => ({ value: p, label: p }))]} />
        <Select value={String(filters.minScore)} onChange={(v) => onFilter({ minScore: +v })}
                options={[{ value: '0', label: 'Cualquier score' }, ...['0.6', '0.7', '0.8', '0.9'].map((v) => ({ value: v, label: `Score ≥ ${v.replace('.', ',')}` }))]} />
        </>}
        {mobile && <Select value={`${sort.key}:${sort.dir}`} onChange={(v) => { const [key, dir] = v.split(':'); setSort({ key, dir }) }}
                options={[['impacto:desc', 'Mayor impacto'], ['score:desc', 'Mayor score'], ['fecha:desc', 'Más recientes'], ['desviacion_pct:desc', 'Mayor desviación']].map(([value, label]) => ({ value, label }))} />}
        <a className="btn-ghost" href={apiUrl(`/api/export.csv?${fq}`)} download aria-label="Exportar CSV"><Download size={14} />{mobile ? 'CSV' : 'Exportar CSV'}</a>
      </div>
      {error && <div className="empty error">{error.message}</div>}
      {mobile ? (
        <ul className={`m-list ${loading ? 'loading' : ''}`}>
          {data?.rows.map((a) => (
            <li key={a.id}>
              <button className="m-card anomaly" onClick={() => onOpen(a.id)}>
                <div className="m-card-main">
                  <div className="m-card-top">
                    <b>{a.restaurante_id}</b><span className={`tag ch-${a.canal}`}>{CHANNEL_LABEL[a.canal]}</span>
                    <span className={`pill ${a.tipo}`}>{a.tipo === 'caida' ? 'Caída' : 'Subida'}</span>
                  </div>
                  <small className="muted">{dateShort(a.fecha, { weekday: true })} · {a.provincia || 'Sin maestro'}</small>
                  <div className="m-figs">
                    <span><small>Reales</small>{eur(a.ventas)}</span>
                    <span><small>Esperadas</small>{eur(a.ventas_esperadas)}</span>
                    <span><small>Tickets</small>{num(a.tickets)} / {num(a.tickets_esperados)}</span>
                  </div>
                  <p className="m-exp">{a.explicacion}</p>
                </div>
                <div className="m-card-side">
                  <b className={a.desviacion_pct < 0 ? 't-down' : 't-up'}>{pct(a.desviacion_pct)}</b>
                  <small className={a.impacto < 0 ? 't-down' : 't-up'}>{eur(a.impacto, { sign: true })}</small>
                  <span className={`score-chip p-${a.prioridad}`}>{score(a.score)}</span>
                </div>
              </button>
            </li>
          ))}
          {data && !data.rows.length && <li className="empty">{empty}</li>}
        </ul>
      ) : (
      <div className={`table-wrap scroll-x ${loading ? 'loading' : ''}`}>
        <table className="dtable anomalies">
          <thead>
            <tr>
              {COLS.map((c) => (
                <th key={c.key} className={`${c.r ? 'r' : ''} ${c.sort ? 'sortable' : ''} ${sort.key === c.sort ? 'sorted' : ''}`}
                    onClick={c.sort ? () => toggleSort(c.sort) : undefined} aria-sort={sort.key === c.sort ? (sort.dir === 'desc' ? 'descending' : 'ascending') : undefined}>
                  {c.label}{sort.key === c.sort && (sort.dir === 'desc' ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data?.rows.map((a) => (
              <tr key={a.id} className="clickable" onClick={() => onOpen(a.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen(a.id)}>
                <td><b>{a.restaurante_id}</b><small className="muted block">{a.provincia || 'Sin maestro'}</small></td>
                <td className="nowrap">{dateShort(a.fecha, { weekday: true })}</td>
                <td><span className={`tag ch-${a.canal}`}>{CHANNEL_LABEL[a.canal]}</span></td>
                <td><span className={`pill ${a.tipo}`}>{a.tipo === 'caida' ? 'Caída' : 'Subida'}</span></td>
                <td className="r">{eur(a.ventas)}</td>
                <td className="r muted">{eur(a.ventas_esperadas)}</td>
                <td className={`r ${a.desviacion < 0 ? 't-down' : 't-up'}`}>{eur(a.desviacion, { sign: true })}</td>
                <td className={`r ${a.desviacion_pct < 0 ? 't-down' : 't-up'}`}><b>{pct(a.desviacion_pct)}</b></td>
                <td className="r">{num(a.tickets)}</td>
                <td className="r muted">{num(a.tickets_esperados)}</td>
                <td className="r">{eur(a.ticket_medio, { decimals: 2 })}</td>
                <td className={`r ${a.impacto < 0 ? 't-down' : 't-up'}`}><b>{eur(a.impacto, { sign: true })}</b></td>
                <td className="r"><span className={`score-chip p-${a.prioridad}`}>{score(a.score)}</span></td>
                <td className="exp" title={a.explicacion}>{a.explicacion}</td>
              </tr>
            ))}
            {data && !data.rows.length && <tr><td colSpan={COLS.length} className="empty">{empty}</td></tr>}
          </tbody>
        </table>
      </div>
      )}
      {data && (
        <div className="pager">
          {!mobile && <><span className="muted small">Filas por página</span>
          <Select value={size} onChange={setSize} options={['15', '25', '50', '100'].map((v) => ({ value: v, label: v }))} /></>}
          <span className="muted small">{data.total ? `${num((data.page - 1) * data.size + 1)}–${num(Math.min(data.total, data.page * data.size))} de ${num(data.total)}` : '0'}</span>
          <button className="icon-mini" disabled={data.page <= 1} onClick={() => setPage(data.page - 1)} aria-label="Página anterior"><ChevronLeft size={16} /></button>
          <span className="small">{data.page} / {data.pages}</span>
          <button className="icon-mini" disabled={data.page >= data.pages} onClick={() => setPage(data.page + 1)} aria-label="Página siguiente"><ChevronRight size={16} /></button>
        </div>
      )}
    </Panel>
  )
}
