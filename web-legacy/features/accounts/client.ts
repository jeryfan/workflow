import type { GetAccountProfileResponse } from '@dify/contracts/api/console/account/types.gen'
import { queryOptions } from '@tanstack/react-query'
import { get } from '@/service/base'

export type AccountRole = 'owner' | 'admin' | 'editor' | 'normal'
export type AccountProfile = GetAccountProfileResponse & { role: AccountRole }

export type AccountPermissionsResponse = {
  account: { role: AccountRole; permission_keys: string[] }
  app: { default_permission_keys: string[] }
}

export type AccountSummary = {
  id: string
  name: string
  email: string
  role: AccountRole
  avatar: string | null
  avatar_url: string | null
  status: string
  created_at: number
  last_login_at: number | null
}

export const accountPermissionsQueryOptions = () =>
  queryOptions({
    queryKey: ['account', 'permissions'],
    queryFn: () => get<AccountPermissionsResponse>('/account/permissions'),
  })

export const accountsQueryOptions = () =>
  queryOptions({
    queryKey: ['accounts'],
    queryFn: () => get<{ accounts: AccountSummary[] }>('/accounts'),
  })
