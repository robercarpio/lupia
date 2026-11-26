import { Clock } from 'lucide-react'
import { Panel } from '../components/ui.jsx'
import { filtersQuery, useApi } from '../api.js'
import FilterBar from './FilterBar.jsx'
import SalesKpis from './SalesKpis.jsx'
import AnomalyKpis from './AnomalyKpis.jsx'
import TimelineChart from './TimelineChart.jsx'
import ChannelPanel from './ChannelPanel.jsx'
import RestaurantRanking from './RestaurantRanking.jsx'
import GeoPanel from './GeoPanel.jsx'
import AnomalyTable from './AnomalyTable.jsx'
import DetectorPanel from './DetectorPanel.jsx'
import MobileFilters from './MobileFilters.jsx'
import { useIsMobile } from '../useMedia.js'

/** true si el modelo externo ya ha escrito alertas en Databricks. */
export const hasAnomalies = (meta) => meta?.modelStatus?.anomalias === 'ok'

/**
 * Aviso de estado del modelo externo. No es un error: las ventas son reales y las secciones de
 * anomalías se rellenan solas cuando el modelo escribe en alerts_prioritized / model_runs.
 */
export function ModelBanner({ meta }) {
  const st = meta?.modelStatus
  if (!st || st.anomalias === 'ok') return null
  return (
    <div className="data-banner span-12" role="status">
      <Clock size={16} />
      <span>
        {st.anomalias === 'pendiente'
          ? <><b>Anomalías pendientes del modelo.</b> Las tablas de anomalías de Databricks todavía están vacías. </>
          : <><b>Sin alertas.</b> El modelo se ha ejecutado ({st.ejecucion}) y no ha registrado alertas. </>}
        Las ventas que ves son reales; las anomalías aparecerán aquí automáticamente cuando el modelo las escriba
        (se comprueba cada minuto).
      </span>
    </div>
  )
}

export default function Dashboard({ meta, filters, onFilter, onClear, onOpen, synced }) {
  const { data, error, loading } = useApi(`/api/dashboard?${filtersQuery(filters)}`)
  const mobile = useIsMobile()
  const anomalies = hasAnomalies(meta)
  const noRef = data?.timeline.baseline === 'pendiente'
  return (
    <div className={`content ${loading && data ? 'is-loading' : ''}`}>
      <ModelBanner meta={meta} />
      {mobile
        ? <MobileFilters filters={filters} meta={meta} onChange={onFilter} onClear={onClear} synced={synced} total={data?.kpis.total} />
        : <FilterBar filters={filters} meta={meta} onChange={onFilter} onClear={onClear} syncedFromAssistant={synced} />}
      {error && <div className="card panel span-12 empty error">{error.message}{error.code === 'WAREHOUSE_STARTING' ? ' Se reintentará al cambiar un filtro.' : ''}</div>}
      {data && (
        <>
          <div id="sec-resumen" className="anchor span-12" />
          <SalesKpis k={data.sales.kpis} totalRestaurants={meta.restaurantes} />
          <AnomalyKpis k={data.kpis} totalRestaurants={meta.restaurantes} />
          <Panel title="Evolución temporal" className="span-8"
                 actions={<div className="legend">
                   <span><i className="ln" />Ventas reales</span>{!noRef && <span><i className="dash" />Esperadas</span>}
                   <span><i className="sq" style={{ background: 'var(--down)' }} />Caída</span><span><i className="sq" style={{ background: 'var(--up)' }} />Subida</span>
                 </div>}>
            <p className="sub">
              {noRef
                ? <>Ventas reales de los restaurantes y canales filtrados ({data.timeline.step === 7 ? 'semanal' : 'diaria'}). Las ventas esperadas aparecerán cuando el modelo las calcule.</>
                : <>Suma de los restaurantes y canales filtrados ({data.timeline.step === 7 ? 'semanal' : 'diaria'}), en los días con referencia calculable.
                  {anomalies && ' Clic en un punto rojo o verde para ver sus anomalías.'}</>}
            </p>
            <TimelineChart data={data.timeline} onOpen={onOpen} height={mobile ? 250 : 330} />
          </Panel>
          <ChannelPanel channels={data.channels} sales={data.sales.channels} anomalies={anomalies} filters={filters} onFilter={onFilter} className="span-4" />
          <div id="sec-restaurantes" className="anchor span-12" />
          <RestaurantRanking filters={filters} onFilter={onFilter} anomalies={anomalies} className="span-7" mobile={mobile} />
          <div id="sec-geografia" className="span-5 anchor-inline">
            <GeoPanel geo={data.geo} salesGeo={data.sales.geo} anomalies={anomalies} filters={filters} onFilter={onFilter} />
          </div>
          <div id="sec-anomalias" className="anchor span-12" />
          <AnomalyTable filters={filters} meta={meta} onFilter={onFilter} onOpen={onOpen} className="span-12" mobile={mobile} />
          <div id="sec-informes" className="anchor span-12" />
          <DetectorPanel meta={meta} filters={filters} className="span-12" />
        </>
      )}
      {!data && !error && <div className="card panel span-12"><div className="skeleton tall" /></div>}
    </div>
  )
}
