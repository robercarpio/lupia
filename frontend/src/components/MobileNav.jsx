import { Bell, LayoutDashboard, AlertTriangle, Store, Map, Sparkles } from 'lucide-react'
import { Brand, ThemeButton } from './Brand'

/** Cabecera compacta para móvil: marca, título del modo y acciones rápidas. */
export function MobileHeader({ title, subtitle, theme, onTheme, alerts = 0, unseen = alerts > 0, onAlerts, onSeen, onHome, children }) {
  return (
    <header className="m-header">
      <div className="m-header-row">
        <Brand compact className="m-brand" onClick={onHome} />
        <div className="m-title">
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <ThemeButton theme={theme} onTheme={onTheme} />
        <button className="icon-btn" onClick={(e) => { onAlerts?.(e); onSeen?.() }} aria-label={`Alertas de prioridad alta: ${alerts}${unseen ? ' (nuevas)' : ''}`}>
          <Bell size={18} />{unseen && <i className="badge" />}
        </button>
      </div>
      {children && <div className="m-header-extra">{children}</div>}
    </header>
  )
}

const TABS = [
  { icon: LayoutDashboard, label: 'Resumen', target: 'sec-resumen', i: 0 },
  { icon: AlertTriangle, label: 'Anomalías', target: 'sec-anomalias', i: 1 },
  { icon: Store, label: 'Ranking', target: 'sec-restaurantes', i: 2 },
  { icon: Map, label: 'Mapa', target: 'sec-geografia', i: 3 },
]

/**
 * Barra de pestañas inferior: en móvil sustituye al sidebar.
 * Las cuatro primeras son secciones del dashboard; «Asistente» cambia de modo.
 */
export function MobileTabBar({ mode, active, onNavigate, onMode }) {
  return (
    <nav className="m-tabbar" aria-label="Navegación principal">
      {TABS.map(({ icon: Icon, label, target, i }) => (
        <button key={label} className={`m-tab ${mode === 'dashboard' && active === i ? 'on' : ''}`} onClick={() => onNavigate(i, target)}>
          <Icon size={21} /><span>{label}</span>
        </button>
      ))}
      <button className={`m-tab ai ${mode === 'assistant' ? 'on' : ''}`} onClick={() => onMode(mode === 'assistant' ? 'dashboard' : 'assistant')}
              aria-pressed={mode === 'assistant'}>
        <span className="m-ai-icon"><Sparkles size={17} /></span><span>Asistente</span>
      </button>
    </nav>
  )
}
