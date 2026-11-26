import { useCallback, useEffect, useState } from 'react'
import { flushSync } from 'react-dom'

const root = () => document.documentElement
const read = () => (root().dataset.theme === 'dark' ? 'dark' : 'light')

/**
 * Tema claro/oscuro (ambos con la paleta Lupia). Respeta la preferencia del sistema hasta que el usuario elige.
 * El cambio se anima con la View Transitions API: el navegador captura la página
 * entera antes y después, y el tema nuevo se revela con un círculo que crece desde
 * el botón. Así no se interpolan cientos de elementos (gradientes, SVG, canvas),
 * que era lo que producía saltos. Sin soporte o con «reducir movimiento»: cambio instantáneo.
 */
export function useTheme() {
  const [theme, setTheme] = useState(read)

  useEffect(() => {
    let saved = null
    try { saved = localStorage.getItem('theme') } catch { /* sin almacenamiento */ }
    if (saved) return
    const mq = matchMedia('(prefers-color-scheme: dark)')
    const on = (e) => { root().dataset.theme = e.matches ? 'dark' : 'light'; setTheme(read()) }
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])

  const toggle = useCallback((event) => {
    const next = read() === 'dark' ? 'light' : 'dark'
    const apply = () => {
      root().dataset.theme = next
      flushSync(() => setTheme(next)) // el icono y el tema cambian en el mismo fotograma capturado
    }
    try { localStorage.setItem('theme', next) } catch { /* sin almacenamiento */ }

    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    if (!document.startViewTransition || reduce) { apply(); return }

    const rect = event?.currentTarget?.getBoundingClientRect?.()
    const x = rect ? rect.left + rect.width / 2 : innerWidth - 40
    const y = rect ? rect.top + rect.height / 2 : 40
    const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y))
    root().classList.add('vt-theme')
    const vt = document.startViewTransition(apply)
    vt.ready.then(() => {
      root().animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
        { duration: 560, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', pseudoElement: '::view-transition-new(root)' },
      )
    }).catch(() => {})
    vt.finished.finally(() => root().classList.remove('vt-theme'))
  }, [])

  return [theme, toggle]
}
