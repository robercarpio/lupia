import { LayoutDashboard, AlertTriangle, Store, Map, FileBarChart, Settings, LogOut, Sparkles } from 'lucide-react'
import { Brand } from './Brand'

// Secciones del dashboard (cada una hace scroll a su bloque).
const items = [
  { icon: LayoutDashboard, label: 'Resumen', target: 'sec-resumen' },
  { icon: AlertTriangle, label: 'Anomalías', target: 'sec-anomalias' },
  { icon: Store, label: 'Restaurantes', target: 'sec-restaurantes' },
  { icon: Map, label: 'Geografía', target: 'sec-geografia' },
  { icon: FileBarChart, label: 'Informes', target: 'sec-informes' },
]

/** Selector de experiencia, con la misma interacción que un conmutador claro/oscuro. */
export function ModeSwitch({ mode, onChange }) {
  return (
    <div className={`mode-switch ${mode}`} role="radiogroup" aria-label="Modo de la aplicación">
      <span className="mode-thumb" aria-hidden />
      <button role="radio" aria-checked={mode === 'dashboard'} className={mode === 'dashboard' ? 'on' : ''}
              onClick={() => onChange('dashboard')} title="Dashboard">
        <LayoutDashboard size={16} /><span>Dashboard</span>
      </button>
      <button role="radio" aria-checked={mode === 'assistant'} className={mode === 'assistant' ? 'on' : ''}
              onClick={() => onChange('assistant')} title="AI Assistant">
        <Sparkles size={16} /><span>AI Assistant</span>
      </button>
    </div>
  )
}

export default function Sidebar({ mode = 'dashboard', onMode = () => {}, active = 0, onNavigate = () => {}, onHome }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <Brand onClick={onHome || (() => onMode('dashboard'))} />
      </div>
      <nav>
        {items.map(({ icon: Icon, label, target }, i) => (
          <button key={label} className={`nav-item ${mode === 'dashboard' && active === i ? 'active' : ''}`}
                  onClick={() => onNavigate(i, target)} title={label}>
            <Icon size={18} /> <span>{label}</span>
          </button>
        ))}
      </nav>
      <ModeSwitch mode={mode} onChange={onMode} />
      <div className="nav-bottom">
        <button className="nav-item" title="Ajustes"><Settings size={18} /> <span>Ajustes</span></button>
        <button className="nav-item" title="Salir"><LogOut size={18} /> <span>Salir</span></button>
      </div>
    </aside>
  )
}
