import type { ReactNode } from 'react'
import { useQuery, useSuspenseQueries } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useLocation, useNavigate, Outlet } from 'react-router'
import { FullScreenLoading } from '@/app/components/full-screen-loading'
import { accountPermissionsQueryOptions } from '@/features/accounts/client'
import { userProfileQueryOptions } from '@/features/account-profile/client'
import { consoleQuery } from '@/service/console'
import { refreshAccessTokenOrReLogin } from '@/service/refresh-token'
import { resolveLoginRedirectTarget } from '@/utils/login-redirect'

export function SetupBoundary() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { data: setup, isLoading } = useQuery(consoleQuery.setup.get.queryOptions())

  useEffect(() => {
    if (!setup || isLoading) return
    if (setup.step !== 'finished' && pathname !== '/install' && pathname !== '/init')
      navigate('/install', { replace: true })
    if (setup.step === 'finished' && pathname === '/install') navigate('/signin', { replace: true })
  }, [isLoading, navigate, pathname, setup])

  if (!setup || isLoading) return <FullScreenLoading />
  return <Outlet />
}

export function ProtectedConsoleBoundary({ children }: { children: ReactNode }) {
  useSuspenseQueries({
    queries: [userProfileQueryOptions(), accountPermissionsQueryOptions()],
  })
  return children
}

export function AuthRefreshPage() {
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    const target = resolveLoginRedirectTarget(
      new URLSearchParams(window.location.search).get('redirect_url'),
    ) ?? { kind: 'internal' as const, href: '/' }

    refreshAccessTokenOrReLogin(100000)
      .then(() => {
        if (!cancelled) navigate(target.href, { replace: true })
      })
      .catch(() => {
        if (!cancelled)
          navigate(`/signin?redirect_url=${encodeURIComponent(target.href)}`, { replace: true })
      })

    return () => {
      cancelled = true
    }
  }, [navigate])

  return <FullScreenLoading />
}

export function NotFoundPage() {
  return (
    <main
      style={{
        alignItems: 'center',
        backgroundColor: '#fff',
        color: '#000',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: 'system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
        height: '100vh',
        justifyContent: 'center',
        textAlign: 'center',
      }}
    >
      <div>
        <h1
          style={{
            borderRight: '1px solid rgba(0, 0, 0, 0.3)',
            display: 'inline-block',
            fontSize: 24,
            fontWeight: 500,
            lineHeight: '49px',
            margin: '0 20px 0 0',
            padding: '0 23px 0 0',
            verticalAlign: 'top',
          }}
        >
          404
        </h1>
        <div style={{ display: 'inline-block' }}>
          <h2 style={{ fontSize: 14, fontWeight: 400, lineHeight: '49px', margin: 0 }}>
            This page could not be found.
          </h2>
        </div>
      </div>
    </main>
  )
}
