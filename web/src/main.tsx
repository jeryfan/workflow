import { createRoot } from 'react-dom/client'
import { normalizeLocale } from '@/i18n/locale'
import App from './app'
import { createBrowserI18n } from './i18n-provider'
import '@/app/styles/globals.css'
import '@/app/styles/markdown.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root')

const locale = normalizeLocale(
  document.cookie
    .split('; ')
    .find((cookie) => cookie.startsWith('locale='))
    ?.split('=')[1] || window.navigator.language,
)
const i18n = await createBrowserI18n(locale)
createRoot(root).render(<App i18n={i18n} />)
