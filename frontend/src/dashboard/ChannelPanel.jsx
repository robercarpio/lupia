import { useEffect, useState } from 'react'
import { Delta, Panel, Select } from '../components/ui.jsx'
import { CHANNEL_COLOR, CHANNEL_LABEL, eur, num, pct } from '../format.js'

/**
 * Comparativa de sala, domicilio y autoking (mismo lenguaje visual que «Ventas por tipo de cuenta»).
 * Dos vistas: ventas reales por canal y anomalías por canal. Sin anomalías del modelo se abre en ventas.
 */
export default function ChannelPanel({ channels, sales = [], anomalies = true, filters, onFilter, className = '' }) {
  const [hover, setHover] = useState(null)
  const [metric, setMetric] = useState(anomalies ? 'anomalias' : 'ventas')
  useEffect(() => { if (anomalies) setMetric('anomalias') }, [anomalies])
  const isSales = metric === 'ventas'
  const toggle = (canal) => onFilter({ canales: filters.canales.length === 1 && filters.canales[0] === canal ? [] : [canal] })
  const dim = (i) => (hover !== null && hover !== i ? 'dim' : '')
  const actions = <Select value={metric} onChange={setMetric} options={[{ value: 'ventas', label: 'Ventas' }, { value: 'anomalias', label: 'Anomalías' }]} />

  if (isSales) {
    const total = sales.reduce((s, c) => s + c.ventas, 0)
    return (
      <Panel title="Ventas por canal" className={className} actions={actions}>
        <p className="sub">{eur(total, { compact: true })} en el periodo · clic en un canal para filtrar</p>
        {!sales.length ? <div className="empty">No hay ventas con estos filtros</div> : (
          <>
            <div className="mix-legend">
              {sales.map((c, i) => (
                <button key={c.canal} className={`mix-item mix-btn ${dim(i)} ${filters.canales.includes(c.canal) ? 'sel' : ''}`}
                        onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => toggle(c.canal)}>
                  <span>{CHANNEL_LABEL[c.canal]}</span>
                  <b style={{ color: CHANNEL_COLOR[c.canal] }}>{pct(c.share, { sign: false })}</b>
                  <small>{eur(c.ventas, { compact: true })}</small>
                </button>
              ))}
            </div>
            <div className="mix-bar">
              {sales.map((c, i) => (
                <div key={c.canal} style={{ flex: c.ventas || 0.0001, background: CHANNEL_COLOR[c.canal], opacity: hover !== null && hover !== i ? 0.35 : 1 }}
                     onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} title={`${CHANNEL_LABEL[c.canal]}: ${eur(c.ventas)}`} />
              ))}
            </div>
            <div className="ch-rows">
              {sales.map((c, i) => (
                <div key={c.canal} className={`ch-row sales ${dim(i)}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                  <div className="ch-name"><i className="sq" style={{ background: CHANNEL_COLOR[c.canal] }} />{CHANNEL_LABEL[c.canal]}</div>
                  <small className="ch-count" title={`${num(c.tickets)} tickets · ticket medio ${eur(c.ticket_medio, { decimals: 2 })}`}>{num(c.tickets)} tk · TM {eur(c.ticket_medio, { decimals: 2 })}</small>
                  <div className="ch-imp">
                    <b>{eur(c.ventas, { compact: true })}</b>
                    {c.variacion != null && <Delta value={c.variacion * 100} decimals={1} />}
                  </div>
                </div>
              ))}
            </div>
            <div className="ch-legend muted small"><span>tk = tickets · TM = ticket medio</span><span>Variación frente al periodo anterior</span></div>
          </>
        )}
      </Panel>
    )
  }

  const total = channels.reduce((s, c) => s + c.total, 0)
  const maxImp = Math.max(1, ...channels.map((c) => Math.abs(c.perdidas) + c.ganancias))
  return (
    <Panel title="Anomalías por canal" className={className} actions={actions}>
      <p className="sub">{anomalies ? `${num(total)} alertas · clic en un canal para filtrar` : 'El modelo todavía no ha escrito anomalías'}</p>
      <div className="mix-legend">
        {channels.map((c, i) => (
          <button key={c.canal} className={`mix-item mix-btn ${dim(i)} ${filters.canales.includes(c.canal) ? 'sel' : ''}`}
                  onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => toggle(c.canal)}>
            <span>{CHANNEL_LABEL[c.canal]}</span>
            <b style={{ color: CHANNEL_COLOR[c.canal] }}>{pct(c.share, { sign: false })}</b>
            <small>{num(c.total)} alertas</small>
          </button>
        ))}
      </div>
      <div className="mix-bar">
        {total ? channels.map((c, i) => (
          <div key={c.canal} style={{ flex: c.total || 0.0001, background: CHANNEL_COLOR[c.canal], opacity: hover !== null && hover !== i ? 0.35 : 1 }}
               onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} title={`${CHANNEL_LABEL[c.canal]}: ${num(c.total)}`} />
        )) : <div style={{ flex: 1, background: 'var(--line)' }} />}
      </div>
      <div className="ch-rows">
        {channels.map((c, i) => (
          <div key={c.canal} className={`ch-row ${dim(i)}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <div className="ch-name"><i className="sq" style={{ background: CHANNEL_COLOR[c.canal] }} />{CHANNEL_LABEL[c.canal]}</div>
            <div className="ch-split" title={`${c.caidas} caídas · ${c.subidas} subidas`}>
              <div className="bg-down" style={{ width: `${c.total ? (c.caidas / c.total) * 100 : 0}%` }} />
              <div className="bg-up" style={{ width: `${c.total ? (c.subidas / c.total) * 100 : 0}%` }} />
            </div>
            <small className="ch-count"><span className="t-down">{c.caidas}↓</span> <span className="t-up">{c.subidas}↑</span></small>
            <div className="ch-imp">
              <div className="imp-bar"><div style={{ width: `${((Math.abs(c.perdidas) + c.ganancias) / maxImp) * 100}%`, background: CHANNEL_COLOR[c.canal] }} /></div>
              <b className={c.impacto < 0 ? 't-down' : 't-up'}>{eur(c.impacto, { sign: true, compact: true })}</b>
            </div>
          </div>
        ))}
      </div>
      <div className="ch-legend muted small"><span><i className="sq bg-down" />Caídas</span><span><i className="sq bg-up" />Subidas</span><span>Impacto neto</span></div>
    </Panel>
  )
}
