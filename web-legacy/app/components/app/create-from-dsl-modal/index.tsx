'use client'

import type { AppImportPayload, AppMode, Import } from '@dify/contracts/api/console/apps/types.gen'
import type { HotkeyChord } from '@tanstack/react-hotkeys'
import { Button } from '@langgenius/dify-ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogHeaderCloseButton,
  DialogTitle,
} from '@langgenius/dify-ui/dialog'
import { Form } from '@langgenius/dify-ui/form'
import { Kbd, KbdGroup } from '@langgenius/dify-ui/kbd'
import { formatForDisplay, matchesKeyboardEvent } from '@tanstack/react-hotkeys'
import { useMutation, useQuery, useSuspenseQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import AppsFull from '@/app/components/billing/apps-full-in-dialog'
import { getCountQuota, isQuotaReached } from '@/app/components/billing/quota'
import { usePluginDependencies } from '@/app/components/workflow/plugin-dependency/hooks'
import { toast } from '@/app/notifications'
import { workspacePermissionKeysAtom } from '@/context/permission-state'
import { userProfileQueryOptions } from '@/features/account-profile/client'
import { systemFeaturesQueryOptions } from '@/features/system-features/client'
import { useRouter } from '@/next/navigation'
import { consoleQuery } from '@/service/console'
import { getRedirection } from '@/utils/app-redirection'
import { trackCreateApp } from '@/utils/create-app-tracking'
import { getErrorMessage } from '@/utils/error-message'
import { resolveImportedAppRedirectionTarget } from '@/utils/imported-app-redirection'
import DSLConfirmModal from './dsl-confirm-modal'
import DSLImportWarningDescription from './dsl-import-warning-description'
import { Uploader } from './uploader'

type CreateFromDSLModalProps = {
  show: boolean
  onSuccess?: () => void
  onClose: () => void
  droppedFile?: File
}

type PendingImport = {
  id: string
  importedVersion: string
  systemVersion: string
}

const CREATE_FROM_DSL_HOTKEY = 'Mod+Enter' satisfies HotkeyChord

function getImportedAppMode(mode?: string | null): AppMode | undefined {
  switch (mode) {
    case 'completion':
      return 'completion'
    case 'workflow':
      return 'workflow'
    case 'chat':
      return 'chat'
    case 'advanced-chat':
      return 'advanced-chat'
    case 'agent-chat':
      return 'agent-chat'
    case 'agent':
      return 'agent'
    default:
      return undefined
  }
}

function CreateFromDSLModal({ show, onSuccess, onClose, droppedFile }: CreateFromDSLModalProps) {
  const { push } = useRouter()
  const { t } = useTranslation(['app', 'common'])
  const browseButtonRef = useRef<HTMLButtonElement>(null)
  const [currentFile, setCurrentFile] = useState<File | undefined>(droppedFile)
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null)
  const { mutateAsync: requestImport } = useMutation(
    consoleQuery.apps.imports.post.mutationOptions({ context: { silent: true } }),
  )
  const importMutation = useMutation({
    mutationFn: async (file: File) => {
      const body = {
        mode: 'yaml-content',
        yaml_content: await file.text(),
      } satisfies AppImportPayload
      return requestImport({ body })
    },
  })
  const confirmImportMutation = useMutation(
    consoleQuery.apps.imports.byImportId.confirm.post.mutationOptions({
      context: { silent: true },
    }),
  )
  const { handleCheckPluginDependencies } = usePluginDependencies()
  const { data: systemFeatures } = useSuspenseQuery(systemFeaturesQueryOptions())
  const { data: currentUserId } = useSuspenseQuery({
    ...userProfileQueryOptions(),
    select: (data) => data.profile.id,
  })
  const workspacePermissionKeys = useAtomValue(workspacePermissionKeysAtom)
  const { data: appQuota } = useQuery(
    consoleQuery.features.get.queryOptions({
      select: (data) => getCountQuota(data.apps),
    }),
  )
  const isAppQuotaUnavailable = appQuota === undefined
  const isAppsFull = appQuota !== undefined && isQuotaReached(appQuota)
  const isImporting = importMutation.isPending
  const isConfirming = confirmImportMutation.isPending

  const handleCompletedImport = async (response: Import) => {
    const appMode = getImportedAppMode(response.app_mode)

    if (appMode) trackCreateApp({ source: 'studio_upload', appMode })
    onSuccess?.()
    onClose()

    toast(
      t(($) => $[response.status === 'completed' ? 'newApp.appCreated' : 'newApp.caution'], {
        ns: 'app',
      }),
      {
        type: response.status === 'completed' ? 'success' : 'warning',
        description:
          response.status === 'completed-with-warnings' ? (
            <DSLImportWarningDescription
              warnings={response.warnings}
              fallback={t(($) => $['newApp.appCreateDSLWarning'], { ns: 'app' })}
            />
          ) : undefined,
      },
    )
    if (!response.app_id || !appMode) return

    // Dependency checks own their error feedback; a created app remains navigable.
    await handleCheckPluginDependencies(response.app_id)
    const redirectionTarget = await resolveImportedAppRedirectionTarget({
      id: response.app_id,
      mode: appMode,
      permission_keys: response.permission_keys,
    })
    getRedirection(redirectionTarget, push, {
      currentUserId,
      resourceMaintainer: currentUserId,
      workspacePermissionKeys,
      isRbacEnabled: systemFeatures.rbac_enabled,
    })
  }

  const handleImportResponse = async (response: Import) => {
    if (response.status === 'completed' || response.status === 'completed-with-warnings') {
      await handleCompletedImport(response)
      return
    }

    if (response.status === 'pending') {
      setPendingImport({
        id: response.id,
        importedVersion: response.imported_dsl_version ?? '',
        systemVersion: response.current_dsl_version ?? '',
      })
      return
    }

    toast.error(
      t(($) => $['newApp.appCreateFailed'], { ns: 'app' }),
      {
        description: response.error || undefined,
      },
    )
  }

  const handleSubmit = async () => {
    if (isAppQuotaUnavailable || isAppsFull || isImporting || !currentFile) return

    let response: Import
    try {
      response = await importMutation.mutateAsync(currentFile)
    } catch (error) {
      toast.error(
        t(($) => $['newApp.appCreateFailed'], { ns: 'app' }),
        {
          description: getErrorMessage(error),
        },
      )
      return
    }
    await handleImportResponse(response)
  }

  const handleConfirm = async () => {
    if (!pendingImport || isConfirming) return

    let response: Import
    try {
      response = await confirmImportMutation.mutateAsync({
        params: { import_id: pendingImport.id },
      })
    } catch (error) {
      toast.error(
        t(($) => $['newApp.appCreateFailed'], { ns: 'app' }),
        {
          description: getErrorMessage(error),
        },
      )
      return
    }

    if (response.status === 'completed' || response.status === 'completed-with-warnings') {
      setPendingImport(null)
      await handleCompletedImport(response)
      return
    }

    if (response.status === 'failed')
      toast.error(
        t(($) => $['newApp.appCreateFailed'], { ns: 'app' }),
        { description: response.error || undefined },
      )
  }

  const createDisabled = isAppQuotaUnavailable || isAppsFull || !currentFile

  return (
    <>
      <Dialog
        open={show}
        onOpenChange={(open) => {
          if (!open && !isImporting && !pendingImport) onClose()
        }}
      >
        <DialogContent
          initialFocus={browseButtonRef}
          render={
            <Form
              onFormSubmit={handleSubmit}
              onKeyDown={(event) => {
                if (
                  !show ||
                  createDisabled ||
                  isImporting ||
                  !!pendingImport ||
                  event.defaultPrevented ||
                  event.nativeEvent.isComposing ||
                  !(event.target instanceof Node) ||
                  !event.currentTarget.contains(event.target) ||
                  !matchesKeyboardEvent(event.nativeEvent, CREATE_FROM_DSL_HOTKEY)
                )
                  return
                event.preventDefault()
                event.stopPropagation()
                if (event.repeat) return
                event.currentTarget.requestSubmit()
              }}
            />
          }
        >
          <DialogHeader>
            <DialogTitle>{t(($) => $.importApp, { ns: 'app' })}</DialogTitle>
            <DialogHeaderCloseButton
              aria-label={t(($) => $['operation.close'], { ns: 'common' })}
              disabled={isImporting}
            />
          </DialogHeader>
          <DialogBody>
            <Uploader
              importType="app"
              browseButtonRef={browseButtonRef}
              className="mt-0"
              file={currentFile}
              updateFile={setCurrentFile}
              disabled={isImporting}
            />
          </DialogBody>
          {isAppsFull && (
            <div className="px-6">
              <AppsFull className="mt-0" loc="app-create-dsl" />
            </div>
          )}
          <DialogFooter>
            <Button disabled={isImporting} onClick={onClose}>
              {t(($) => $['newApp.Cancel'], { ns: 'app' })}
            </Button>
            <Button type="submit" disabled={createDisabled} loading={isImporting} variant="primary">
              <span>{t(($) => $['operation.create'], { ns: 'common' })}</span>
              <KbdGroup>
                {formatForDisplay(CREATE_FROM_DSL_HOTKEY, { parts: true }).map((key) => (
                  <Kbd key={key} color="white">
                    {key}
                  </Kbd>
                ))}
              </KbdGroup>
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {pendingImport && (
        <DSLConfirmModal
          versions={{
            importedVersion: pendingImport.importedVersion,
            systemVersion: pendingImport.systemVersion,
          }}
          onCancel={() => {
            if (!isConfirming) setPendingImport(null)
          }}
          onConfirm={handleConfirm}
          confirmLoading={isConfirming}
        />
      )}
    </>
  )
}

export default CreateFromDSLModal
