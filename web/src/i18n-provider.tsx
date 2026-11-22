import type { ReactNode } from 'react'
import type { i18n as I18nInstance } from 'i18next'
import type { Locale } from '@/i18n'
import { createInstance } from 'i18next'
import resourcesToBackend from 'i18next-resources-to-backend'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import { I18nDirectionProvider } from '@/app/components/provider/i18n-direction'
import { loadI18nResource } from '@/i18n/load-resource'
import { supportedLocales } from '@/i18n/locale'
import { namespaces } from '@/i18n/resources'
import { getInitOptions } from '@/i18n/settings'

export async function createBrowserI18n(locale: Locale) {
  const instance = createInstance()
  await instance.use(initReactI18next).use(resourcesToBackend(loadI18nResource)).init({
    ...getInitOptions(namespaces),
    lng: locale,
    resources: {},
    supportedLngs: supportedLocales,
    fallbackNS: false,
    react: { useSuspense: true },
  })
  return instance
}

export function I18nProvider({ i18n, children }: { i18n: I18nInstance; children: ReactNode }) {
  return (
    <I18nextProvider i18n={i18n}>
      <I18nDirectionProvider syncDocument>{children}</I18nDirectionProvider>
    </I18nextProvider>
  )
}
