import { useEffect, useRef, useState } from 'react'
import { Delta, Panel, Select } from '../components/ui.jsx'
import { eur, num } from '../format.js'

import { PROVINCES, VIEWBOX, CANARIAS_BOX } from './spainProvinces.js'

// Mapa coroplético de las provincias españolas (contornos reales simplificados).
// Canarias se dibuja como recuadro abajo a la izquierda. Ceuta y Melilla llevan un
// marcador circular porque su silueta es demasiado pequeña para pulsarla.
const TINY = new Set(['Ceuta', 'Melilla'])
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ' ').trim()
const ALIAS = {
  'la coruna': 'a coruna', coruna: 'a coruna', vizcaya: 'bizkaia', guipuzcoa: 'gipuzkoa', araba: 'alava', 'araba alava': 'alava', 'alava araba': 'alava',
  baleares: 'illes balears', 'islas baleares': 'illes balears', 'balears illes': 'illes balears', castello: 'castellon', 'castellon castello': 'castellon',
  'valencia valencia': 'valencia', alacant: 'alicante', 'alicante alacant': 'alicante', gerona: 'girona', lerida: 'lleida', orense: 'ourense',
  tenerife: 'santa cruz de tenerife', 'principado de asturias': 'asturias', rioja: 'la rioja', 'region de murcia': 'murcia', 'comunidad de madrid': 'madrid', 'palmas las': 'las palmas',
}
const key = (s) => { const n = norm(s); return ALIAS[n] || n }

// Filtros que había antes de pulsar una provincia en el mapa. Al volver a pulsarla se
// restauran tal cual (provincia y comunidad), así la selección se deshace por completo.
// Vive fuera del componente para sobrevivir a los cambios de modo (dashboard ↔ asistente).
let mapPrev = null

export default function GeoPanel({ geo, salesGeo, anomalies = true, filters, onFilter, provinces = [], className = '' }) {
  const [metric, setMetric] = useState(anomalies ? 'anomalias' : 'ventas')
  useEffect(() => { if (anomalies) setMetric((m) => (m === 'ventas' ? 'anomalias' : m)) }, [anomalies])
  const [hover, setHover] = useState(null)
  // En táctil no hay «hover»: el último tipo de puntero decide si el toque muestra la ficha.
  const pointer = useRef('mouse')
  const isSales = metric === 'ventas' && !!salesGeo
  const src = isSales ? salesGeo : geo
  const byKey = new Map(src.provincia.map((g) => [key(g.nombre), g]))
  const val = (g) => (!g ? 0 : isSales ? g.ventas : metric === 'impacto' ? Math.abs(g.impacto) : g.anomalias)
  const fmtVal = (v) => (isSales || metric === 'impacto' ? eur(v, { compact: true }) : num(v))
  const max = Math.max(1, ...src.provincia.filter((g) => g.nombre !== 'Sin dato de maestro').map(val))
  const gridKeys = new Set(PROVINCES.map((p) => key(p.name)))
  // Provincias del maestro: permiten pulsar una provincia aunque no tenga datos con el filtro
  // actual (p. ej. con Alicante seleccionado, el resto del mapa viene vacío pero se puede saltar a Madrid).
  const master = new Map((provinces || []).map((p) => [key(p.provincia), p]))
  const unmatched = src.provincia.filter((g) => !gridKeys.has(key(g.nombre)) && g.nombre !== 'Sin dato de maestro')
  const sinMaestro = src.provincia.find((g) => g.nombre === 'Sin dato de maestro')
  const h = hover && byKey.get(key(hover))
  const top = [...src.ccaa].filter((g) => g.nombre !== 'Sin dato de maestro').sort((a, b) => val(b) - val(a)).slice(0, 5)
  const maxC = Math.max(1, ...top.map(val))

  return (
    <Panel title="Distribución geográfica" className={className}
           actions={<Select value={metric} onChange={setMetric} options={[...(salesGeo ? [{ value: 'ventas', label: 'Ventas' }] : []), { value: 'anomalias', label: 'Nº de anomalías' }, { value: 'impacto', label: 'Impacto económico' }]} />}>
      <p className="sub">Por provincia del restaurante · clic para filtrar</p>
      <div className="geo">
        <div className="geo-map" onPointerLeave={(e) => { if (e.pointerType === 'mouse') setHover(null) }}>
          <svg viewBox={VIEWBOX} role="group" aria-label="Mapa de provincias de España" onPointerDown={(e) => { pointer.current = e.pointerType }}>
            <rect className="geo-inset" x={CANARIAS_BOX[0]} y={CANARIAS_BOX[1]} width={CANARIAS_BOX[2]} height={CANARIAS_BOX[3]} rx="8" />
            {/* Orden fijo: reordenar los nodos al seleccionar rompía el siguiente toque en móvil. */}
            {PROVINCES.map(({ name, d, c }) => {
              const g = byKey.get(key(name))
              const t = val(g) / max
              const sel = filters.provincia && key(filters.provincia) === key(name)
              const target = g || master.get(key(name))
              const clickable = !!target
              const click = () => {
                if (!target) return
                if (pointer.current !== 'mouse') setHover(sel ? null : name)
                if (sel) {
                  // Deseleccionar: vuelve a los filtros previos al clic si la selección vino del mapa.
                  const prev = mapPrev && mapPrev.sel === filters.provincia ? mapPrev : { provincia: '', ccaa: '' }
                  mapPrev = null
                  onFilter({ provincia: prev.provincia, ccaa: prev.ccaa })
                  return
                }
                // Al saltar de una provincia del mapa a otra se conserva el estado original.
                if (!mapPrev || mapPrev.sel !== filters.provincia) mapPrev = { provincia: filters.provincia || '', ccaa: filters.ccaa || '' }
                const nombre = g?.nombre || target.provincia
                mapPrev.sel = nombre
                // Cambia a la nueva provincia (y su comunidad), sustituyendo a la anterior.
                onFilter({ provincia: nombre, ccaa: target.ccaa || filters.ccaa })
              }
              return (
                <g key={name} className={`prov ${g ? '' : 'none'} ${clickable ? 'can' : ''} ${sel ? 'sel' : ''} ${hover === name ? 'hov' : ''}`} style={{ '--t': g ? 0.12 + t * 0.88 : 0 }}
                   onPointerEnter={(e) => { if (e.pointerType === 'mouse') setHover(name) }} onClick={click}
                   role="button" tabIndex={clickable ? 0 : -1} aria-label={`${name}: ${g ? (isSales ? eur(g.ventas) : `${g.anomalias} anomalías`) : 'sin datos'}`}
                   onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); click() } }}>
                  <path d={d} />
                  {TINY.has(name) && <circle cx={c[0]} cy={c[1]} r="7" />}
                </g>
              )
            })}
            {/* Contorno de la seleccionada encima del resto, sin capturar toques. */}
            {PROVINCES.filter((p) => filters.provincia && key(filters.provincia) === key(p.name)).map(({ name, d, c }) => (
              <g key={`sel-${name}`} className="prov-outline" aria-hidden>
                <path d={d} />
                {TINY.has(name) && <circle cx={c[0]} cy={c[1]} r="7" />}
              </g>
            ))}
          </svg>
          <div className="tile-info">
            {hover ? (
              <>
                <b>{hover}</b>
                {h && isSales ? (
                  <>
                    <span>{eur(h.ventas)} · {num(h.tickets)} tickets · {num(h.restaurantes)} restaurantes</span>
                    <span>Ticket medio {eur(h.ticket_medio, { decimals: 2 })}{h.variacion != null && <> · <Delta value={h.variacion * 100} decimals={1} /> vs periodo anterior</>}</span>
                  </>
                ) : h ? (
                  <>
                    <span>{num(h.anomalias)} anomalías · {num(h.restaurantes_afectados)} de {num(h.restaurantes)} restaurantes</span>
                    <span><span className="t-down">{h.caidas} caídas</span> · <span className="t-up">{h.subidas} subidas</span></span>
                    <span>Impacto {eur(h.impacto, { sign: true })}</span>
                  </>
                ) : <span className="muted">{isSales ? 'Sin ventas con estos filtros' : 'Sin anomalías con estos filtros'}</span>}
              </>
            ) : (
              <span className="muted">{pointer.current === 'mouse' ? 'Pasa el ratón por una provincia' : 'Toca una provincia para filtrar'}</span>
            )}
          </div>
        </div>
        <div className="geo-scale"><span>0</span><i /><span>{fmtVal(max)}</span></div>
        <div className="geo-list">
          <small className="muted">Comunidades con más {isSales ? 'ventas' : metric === 'impacto' ? 'impacto' : 'anomalías'}</small>
          {top.map((g) => (
            <button key={g.nombre} className={`geo-row ${filters.ccaa === g.nombre ? 'sel' : ''}`} onClick={() => onFilter({ ccaa: filters.ccaa === g.nombre ? '' : g.nombre, provincia: '' })}>
              <span className="geo-name">{g.nombre}</span>
              {isSales ? (
                <div className="imp-bar"><div style={{ width: `${(val(g) / maxC) * 100}%`, background: 'var(--teal)' }} /></div>
              ) : (
                <div className="ch-split" title={`${g.caidas} caídas · ${g.subidas} subidas`}>
                  <div className="bg-down" style={{ width: `${(val(g) / maxC) * (g.anomalias ? g.caidas / g.anomalias : 0) * 100}%` }} />
                  <div className="bg-up" style={{ width: `${(val(g) / maxC) * (g.anomalias ? g.subidas / g.anomalias : 0) * 100}%` }} />
                </div>
              )}
              <b>{isSales ? eur(g.ventas, { compact: true }) : metric === 'impacto' ? eur(g.impacto, { sign: true, compact: true }) : num(g.anomalias)}</b>
            </button>
          ))}
          {!top.length && <span className="muted small">{isSales ? 'Sin ventas' : 'Sin anomalías'}</span>}
        </div>
        {(sinMaestro || unmatched.length > 0) && (
          <p className="muted small geo-note">
            {sinMaestro && <>{isSales ? `${eur(sinMaestro.ventas, { compact: true })} de ventas` : `${num(sinMaestro.anomalias)} anomalías`} de restaurantes sin provincia en el maestro. </>}
            {unmatched.length > 0 && <>Otras provincias: {unmatched.map((g) => `${g.nombre} (${fmtVal(val(g))})`).join(', ')}.</>}
          </p>
        )}
      </div>
    </Panel>
  )
}
