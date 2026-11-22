import { memo, Suspense } from 'react'
import { Navigate, useParams } from 'react-router'
import InitPasswordPopup from '@/app/(auth)/init/InitPasswordPopup'
import InstallForm from '@/app/(auth)/install/installForm'
import SignInPage from '@/app/(auth)/signin/sign-in-page'
import AccessPoint from '@/app/components/app/access-point'
import { Apps } from '@/app/components/apps'
import { LoadingPlaceholder } from '@/app/components/base/loading-placeholder'
import AuthenticatedLayout from '@/app/(shareLayout)/components/authenticated-layout'
import ShareWorkflow from '@/app/components/share/text-generation'
import WorkflowApp from '@/app/components/workflow-app'
import { ShareLayout } from './layouts'

export function AppsPage() {
  return <Apps />
}

export function WorkflowPage() {
  return (
    <div className="size-full overflow-x-auto">
      <WorkflowApp />
    </div>
  )
}

export function AccessPointPage() {
  const { appId = '' } = useParams<{ appId: string }>()
  return (
    <Suspense fallback={<LoadingPlaceholder className="h-full" />}>
      <AccessPoint appId={appId} />
    </Suspense>
  )
}

export { InitPasswordPopup as InitPage, InstallForm as InstallPage, SignInPage }

export const PublicWorkflowPage = memo(function PublicWorkflowPage() {
  return (
    <ShareLayout>
      <AuthenticatedLayout>
        <ShareWorkflow isWorkflow />
      </AuthenticatedLayout>
    </ShareLayout>
  )
})

export function HomePage() {
  return <Navigate to="/apps" replace />
}
