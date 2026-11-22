import type { ScriptHTMLAttributes } from 'react'
import { useEffect } from 'react'

type ScriptProps = ScriptHTMLAttributes<HTMLScriptElement> & {
  strategy?: string
  onReady?: () => void
}

export default function Script({
  strategy: _strategy,
  onLoad,
  onError,
  onReady,
  children,
  dangerouslySetInnerHTML,
  ...props
}: ScriptProps) {
  useEffect(() => {
    const script = document.createElement('script')
    for (const [name, value] of Object.entries(props)) {
      if (typeof value === 'string') script.setAttribute(name, value)
      else if (value === true) script.setAttribute(name, '')
    }
    script.async = props.async ?? true
    if (typeof children === 'string') script.textContent = children
    if (dangerouslySetInnerHTML?.__html) script.textContent = String(dangerouslySetInnerHTML.__html)
    if (onLoad) script.addEventListener('load', (event) => onLoad(event as never))
    if (onReady) script.addEventListener('load', onReady)
    if (onError) script.addEventListener('error', (event) => onError(event as never))
    document.head.appendChild(script)
    return () => script.remove()
  }, [])

  return null
}
