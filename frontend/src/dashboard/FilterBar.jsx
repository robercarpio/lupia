import { useEffect, useState } from 'react'
import { RotateCcw, Sparkles, Search } from 'lucide-react'
import { Select } from '../components/ui.jsx'
import { CHANNELS, CHANNEL_LABEL, addDays } from '../format.js'

export const PRESETS = [
  { value: '7', label: 'Últimos 7 días' },
  { value: '30', label: 'Últimos 30 días' },
  { value: '90', label: 'Últimos 90 días' },
  { value: '365', label: 'Último año' },
  { value: 'all', label: 'Todo el periodo' },
  { value: 'custom', label: 'Personalizado' },
]
export function presetRange(preset, meta) {
  if (preset === 'all') return { from: meta.evalStart, to: meta.dateMax }
  return { from: addDays(meta.dateMax, -(+preset - 1)), to: meta.dateMax }
}
export function detectPreset(f, meta) {
  if (!meta || f.to !== meta.dateMax) return 'custom'
  if (f.from === meta.evalStart) return 'all'
  return PRESETS.find((p) => !['all', 'custom'].includes(p.value) && addDays(meta.dateMax, -(+p.value - 1)) === f.from)?.value || 'custom'
}
export const emptyFilters = (meta) => ({
  ...presetRange('90', meta), restaurante: '', canales: [], tipo: '', ccaa: '', provincia: '', minScore: 0, minImpact: 0, prioridad: '',
})
export const activeCount = (f, meta) => {
  const d = emptyFilters(meta)
  return ['restaurante', 'tipo', 'ccaa', 'provincia', 'prioridad'].filter((k) => f[k]).length +
    (f.canales.length ? 1 : 0) + (f.minScore ? 1 : 0) + (f.minImpact ? 1 : 0) + (f.from !== d.from || f.to !== d.to ? 1 : 0)
}

/** Filtros globales: afectan a KPIs, gráficos, ranking, mapa y tabla a la vez. */
export default function FilterBar({ filters: f, meta, onChange, onClear, syncedFromAssistant, variant = 'card' }) {
  const [rest, setRest] = useState(f.restaurante)
  const [impact, setImpact] = useState(f.minImpact || '')
  useEffect(() => setRest(f.restaurante), [f.restaurante])
  useEffect(() => setImpact(f.minImpact || ''), [f.minImpact])
  const provinces = meta.provinces.filter((p) => !f.ccaa || p.ccaa === f.ccaa).map((p) => p.provincia).sort((a, b) => a.localeCompare(b, 'es'))
  const toggleCanal = (c) => onChange({ canales: f.canales.includes(c) ? f.canales.filter((x) => x !== c) : [...f.canales, c] })
  const commitRest = () => {
    const v = rest.trim().toUpperCase()
    if (!v) return onChange({ restaurante: '' })
    const id = /^R?\d{1,3}$/.test(v) ? `R${v.replace(/^R/, '').padStart(3, '0')}` : v
    if (meta.restaurantIds.includes(id)) onChange({ restaurante: id })
    else setRest(f.restaurante)
  }
  const n = activeCount(f, meta)

  return (
    <section className={variant === 'sheet' ? 'filter-bar in-sheet' : 'card filter-bar span-12'} aria-label="Filtros globales">
      <div className="fb-group">
        <label className="fb-field">
          <span>Desde</span>
          <input type="date" value={f.from} min={meta.dateMin} max={f.to} onChange={(e) => e.target.value && onChange({ from: e.target.value })} />
        </label>
        <label className="fb-field">
          <span>Hasta</span>
          <input type="date" value={f.to} min={f.from} max={meta.dateMax} onChange={(e) => e.target.value && onChange({ to: e.target.value })} />
        </label>
      </div>
      <span className="sep" />
      <label className="fb-field fb-search">
        <span>Restaurante</span>
        <div className="fb-input"><Search size={14} />
          <input list="rest-ids" placeholder="R001" value={rest} onChange={(e) => setRest(e.target.value)}
                 onBlur={commitRest} onKeyDown={(e) => e.key === 'Enter' && commitRest()} />
        </div>
        <datalist id="rest-ids">{meta.restaurantIds.map((id) => <option key={id} value={id} />)}</datalist>
      </label>
      <div className="fb-field">
        <span>Canal</span>
        <div className="chips">
          {CHANNELS.map((c) => (
            <button key={c} className={`chip ${f.canales.includes(c) ? 'on' : ''}`} data-canal={c} onClick={() => toggleCanal(c)}>{CHANNEL_LABEL[c]}</button>
          ))}
        </div>
      </div>
      <div className="fb-field">
        <span>Tipo</span>
        <div className="chips">
          {[['', 'Todas'], ['caida', 'Caídas'], ['subida', 'Subidas']].map(([v, l]) => (
            <button key={v} className={`chip ${f.tipo === v ? 'on' : ''} ${v}`} onClick={() => onChange({ tipo: v })}>{l}</button>
          ))}
        </div>
      </div>
      <span className="sep" />
      <label className="fb-field">
        <span>Comunidad</span>
        <Select value={f.ccaa} onChange={(ccaa) => onChange({ ccaa, provincia: '' })}
                options={[{ value: '', label: 'Todas' }, ...meta.ccaas.map((c) => ({ value: c, label: c }))]} />
      </label>
      <label className="fb-field">
        <span>Provincia</span>
        <Select value={f.provincia} onChange={(provincia) => onChange({ provincia, ccaa: provincia ? meta.provinces.find((p) => p.provincia === provincia)?.ccaa || f.ccaa : f.ccaa })}
                options={[{ value: '', label: 'Todas' }, ...provinces.map((p) => ({ value: p, label: p }))]} />
      </label>
      <span className="sep" />
      <label className="fb-field fb-range">
        <span>Score mínimo <b>{f.minScore ? f.minScore.toFixed(2).replace('.', ',') : '—'}</b></span>
        <input type="range" min="0" max="0.95" step="0.05" value={f.minScore} onChange={(e) => onChange({ minScore: +e.target.value })} />
      </label>
      <label className="fb-field fb-impact">
        <span>|Impacto| mín.</span>
        <div className="fb-input"><input type="number" min="0" step="100" placeholder="0 €" value={impact}
               onChange={(e) => setImpact(e.target.value)} onBlur={() => onChange({ minImpact: Math.max(0, +impact || 0) })}
               onKeyDown={(e) => e.key === 'Enter' && onChange({ minImpact: Math.max(0, +impact || 0) })} /><small>€</small></div>
      </label>
      <div className="fb-end">
        {syncedFromAssistant && <span className="fb-synced" title="Filtros aplicados desde el AI Assistant"><Sparkles size={13} />Asistente</span>}
        <button className="btn-ghost" disabled={!n} onClick={onClear}><RotateCcw size={14} />Limpiar{n ? ` (${n})` : ''}</button>
      </div>
    </section>
  )
}
