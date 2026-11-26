import { Bell } from 'lucide-react'
import { ThemeButton } from './Brand'

/**
 * Cabecera común. Conserva el diseño original (título, caja de filtros,
 * notificaciones y avatar); el contenido de la caja de filtros lo decide cada modo.
 */
export default function Topbar({ title, subtitle, children, alerts = 0, unseen = alerts > 0, onAlerts, onSeen, theme, onTheme }) {
  return (
    <header className="topbar">
      <div>
        <h1>{title}</h1>
        <p className="muted">{subtitle}</p>
      </div>
      <div className="topbar-right">
        {children && <div className="filter-box">{children}</div>}
        {onTheme && <ThemeButton theme={theme} onTheme={onTheme} />}
        <button className="icon-btn" aria-label={`Alertas de prioridad alta: ${alerts}${unseen ? ' (nuevas)' : ''}`} title={`${alerts} alertas de prioridad alta`} onClick={(e) => { onAlerts?.(e); onSeen?.() }}>
          <Bell size={18} />{unseen && <i className="badge" />}
        </button>
        <span className="user-avatar">CE</span>
      </div>
    </header>
  )
}
