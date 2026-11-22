import type { Locale } from '@daypicker/react'
import { enUS } from '@daypicker/react/locale/en-US'
import { useEffect, useState } from 'react'

const localeLoaders: Record<string, () => Promise<Locale>> = {
  'zh-Hans': () => import('@daypicker/react/locale/zh-CN').then((module) => module.zhCN),
}

const loadedLocales = new Map<string, Locale>([['en-US', enUS]])

export function useCalendarLocale(language: string, overrides?: Partial<Locale>): Locale {
  const key = Object.hasOwn(localeLoaders, language) ? language : 'en-US'
  const [loaded, setLoaded] = useState<{ language: string; locale: Locale }>()
  const cachedLocale = loadedLocales.get(key)
  const locale =
    cachedLocale ?? (loaded?.language === key ? loaded.locale : { ...enUS, code: language })

  useEffect(() => {
    if (cachedLocale) return
    let active = true
    const cached = loadedLocales.get(key)
    const request = cached ? Promise.resolve(cached) : localeLoaders[key]!()
    void request.then(
      (locale) => {
        loadedLocales.set(key, locale)
        if (active) setLoaded({ language: key, locale })
      },
      () => {
        if (active) setLoaded({ language: key, locale: enUS })
      },
    )
    return () => {
      active = false
    }
  }, [key, cachedLocale])

  return overrides
    ? { ...locale, ...overrides, labels: { ...locale.labels, ...overrides.labels } }
    : locale
}
