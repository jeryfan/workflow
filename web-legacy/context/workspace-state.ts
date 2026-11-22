'use client'

import { atom } from 'jotai'
import { atomWithQuery } from 'jotai-tanstack-query'
import type { UserProfileWithMeta } from '@/features/account-profile/client'
import { userProfileQueryOptions } from '@/features/account-profile/client'
import { initialWorkspaceSummary } from './app-context-defaults'
import { getWorkspaceRoleFlags } from './app-context-normalizers'

const currentAccountQueryAtom = atomWithQuery(() => {
  const options = userProfileQueryOptions()
  return {
    ...options,
    select: ({ profile }: UserProfileWithMeta) => ({
      ...initialWorkspaceSummary,
      id: 'current',
      name: profile.name,
      role: profile.role,
    }),
  }
})

export const currentWorkspaceAtom = atom((get) => {
  return get(currentAccountQueryAtom).data ?? initialWorkspaceSummary
})

export const currentWorkspaceIdAtom = atom((get) => {
  return get(currentWorkspaceAtom).id
})

const workspaceRoleFlagsAtom = atom((get) => {
  return getWorkspaceRoleFlags(get(currentWorkspaceAtom))
})

export const isCurrentWorkspaceOwnerAtom = atom((get) => {
  return get(workspaceRoleFlagsAtom).isCurrentWorkspaceOwner
})

export const isCurrentWorkspaceManagerAtom = atom((get) => {
  return get(workspaceRoleFlagsAtom).isCurrentWorkspaceManager
})

export const isCurrentWorkspaceDatasetOperatorAtom = atom(() => false)

export const currentWorkspaceLoadingAtom = atom((get) => {
  return get(currentAccountQueryAtom).isPending
})
