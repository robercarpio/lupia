import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { SlidersHorizontal, X, Sparkles } from 'lucide-react'
import FilterBar, { PRESETS, activeCount, detectPreset, presetRange } from './FilterBar.jsx'
import { CHANNEL_LABEL, eur, score } from '../format.js'

/** Hoja inferior al estilo iOS: se cierra con el fondo, con Escape o con el botón. */
export function Sheet({ open, onClose, title, children, footer }) {
  useEffect(() => {
    if (!open) return
    const k = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    document.body.style.overflow = 'hidden'
    return () => { window.removeEventListener('keydown', k); document.body.style.overflow = '' }
  }, [open, onClose])
  if (!open) return null
  // Portal a <body>: así ningún ancestro con transform/animación altera el position: fixed.
  return createPortal(
    <div className="sheet-layer" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <span className="sheet-grab" aria-hidden />
        <header className="sheet-head"><h2>{title}</h2><button className="icon-mini" onClick={onClose} aria-label="Cerrar"><X size={16} /></button></header>
        <div className="sheet-body">{children}</div>
        {footer && <footer className="sheet-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}

/** Filtros en móvil: periodo rápido + chips de filtros activos + hoja con todos los campos. */
export default function MobileFilters({ filters: f, meta, onChange, onClear, synced, total }) {
  const [open, setOpen] = useState(false)
  const n = activeCount(f, meta)
  const preset = detectPreset(f, meta)
  const chips = [
    f.restaurante && ['restaurante', f.restaurante, { restaurante: '' }],
    f.canales.length > 0 && ['canales', f.canales.map((c) => CHANNEL_LABEL[c]).join(' + '), { canales: [] }],
    f.tipo && ['tipo', f.tipo === 'caida' ? 'Caídas' : 'Subidas', { tipo: '' }],
    f.ccaa && !f.provincia && ['ccaa', f.ccaa, { ccaa: '' }],
    f.provincia && ['provincia', f.provincia, { provincia: '' }],
    f.minScore > 0 && ['score', `Score ≥ ${score(f.minScore)}`, { minScore: 0 }],
    f.minImpact > 0 && ['impacto', `≥ ${eur(f.minImpact)}`, { minImpact: 0 }],
    f.prioridad && ['prioridad', `Prioridad ${f.prioridad}`, { prioridad: '' }],
  ].filter(Boolean)
  return (
    <div className="m-filters span-12">
      <div className="m-filters-row">
        <button className={`m-filter-btn ${n ? 'on' : ''}`} onClick={() => setOpen(true)}>
          <SlidersHorizontal size={15} />Filtros{n ? <b>{n}</b> : null}
        </button>
        <div className="m-presets" role="tablist" aria-label="Periodo">
          {PRESETS.filter((p) => p.value !== 'custom').map((p) => (
            <button key={p.value} role="tab" aria-selected={preset === p.value} className={`chip ${preset === p.value ? 'on' : ''}`}
                    onClick={() => onChange(presetRange(p.value, meta))}>
              {p.value === 'all' ? 'Todo' : p.value === '365' ? '1 año' : `${p.value} d`}
            </button>
          ))}
        </div>
      </div>
      {(chips.length > 0 || synced) && (
        <div className="m-chips">
          {synced && <span className="fb-synced"><Sparkles size={12} />Asistente</span>}
          {chips.map(([k, label, patch]) => (
            <button key={k} className="m-chip" onClick={() => onChange(patch)} aria-label={`Quitar filtro ${label}`}>{label}<X size={12} /></button>
          ))}
        </div>
      )}
      <Sheet open={open} onClose={() => setOpen(false)} title="Filtros"
             footer={<>
               <button className="btn-ghost" disabled={!n} onClick={onClear}>Limpiar</button>
               <button className="btn-apply big" onClick={() => setOpen(false)}>Ver {total != null ? `${total} ` : ''}anomalías</button>
             </>}>
        <FilterBar filters={f} meta={meta} onChange={onChange} onClear={onClear} variant="sheet" />
      </Sheet>
    </div>
  )
}
