import type { ReactNode } from 'react'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

type ThemeContextValue = {
  theme?: string
  setTheme: (theme: string | ((current?: string) => string)) => void
  forcedTheme?: string
  resolvedTheme?: string
  themes: string[]
  systemTheme?: 'light' | 'dark'
}
type ThemeProviderProps = {
  children: ReactNode
  attribute?: string
  defaultTheme?: string
  enableSystem?: boolean
  enableColorScheme?: boolean
  disableTransitionOnChange?: boolean
  storageKey?: string
  forcedTheme?: string
  themes?: string[]
  nonce?: string
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)
const media = () => window.matchMedia('(prefers-color-scheme: dark)')
const systemTheme = () => (media().matches ? 'dark' : 'light')

export function ThemeProvider({
  children,
  attribute = 'data-theme',
  defaultTheme = 'system',
  enableSystem = true,
  enableColorScheme = true,
  disableTransitionOnChange = false,
  storageKey = 'theme',
  forcedTheme,
  themes = ['light', 'dark'],
}: ThemeProviderProps) {
  const [selected, setSelected] = useState(() => {
    try {
      return localStorage.getItem(storageKey) || defaultTheme
    } catch {
      return defaultTheme
    }
  })
  const [system, setSystem] = useState<'light' | 'dark'>(systemTheme)
  const theme = forcedTheme || selected
  const resolvedTheme = theme === 'system' && enableSystem ? system : theme

  useEffect(() => {
    const query = media()
    const update = () => setSystem(systemTheme())
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    const root = document.documentElement
    const cleanup: (() => void)[] = []
    if (disableTransitionOnChange) {
      const style = document.createElement('style')
      style.appendChild(document.createTextNode('*,*::before,*::after{transition:none!important}'))
      document.head.appendChild(style)
      cleanup.push(() => style.remove())
    }
    for (const name of attribute.split(' ')) root.setAttribute(name, resolvedTheme)
    if (enableColorScheme) root.style.colorScheme = resolvedTheme
    return () => cleanup.forEach((dispose) => dispose())
  }, [attribute, disableTransitionOnChange, enableColorScheme, resolvedTheme])

  const setTheme = useCallback<ThemeContextValue['setTheme']>(
    (next) => {
      setSelected((current) => {
        const value = typeof next === 'function' ? next(current) : next
        try {
          localStorage.setItem(storageKey, value)
        } catch {}
        return value
      })
    },
    [storageKey],
  )

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, forcedTheme, resolvedTheme, themes, systemTheme: system }),
    [forcedTheme, resolvedTheme, setTheme, system, theme, themes],
  )
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within ThemeProvider')
  return context
}
