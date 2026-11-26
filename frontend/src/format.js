// Formato en español para los datos del reto (euros, porcentajes, fechas).
export const CHANNELS = ['sala', 'domicilio', 'autoking']
export const CHANNEL_LABEL = { sala: 'Sala', domicilio: 'Domicilio', autoking: 'Autoking' }
export const CHANNEL_COLOR = { sala: 'var(--teal)', domicilio: 'var(--purple)', autoking: 'var(--slate)' }
export const TIPO_LABEL = { caida: 'Caída', subida: 'Subida' }
export const PRIORIDAD_LABEL = { alta: 'Alta', media: 'Media', baja: 'Baja' }

const nf = (d) => new Intl.NumberFormat('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: 'always' })
const cache = {}
const fmtN = (v, d = 0) => (cache[d] ??= nf(d)).format(v)

export const num = (v, d = 0) => (v == null || Number.isNaN(v) ? '—' : fmtN(v, d))
export function eur(v, { sign = false, decimals = 0, compact = false } = {}) {
  if (v == null || Number.isNaN(v)) return '—'
  const s = sign && v > 0 ? '+' : v < 0 ? '−' : ''
  const a = Math.abs(v)
  if (compact && a >= 10000) return `${s}${fmtN(a / 1000, a >= 100000 ? 0 : 1)} k€`
  return `${s}${fmtN(a, decimals)} €`
}
export function pct(frac, { sign = true, decimals = 0 } = {}) {
  if (frac == null || Number.isNaN(frac)) return '—'
  const v = frac * 100
  const s = sign && v > 0 ? '+' : v < 0 ? '−' : ''
  return `${s}${fmtN(Math.abs(v), decimals)} %`
}
export const score = (v) => (v == null ? '—' : fmtN(v, 2))

const MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']
const DIA = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb']
export function dateShort(iso, { year = true, weekday = false } = {}) {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-').map(Number)
  const wd = weekday ? `${DIA[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ` : ''
  return `${wd}${d} ${MES[m - 1]}${year ? ` ${y}` : ''}`
}
export const dateLong = (iso) => {
  const s = new Date(iso + 'T00:00:00Z').toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
  return s.charAt(0).toUpperCase() + s.slice(1)
}

export function addDays(iso, n) {
  const d = new Date(iso + 'T00:00:00Z')
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
export const daysBetween = (a, b) => Math.round((new Date(b + 'T00:00:00Z') - new Date(a + 'T00:00:00Z')) / 864e5) + 1

export function describeFilters(f, meta) {
  if (!f) return ''
  const p = []
  if (meta && f.from === meta.evalStart && f.to === meta.dateMax) p.push('Todo el periodo')
  else p.push(`${dateShort(f.from)} – ${dateShort(f.to)}`)
  if (f.restaurante) p.push(f.restaurante)
  if (f.canales?.length) p.push(f.canales.map((c) => CHANNEL_LABEL[c]).join(' + '))
  if (f.tipo) p.push(f.tipo === 'caida' ? 'Caídas' : 'Subidas')
  if (f.provincia) p.push(f.provincia)
  else if (f.ccaa) p.push(f.ccaa)
  if (f.minScore) p.push(`Score ≥ ${score(f.minScore)}`)
  if (f.minImpact) p.push(`|Impacto| ≥ ${eur(f.minImpact)}`)
  if (f.prioridad) p.push(`Prioridad ${f.prioridad}`)
  return p.join(' · ')
}
