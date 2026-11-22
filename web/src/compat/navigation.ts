import { useMemo } from 'react'
import {
  useLocation,
  useNavigate,
  useParams as useRouteParams,
  useSearchParams as useRouteSearchParams,
} from 'react-router'

export type ReadonlyURLSearchParams = URLSearchParams

export function useRouter() {
  const navigate = useNavigate()

  return useMemo(
    () => ({
      back: () => navigate(-1),
      forward: () => navigate(1),
      push: (href: string, _options?: { scroll?: boolean }) => navigate(href),
      replace: (href: string, _options?: { scroll?: boolean }) => navigate(href, { replace: true }),
      refresh: () => window.location.reload(),
      prefetch: (_href: string) => Promise.resolve(),
    }),
    [navigate],
  )
}

export function usePathname() {
  return useLocation().pathname
}

export function useSearchParams() {
  const [searchParams] = useRouteSearchParams()
  return searchParams
}

export function useParams<T extends Record<string, string | undefined>>() {
  return useRouteParams() as T
}

export function useSelectedLayoutSegments() {
  return useLocation().pathname.split('/').filter(Boolean)
}

export function useSelectedLayoutSegment() {
  return useSelectedLayoutSegments()[0] ?? null
}

export function useServerInsertedHTML(_callback: () => React.ReactNode) {}

export function redirect(href: string) {
  window.location.replace(href)
}

export function notFound(): never {
  throw new Error('Page not found')
}
