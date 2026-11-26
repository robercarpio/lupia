import { Moon, Sun } from 'lucide-react'
import icono from '../assets/lupia-icono.png'
import texto from '../assets/lupia-texto.png'

/** Marca Lupia: icono + logotipo. `compact` muestra solo el icono. */
export function Brand({ className = '', compact = false, onClick }) {
  const content = (
    <>
      <img className="brand-logo-icon" src={icono} alt={compact ? 'Lupia' : ''} />
      {!compact && <img className="brand-logo-text brand-name" src={texto} alt="Lupia" />}
    </>
  )
  // Con onClick la marca es un botón que lleva al inicio del dashboard.
  return onClick
    ? <button type="button" className={`brand-logo brand-link ${className}`} onClick={onClick} title="Ir al dashboard" aria-label="Lupia · ir al dashboard">{content}</button>
    : <span className={`brand-logo ${className}`}>{content}</span>
}

/** Botón claro/oscuro: muestra el icono del tema al que se cambia. */
export function ThemeButton({ theme, onTheme }) {
  const dark = theme === 'dark'
  return (
    <button className="icon-btn theme-btn" onClick={onTheme}
            aria-label={dark ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro'}
            title={dark ? 'Modo claro' : 'Modo oscuro'}>
      {dark ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  )
}
