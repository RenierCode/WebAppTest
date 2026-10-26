import { useEffect, useState } from 'react'

const QUERY = '(max-width: 767px)'      // below Tailwind's md breakpoint: phones

/** True on phone-sized screens, updating when the window is resized or the phone is rotated. */
export function useIsMobile() {
  const [mobile, setMobile] = useState(() => window.matchMedia(QUERY).matches)
  useEffect(() => {
    const media = window.matchMedia(QUERY)
    const onChange = () => setMobile(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return mobile
}
