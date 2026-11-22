import type { QueryClient } from '@tanstack/react-query'
import type { AccountPermissionsResponse } from '@/features/accounts/client'

const accountPermissionsQueryKey = ['account', 'permissions']

const createAccountPermissionsFixture = (
  permissionKeys: readonly string[] = [],
): AccountPermissionsResponse => ({
  account: {
    role: 'owner',
    permission_keys: [...permissionKeys],
  },
  app: {
    default_permission_keys: [],
  },
})

export const seedWorkspacePermissionsQuery = (
  queryClient: QueryClient,
  permissionKeys: readonly string[] = [],
) => {
  const data = createAccountPermissionsFixture(permissionKeys)
  queryClient.setQueryData(accountPermissionsQueryKey, data)
  return data
}

export const ensureWorkspacePermissionsQuery = (
  queryClient: QueryClient,
  permissionKeys: readonly string[] = [],
) => {
  const existingPermissions = queryClient.getQueryData<AccountPermissionsResponse>(
    accountPermissionsQueryKey,
  )
  if (existingPermissions === undefined)
    return seedWorkspacePermissionsQuery(queryClient, permissionKeys)

  return existingPermissions
}
