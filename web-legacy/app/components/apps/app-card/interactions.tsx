'use client'

import type {
  AppPartial,
  EnvironmentVariableItemResponse,
} from '@dify/contracts/api/console/apps/types.gen'
import type { MouseEvent, ReactElement } from 'react'
import type { DuplicateAppModalProps } from '@/app/components/app/duplicate-modal'
import { zIconType } from '@dify/contracts/api/console/apps/zod.gen'
import { cn } from '@langgenius/dify-ui/cn'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@langgenius/dify-ui/context-menu'
import { Dialog, DialogContent } from '@langgenius/dify-ui/dialog'
import { IconButton } from '@langgenius/dify-ui/icon-button'
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from '@langgenius/dify-ui/menu'
import { useMutation, useSuspenseQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import dynamic from 'next/dynamic'
import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AppMetadataForm } from '@/app/components/app/app-metadata'
import { useExportAppDsl, useExportWorkflowAppDsl } from '@/app/components/app/use-export-app-dsl'
import {
  getStepByStepTourMenuContentProps,
  useStepByStepTourControlledMenu,
} from '@/app/components/step-by-step-tour/menu'
import { toast } from '@/app/notifications'
import { workspacePermissionKeysAtom } from '@/context/permission-state'
import { userProfileQueryOptions } from '@/features/account-profile/client'
import { systemFeaturesQueryOptions } from '@/features/system-features/client'
import { useRouter } from '@/next/navigation'
import { consoleQuery } from '@/service/console'
import { getRedirection } from '@/utils/app-redirection'
import { getErrorMessage } from '@/utils/error-message'
import { getAppACLCapabilities, hasPermission } from '@/utils/permission'

const DuplicateAppModal = dynamic(() => import('@/app/components/app/duplicate-modal'), {
  ssr: false,
})
const SwitchAppModal = dynamic(() => import('@/app/components/app/switch-app-modal'), {
  ssr: false,
})
const AppExportConfirmModal = dynamic(() => import('@/app/components/app/export-confirm-modal'), {
  ssr: false,
})

const OPERATIONS_MENU_POPUP_CLASS_NAME = 'min-w-[216px]'
type AppCardOperationsMenuItemsProps = {
  app: AppPartial
  kind: 'context' | 'menu'
  shouldShowEditOption: boolean
  shouldShowDuplicateOption: boolean
  shouldShowExportOption: boolean
  shouldShowSwitchOption: boolean
  shouldShowAccessConfigOption: boolean
  shouldShowDeleteOption: boolean
  isExporting: boolean
  onEdit: () => void
  onDuplicate: () => void
  onExport: () => void
  onSwitch: () => void
  onDelete: () => void
  onAccessConfig: () => void
}

function AppCardOperationsMenuItems({
  app,
  kind,
  shouldShowEditOption,
  shouldShowDuplicateOption,
  shouldShowExportOption,
  shouldShowSwitchOption,
  shouldShowAccessConfigOption,
  shouldShowDeleteOption,
  isExporting,
  onEdit,
  onDuplicate,
  onExport,
  onSwitch,
  onDelete,
  onAccessConfig,
}: AppCardOperationsMenuItemsProps) {
  const { t } = useTranslation(['app', 'common', 'navigation'])
  const hasEditGroup = shouldShowEditOption
  const hasCreateExportGroup = shouldShowDuplicateOption || shouldShowExportOption
  const hasSwitchOrExploreGroup = shouldShowSwitchOption
  const hasAccessDeleteGroup = shouldShowAccessConfigOption || shouldShowDeleteOption
  const ActionItem = kind === 'context' ? ContextMenuItem : MenuItem
  const ActionSeparator = kind === 'context' ? ContextMenuSeparator : MenuSeparator

  function handleMenuAction(event: MouseEvent<HTMLElement>, action: () => void) {
    event.stopPropagation()
    event.preventDefault()
    action()
  }

  return (
    <>
      {shouldShowEditOption && (
        <ActionItem className="gap-2 px-3" onClick={(event) => handleMenuAction(event, onEdit)}>
          <span className="system-sm-regular text-text-secondary">
            {t(($) => $.editApp, { ns: 'app' })}
          </span>
        </ActionItem>
      )}
      {hasEditGroup &&
        (hasCreateExportGroup || hasSwitchOrExploreGroup || hasAccessDeleteGroup) && (
          <ActionSeparator />
        )}
      {shouldShowDuplicateOption && (
        <ActionItem
          className="gap-2 px-3"
          onClick={(event) => handleMenuAction(event, onDuplicate)}
        >
          <span className="system-sm-regular text-text-secondary">
            {t(($) => $.duplicate, { ns: 'app' })}
          </span>
        </ActionItem>
      )}
      {shouldShowExportOption && (
        <ActionItem
          className="gap-2 px-3"
          disabled={isExporting}
          onClick={(event) => handleMenuAction(event, onExport)}
        >
          <span className="system-sm-regular text-text-secondary">
            {t(($) => $.exportApp, { ns: 'app' })}
          </span>
        </ActionItem>
      )}
      {hasCreateExportGroup && (hasSwitchOrExploreGroup || hasAccessDeleteGroup) && (
        <ActionSeparator />
      )}
      {shouldShowSwitchOption && (
        <ActionItem className="gap-2 px-3" onClick={(event) => handleMenuAction(event, onSwitch)}>
          <span className="text-sm/5 text-text-secondary">{t(($) => $.switch, { ns: 'app' })}</span>
        </ActionItem>
      )}
      {hasSwitchOrExploreGroup && hasAccessDeleteGroup && <ActionSeparator />}
      {shouldShowAccessConfigOption && (
        <ActionItem
          className="gap-2 px-3"
          onClick={(event) => handleMenuAction(event, onAccessConfig)}
        >
          <span className="text-sm/5 text-text-secondary">
            {t(($) => $['settings.resourceAccess'], { ns: 'navigation' })}
          </span>
        </ActionItem>
      )}
      {shouldShowAccessConfigOption && shouldShowDeleteOption && <ActionSeparator />}
      {shouldShowDeleteOption && (
        <ActionItem
          variant="destructive"
          className="gap-2 px-3"
          onClick={(event) => handleMenuAction(event, onDelete)}
        >
          <span className="system-sm-regular">
            {t(($) => $['operation.delete'], { ns: 'common' })}
          </span>
        </ActionItem>
      )}
    </>
  )
}

type AppCardInteractionsProps = {
  app: AppPartial
  children: ReactElement
  onDelete: () => void
  stepByStepTourActionMenuOpen?: boolean
  stepByStepTourActionMenuHighlightPart?: string
}

export function AppCardInteractions({
  app,
  children,
  onDelete,
  stepByStepTourActionMenuOpen = false,
  stepByStepTourActionMenuHighlightPart,
}: AppCardInteractionsProps) {
  const { t } = useTranslation(['app', 'common'])
  const { data: systemFeatures } = useSuspenseQuery(systemFeaturesQueryOptions())
  const { data: currentUserId } = useSuspenseQuery({
    ...userProfileQueryOptions(),
    select: (data) => data.profile.id,
  })
  const workspacePermissionKeys = useAtomValue(workspacePermissionKeysAtom)
  const isRbacEnabled = systemFeatures.rbac_enabled
  const { push } = useRouter()
  const { mutate: copyApp } = useMutation(
    consoleQuery.apps.byAppId.copy.post.mutationOptions({ context: { silent: true } }),
  )

  const edit = useMutation(
    consoleQuery.apps.byAppId.put.mutationOptions({ context: { silent: true } }),
  )
  const editNameRef = useRef<HTMLInputElement>(null)
  const [activeDialog, setActiveDialog] = useState<'duplicate' | 'edit' | 'switch' | null>(null)
  const operationsMenu = useStepByStepTourControlledMenu({
    allowTriggerCloseWhileControlled: false,
    controlledOpen: stepByStepTourActionMenuOpen,
  })
  const isOperationsMenuOpen = operationsMenu.open
  const [secretEnvList, setSecretEnvList] = useState<EnvironmentVariableItemResponse[]>([])
  const {
    exportAppDsl,
    isExporting: isAppDslExporting,
    errorMessage: exportError,
    reset: resetExport,
  } = useExportAppDsl()
  const { exportWorkflowAppDsl, isExporting: isWorkflowAppDslExporting } = useExportWorkflowAppDsl()
  const isExporting = isAppDslExporting || isWorkflowAppDslExporting
  const appIconType = zIconType.safeParse(app.icon_type).data ?? null
  const resourceMaintainer = app.maintainer ?? undefined
  const maintainerPermissionOptions = useMemo(
    () => ({
      currentUserId,
      resourceMaintainer,
      workspacePermissionKeys,
      isRbacEnabled,
    }),
    [currentUserId, isRbacEnabled, resourceMaintainer, workspacePermissionKeys],
  )
  const appACLCapabilities = useMemo(
    () => getAppACLCapabilities(app.permission_keys, maintainerPermissionOptions),
    [app.permission_keys, maintainerPermissionOptions],
  )
  const canCreateApp = hasPermission(workspacePermissionKeys, 'app.create_and_management')

  const onCopy: DuplicateAppModalProps['onConfirm'] = ({
    name,
    icon_type,
    icon,
    icon_background,
  }) => {
    try {
      copyApp(
        {
          params: { app_id: app.id },
          body: {
            name,
            icon_type,
            icon,
            icon_background,
          },
        },
        {
          onSuccess: (newApp) => {
            if (!('mode' in newApp)) {
              toast.error(t(($) => $['newApp.appCreateFailed'], { ns: 'app' }))
              return
            }

            setActiveDialog(null)
            getRedirection(newApp, push, {
              currentUserId,
              resourceMaintainer: newApp.maintainer ?? undefined,
              workspacePermissionKeys,
              isRbacEnabled,
            })
          },
          onError: (error) =>
            toast.error(
              getErrorMessage(error) ?? t(($) => $['newApp.appCreateFailed'], { ns: 'app' }),
            ),
        },
      )
    } catch (error) {
      toast.error(getErrorMessage(error) ?? t(($) => $['newApp.appCreateFailed'], { ns: 'app' }))
    }
    return Promise.resolve()
  }

  const onExport = async (include = false) => {
    const result = await exportAppDsl({
      appId: app.id,
      appName: app.name,
      includeSecret: include,
      feedback: 'dialog',
    })
    return result.status === 'downloaded'
  }

  const exportCheck = async () => {
    if (isExporting) return

    resetExport()
    const isWorkflowApp = app.mode === 'workflow' || app.mode === 'advanced-chat'
    const result = isWorkflowApp
      ? await exportWorkflowAppDsl({ appId: app.id, appName: app.name })
      : await exportAppDsl({ appId: app.id, appName: app.name })
    if (result?.status === 'confirmation-required') setSecretEnvList(result.secretEnvList)
  }

  const shouldShowEditOption = appACLCapabilities.canEdit
  const shouldShowDuplicateOption = canCreateApp
  const shouldShowExportOption = appACLCapabilities.canImportExportDSL
  const shouldShowSwitchOption =
    appACLCapabilities.canEdit && (app.mode === 'completion' || app.mode === 'chat')
  const shouldShowAccessConfigOption = appACLCapabilities.canAccessConfig
  const shouldShowDeleteOption = appACLCapabilities.canDelete
  const shouldShowOperationsMenu =
    shouldShowEditOption ||
    shouldShowDuplicateOption ||
    shouldShowExportOption ||
    shouldShowSwitchOption ||
    shouldShowAccessConfigOption ||
    shouldShowDeleteOption
  const operationsMenuItemsProps = {
    app,
    shouldShowEditOption,
    shouldShowDuplicateOption,
    shouldShowExportOption,
    shouldShowSwitchOption,
    shouldShowAccessConfigOption,
    shouldShowDeleteOption,
    isExporting,
    onEdit: () => setActiveDialog('edit'),
    onDuplicate: () => setActiveDialog('duplicate'),
    onExport: exportCheck,
    onSwitch: () => setActiveDialog('switch'),
    onDelete,
    onAccessConfig: () => push(`/app/${app.id}/access-config`),
  }

  return (
    <>
      {shouldShowOperationsMenu ? (
        <ContextMenu>
          <ContextMenuTrigger render={children} />
          <ContextMenuContent className={OPERATIONS_MENU_POPUP_CLASS_NAME}>
            <AppCardOperationsMenuItems kind="context" {...operationsMenuItemsProps} />
          </ContextMenuContent>
        </ContextMenu>
      ) : (
        children
      )}
      <div
        className={cn(
          'pointer-events-none absolute inset-e-[-0.5px] top-[-0.5px] flex h-16 w-30 items-start justify-end bg-[linear-gradient(67deg,var(--color-components-card-bg-alt-transparent)_0%,var(--color-components-card-bg-alt)_75%)] p-2 opacity-0 rtl:bg-[linear-gradient(293deg,var(--color-components-card-bg-alt-transparent)_0%,var(--color-components-card-bg-alt)_75%)]',
          isOperationsMenuOpen || isExporting
            ? 'opacity-100'
            : 'group-focus-within:opacity-100 group-hover:opacity-100 has-data-popup-open:opacity-100 [@media(hover:none)]:opacity-100',
        )}
      >
        <div
          className={cn(
            'flex items-center overflow-hidden rounded-[10px] border-[0.5px] border-components-actionbar-border bg-components-actionbar-bg p-0.5 shadow-lg backdrop-blur-xs',
            isOperationsMenuOpen || isExporting
              ? 'pointer-events-auto'
              : 'pointer-events-none group-focus-within:pointer-events-auto group-hover:pointer-events-auto has-data-popup-open:pointer-events-auto [@media(hover:none)]:pointer-events-auto',
          )}
        >
          {shouldShowOperationsMenu && (
            <Menu
              modal={false}
              open={isOperationsMenuOpen}
              onOpenChange={operationsMenu.onOpenChange}
            >
              <MenuTrigger
                render={
                  <IconButton
                    size="lg"
                    aria-label={t(($) => $['operation.moreActionsFor'], {
                      ns: 'common',
                      name: app.name,
                    })}
                    className="data-popup-open:bg-state-base-hover"
                  >
                    <span aria-hidden className="i-ri-more-fill size-4.5 text-text-tertiary" />
                  </IconButton>
                }
              />
              <MenuContent
                align="end"
                {...getStepByStepTourMenuContentProps({
                  highlightPart: stepByStepTourActionMenuHighlightPart,
                  interactionMode: operationsMenu.controlled ? 'presentation' : 'interactive',
                  className: OPERATIONS_MENU_POPUP_CLASS_NAME,
                })}
              >
                <AppCardOperationsMenuItems kind="menu" {...operationsMenuItemsProps} />
              </MenuContent>
            </Menu>
          )}
        </div>
      </div>
      <Dialog
        open={activeDialog === 'edit'}
        disablePointerDismissal
        onOpenChange={(open, details) => {
          if (!open && edit.isPending) details.cancel()
          else if (!open) setActiveDialog(null)
        }}
        onOpenChangeComplete={(open) => {
          if (!open) {
            edit.reset()
          }
        }}
      >
        <DialogContent initialFocus={editNameRef}>
          <AppMetadataForm
            appDetail={app}
            nameInputRef={editNameRef}
            operation="edit"
            isPending={edit.isPending}
            error={edit.error?.message}
            onSubmit={(body) => {
              if (edit.isPending) return
              edit.mutate(
                { params: { app_id: app.id }, body },
                {
                  onSuccess: () => setActiveDialog(null),
                },
              )
            }}
          />
        </DialogContent>
      </Dialog>
      {activeDialog === 'duplicate' && (
        <DuplicateAppModal
          appName={app.name}
          icon_type={appIconType}
          icon={app.icon ?? ''}
          icon_background={app.icon_background}
          icon_url={app.icon_url}
          show
          onConfirm={onCopy}
          onHide={() => setActiveDialog(null)}
        />
      )}
      {activeDialog === 'switch' && (
        <SwitchAppModal open sourceApp={app} onClose={() => setActiveDialog(null)} />
      )}
      {secretEnvList.length > 0 && (
        <AppExportConfirmModal
          envList={secretEnvList}
          errorMessage={exportError}
          isExporting={isExporting}
          onConfirm={onExport}
          onClose={() => setSecretEnvList([])}
        />
      )}
    </>
  )
}
