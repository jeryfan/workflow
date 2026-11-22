'use client'

import { atom } from 'jotai'
import { atomWithQuery } from 'jotai-tanstack-query'
import { accountPermissionsQueryOptions } from '@/features/accounts/client'
import { emptyWorkspacePermissionKeys } from './app-context-normalizers'

const accountPermissionsQueryAtom = atomWithQuery(() => accountPermissionsQueryOptions())

export const workspacePermissionKeysAtom = atom((get) => {
  return (
    get(accountPermissionsQueryAtom).data?.account.permission_keys ?? emptyWorkspacePermissionKeys
  )
})

export const datasetDefaultPermissionKeysAtom = atom(() => [])

export const agentDefaultPermissionKeysAtom = atom(() => [])

export const workspacePermissionKeysLoadingAtom = atom((get) => {
  return get(accountPermissionsQueryAtom).isPending
})

export const workspacePermissionKeysErrorAtom = atom((get) => {
  return get(accountPermissionsQueryAtom).error
})
