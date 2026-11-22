import type { Locale } from './locale'
import type { DocLanguage } from '@/types/doc-paths'
import data from './languages'
import { supportedLocales } from './locale'

export type I18nText = Record<(typeof LanguagesSupported)[number], string>

export const languages = data.languages

export const LanguagesSupported: Locale[] = supportedLocales

const DOC_LANGUAGE: Record<string, DocLanguage | undefined> = {
  'zh-Hans': 'zh',
  'en-US': 'en',
}

export type AccessControlTemplateLanguage = 'zh' | 'en'

const ACCESS_CONTROL_TEMPLATE_LANGUAGE: Record<string, AccessControlTemplateLanguage> = {
  'zh-Hans': 'zh',
  'en-US': 'en',
}

export const localeMap: Record<Locale | 'en_US' | 'zh_Hans', string> = {
  'en-US': 'en',
  en_US: 'en',
  'zh-Hans': 'zh-cn',
  zh_Hans: 'zh-cn',
}

export const getDocLanguage = (locale: string): DocLanguage => {
  return DOC_LANGUAGE[locale] || 'en'
}

export const getAccessControlTemplateLanguage = (locale: string): AccessControlTemplateLanguage => {
  return ACCESS_CONTROL_TEMPLATE_LANGUAGE[locale] || 'en'
}
