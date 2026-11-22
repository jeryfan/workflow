import type { ReactNode } from 'react'
import Cookies from 'js-cookie'
import { Outlet, useParams } from 'react-router'
import AppDetailLayout from '@/app/(commonLayout)/app/(appDetailLayout)/[appId]/layout-main'
import { TriggerEventsLimitPrompt } from '@/app/components/billing/trigger-events-limit-modal/prompt'
import { ModelCredentialDialog } from '@/app/components/header/account-setting/model-provider-page/model-modal/dialog'
import { AppDetailSidebar } from '@/app/components/app-sidebar/app-detail-sidebar'
import { AuthShell } from '@/app/components/auth-shell'
import { DETAIL_SIDEBAR_COOKIE_NAME, parseDetailSidebarMode } from '@/app/components/detail-sidebar/cookie'
import MainNavLayout from '@/app/components/main-nav/layout'
import { getPlatformFromUserAgent } from '@/app/components/main-nav/shortcut-platform'
import { EventEmitterContextProvider } from '@/context/event-emitter-provider'
import WebAppStoreProvider from '@/context/web-app-context'
import Splash from '@/app/(shareLayout)/components/splash'
import { ProtectedConsoleBoundary } from './bootstrap'

function ConsoleContextProviders({ children }: { children: ReactNode }) {
  return (
    <EventEmitterContextProvider>
      {children}
      <ModelCredentialDialog />
      <TriggerEventsLimitPrompt />
    </EventEmitterContextProvider>
  )
}

export function AuthLayout() {
  return (
    <AuthShell>
      <Outlet />
    </AuthShell>
  )
}

export function ConsoleLayout() {
  return (
    <ProtectedConsoleBoundary>
      <div className="flex h-dvh flex-col overflow-hidden">
        <ConsoleContextProviders>
          <MainNavLayout
            detailSidebar={<AppDetailSidebar />}
            initialPlatform={getPlatformFromUserAgent(window.navigator.userAgent)}
            initialDetailSidebarMode={parseDetailSidebarMode(
              Cookies.get(DETAIL_SIDEBAR_COOKIE_NAME),
            )}
          >
            <Outlet />
          </MainNavLayout>
        </ConsoleContextProviders>
      </div>
    </ProtectedConsoleBoundary>
  )
}

export function AppLayout() {
  const { appId = '' } = useParams<{ appId: string }>()
  return (
    <AppDetailLayout appId={appId}>
      <Outlet />
    </AppDetailLayout>
  )
}

export function ShareLayout({ children }: { children: ReactNode }) {
  return (
    <div className="h-dvh min-w-75">
      <WebAppStoreProvider>
        <Splash>{children}</Splash>
      </WebAppStoreProvider>
    </div>
  )
}
