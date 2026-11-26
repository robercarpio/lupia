import { Download, FileText, Info } from 'lucide-react'
import { Panel } from '../components/ui.jsx'
import { apiUrl, filtersQuery } from '../api.js'
import { dateShort, num, score } from '../format.js'

const MOTIVO = [
  ['festivo', 'Festivos'], ['vispera_festivo', 'Vísperas de festivo'], ['fecha_especial', 'Fechas especiales'], ['navidad', 'Navidad'],
  ['semana_santa', 'Semana Santa'], ['dia_neutro', 'Eventos generales'], ['apertura', 'Aperturas'], ['cierre', 'Previos a cierre'],
  ['reforma', 'Reformas'], ['canal_pequeno', 'Canales pequeños'], ['sin_historico', 'Sin histórico suficiente'],
]
// Métricas que publica el modelo en model_runs (sobre su clave sintética, no la del jurado).
const METRICS = [['f1_synthetic', 'F1'], ['precision_synthetic', 'Precisión'], ['recall_synthetic', 'Recall'], ['precision_at_100_synthetic', 'Precision@100']]
// Valor de un parámetro publicado: números legibles, objetos/arrays como JSON compacto.
const fmtParam = (v) => (typeof v === 'number' ? (Number.isInteger(v) ? num(v) : String(+v.toFixed(4)).replace('.', ',')) : v && typeof v === 'object' ? JSON.stringify(v) : String(v))
const dt = (iso) => (iso ? `${dateShort(iso.slice(0, 10))}${iso.length > 10 ? ` ${iso.slice(11, 16)}` : ''}` : '—')

/**
 * Informe del modelo de detección. Todo sale de Databricks: model_runs (ejecución activa, parámetros
 * y métricas) y series_summary (motivos de exclusión). El modelo lo desarrolla otro sistema: aquí
 * no se describen umbrales que el modelo no haya publicado.
 */
export default function DetectorPanel({ meta, filters, className = '' }) {
  const run = meta.modelRun
  const st = meta.modelStatus || {}
  const params = Object.entries(meta.config?.params || {})
  const excl = MOTIVO.map(([k, l]) => [k, l, meta.reasonCounts?.[k] || 0])
  const totalExcl = excl.reduce((s, [, , v]) => s + v, 0)
  const total = Object.values(meta.reasonCounts || {}).reduce((s, v) => s + v, 0)
  return (
    <Panel title="Informe del modelo" className={className}
           actions={<>
             <a className="btn-ghost" href={apiUrl('/api/anomalias.csv')} download><Download size={14} />Entrega oficial</a>
             <a className="btn-apply as-link" href={apiUrl(`/api/informe.pdf?${filtersQuery(filters)}`)} download
                title="Informe PDF con resumen, desgloses y el listado de las anomalías con los filtros actuales"><FileText size={14} />Descargar informe PDF</a>
           </>}>
      <div className="det-grid">
        <div>
          <h3>Ejecución activa</h3>
          {run ? (
            <div className="metrics">
              {[['Ejecución', run.run_id], ['Versión', run.model_version || '—'], ['Fecha', dt(run.created_at)],
                ['Periodo evaluado', `${dateShort(run.eval_start_date)} – ${dateShort(run.eval_end_date)}`],
                ['Filas candidatas', num(run.total_candidate_rows)], ['Alertas', `${num(run.total_alerts)} (${num(run.total_caidas)} caídas · ${num(run.total_subidas)} subidas)`]]
                .map(([k, v]) => <div key={k}><span>{k}</span><b>{v}</b></div>)}
            </div>
          ) : (
            <p className="note"><Info size={14} /><span>El modelo externo todavía no ha registrado ninguna ejecución en <code>model_runs</code>. Cuando lo haga, este informe y todas las secciones de anomalías se actualizarán solas.</span></p>
          )}
          <h3>Parámetros publicados por el modelo</h3>
          {params.length ? (
            <div className="metrics">{params.map(([k, v]) => (v && typeof v === 'object' && !Array.isArray(v)
              ? <div key={k} className="metric-group">
                  <span>{k}</span>
                  <div className="metrics sub">{Object.entries(v).map(([sk, sv]) => <div key={sk}><span>{sk}</span><b>{fmtParam(sv)}</b></div>)}</div>
                </div>
              : <div key={k}><span>{k}</span><b>{fmtParam(v)}</b></div>))}</div>
          ) : <p className="muted small">Sin parámetros publicados (<code>model_runs.params_json</code>).</p>}
        </div>
        <div>
          <h3>Días excluidos por tener explicación</h3>
          {totalExcl ? (
            <div className="reasons">
              {excl.map(([k, l, v]) => (
                <div key={k}><span>{l}</span><b>{num(v)}</b><div className="imp-bar"><div style={{ width: `${Math.min(100, (v / Math.max(1, total)) * 400)}%` }} /></div></div>
              ))}
            </div>
          ) : (
            <p className="muted small">El modelo aún no ha marcado motivos de exclusión en <code>series_summary.exclusion_reason</code>.</p>
          )}
          <p className="muted small">Filas restaurante-canal-día de <code>series_summary</code>. Los días sin registro de ventas no se evalúan.</p>
        </div>
        <div>
          <h3>Evaluación</h3>
          <div className="metrics">
            {METRICS.map(([k, l]) => <div key={k}><span>{l}</span>{run?.[k] != null ? <b>{score(run[k])}</b> : <b className="muted">No disponible</b>}</div>)}
          </div>
          <p className="note"><Info size={14} /><span>Métricas publicadas por el modelo en <code>model_runs</code>, calculadas sobre su clave sintética. Las métricas oficiales requieren la clave del jurado, que no es pública.</span></p>
          <h3>Datos</h3>
          <p className="muted small">
            Databricks · {num(meta.filas)} filas · {num(meta.restaurantes)} restaurantes ({num(meta.sin_maestro)} sin provincia en el maestro) ·
            {' '}{dateShort(meta.dateMin)} – {dateShort(meta.dateMax)} · {num(meta.totalAnomalias)} alertas ·
            {' '}ventas esperadas calculadas en {num(st.filas_con_baseline || 0)} filas{st.comprobado ? ` · comprobado ${dt(st.comprobado)} UTC` : ''}.
          </p>
        </div>
      </div>
    </Panel>
  )
}
