import type { i18n as I18nInstance } from 'i18next'
import { TooltipProvider } from '@langgenius/dify-ui/tooltip'
import { Provider as JotaiProvider } from 'jotai/react'
import { NuqsAdapter } from 'nuqs/adapters/react-router/v8'
import { Suspense } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router'
import { AppToaster } from '@/app/notifications/app-toaster'
import { QueryStateProvider } from '@/app/query-state-provider'
import { TanStackQueryProvider } from '@/app/query-provider'
import { SystemFeaturesBootstrapBoundary } from '@/features/system-features/bootstrap-boundary'
import { ThemeProvider } from '@/features/theme/provider'
import { I18nProvider } from './i18n-provider'
import { AuthRefreshPage, NotFoundPage, SetupBoundary } from './bootstrap'
import { AppLayout, AuthLayout, ConsoleLayout } from './layouts'
import {
  AccessPointPage,
  AppsPage,
  HomePage,
  InitPage,
  InstallPage,
  PublicWorkflowPage,
  SignInPage,
  WorkflowPage,
} from './pages'

export default function App({ i18n }: { i18n: I18nInstance }) {
  return (
    <div className="isolate">
      <JotaiProvider>
        <ThemeProvider>
          <BrowserRouter>
            <NuqsAdapter>
              <QueryStateProvider>
                <TanStackQueryProvider>
                  <Suspense fallback={null}>
                    <I18nProvider i18n={i18n}>
                      <AppToaster timeout={5000} limit={3} />
                      <SystemFeaturesBootstrapBoundary>
                        <TooltipProvider delay={300} closeDelay={200}>
                          <Routes>
                            <Route path="/workflow/:token" element={<PublicWorkflowPage />} />
                            <Route path="/auth/refresh" element={<AuthRefreshPage />} />
                            <Route element={<SetupBoundary />}>
                              <Route element={<AuthLayout />}>
                                <Route path="/signin" element={<SignInPage />} />
                                <Route path="/install" element={<InstallPage />} />
                                <Route path="/init" element={<InitPage />} />
                              </Route>
                              <Route element={<ConsoleLayout />}>
                                <Route path="/apps" element={<AppsPage />} />
                                <Route path="/app/:appId" element={<AppLayout />}>
                                  <Route path="workflow" element={<WorkflowPage />} />
                                  <Route path="access-point" element={<AccessPointPage />} />
                                </Route>
                              </Route>
                            </Route>
                            <Route path="/" element={<HomePage />} />
                            <Route path="*" element={<NotFoundPage />} />
                          </Routes>
                        </TooltipProvider>
                      </SystemFeaturesBootstrapBoundary>
                    </I18nProvider>
                  </Suspense>
                </TanStackQueryProvider>
              </QueryStateProvider>
            </NuqsAdapter>
          </BrowserRouter>
        </ThemeProvider>
      </JotaiProvider>
    </div>
  )
}
