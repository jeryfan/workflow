import type { Locale } from './locale'

export type PluginLanguage = 'en_US' | 'zh_Hans'

export const getPluginLanguage = (locale: Locale): PluginLanguage => {
  return locale === 'zh-Hans' ? 'zh_Hans' : 'en_US'
}

export const getModelLanguage = (locale: Locale) => locale.replace('-', '_')

export const renderI18nObject = (
  obj: Record<string, string | null | undefined> | null | undefined,
  language: string,
) => {
  if (!obj) return ''
  if (obj?.[language]) return obj[language]
  if (obj?.en_US) return obj.en_US
  return Object.values(obj).find((value): value is string => !!value) || ''
}
