import { useEffect, useState } from 'react'

/** true mientras se cumpla la media query (p. ej. pantallas de móvil). */
export function useMedia(query) {
  const [match, setMatch] = useState(() => typeof window !== 'undefined' && matchMedia(query).matches)
  useEffect(() => {
    const mq = matchMedia(query)
    const on = () => setMatch(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return match
}

export const MOBILE = '(max-width: 760px)'
export const useIsMobile = () => useMedia(MOBILE)
