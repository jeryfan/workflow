import type { GetFeaturesResponse } from '@dify/contracts/api/console/features/types.gen'
import type {
  BatchGetContactOptionsResponse,
  CurrentWorkspaceSummaryResponse,
  ListContactOptionsResponse,
  ListContactsResponse,
} from '@dify/contracts/api/console/workspaces/types.gen'
import type { ReactElement } from 'react'
import type { AccountProfile } from '@/features/accounts/client'
import { zGetFeaturesResponse } from '@dify/contracts/api/console/features/zod.gen'
import { createTanstackQueryUtils } from '@orpc/tanstack-query'
import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import { queryClientAtom } from 'jotai-tanstack-query'
import { createTestQueryClient } from '@/test/query-client'

export const contactApi = {
  features: vi.fn<() => Promise<GetFeaturesResponse>>(),
  profile: vi.fn<() => Promise<AccountProfile>>(),
  summary: vi.fn<() => Promise<CurrentWorkspaceSummaryResponse>>(),
  contacts: vi.fn<() => Promise<ListContactsResponse>>(),
  options:
    vi.fn<
      (input: {
        query: { keyword?: string; page?: number; limit?: number }
      }) => Promise<ListContactOptionsResponse>
    >(),
  batch:
    vi.fn<
      (input: { query: { contact_ids: string[] } }) => Promise<BatchGetContactOptionsResponse>
    >(),
}
export const testConsoleQuery = createTanstackQueryUtils({
  account: { profile: { get: contactApi.profile } },
  features: { get: contactApi.features },
  workspaces: {
    current: {
      summary: { get: contactApi.summary },
      humanInput: {
        v2: {
          contacts: { get: contactApi.contacts },
          contactOptions: { get: contactApi.options, batch: { get: contactApi.batch } },
        },
      },
    },
  },
})
export const contact = {
  id: 'contact-evan',
  name: 'Evan Zhang',
  email: 'evan@example.com',
  type: 'workspace' as const,
  im_providers: [],
}
export const organizationContact = {
  id: 'contact-amanda',
  name: 'Amanda Lin',
  email: 'amanda@example.com',
  type: 'platform' as const,
  im_providers: [],
}
export function resetContactApi() {
  contactApi.profile.mockReset().mockResolvedValue({
    id: 'owner-1',
    name: 'Owner',
    email: 'owner@example.com',
    role: 'owner',
    avatar: null,
    avatar_url: null,
    is_password_set: true,
  })
  contactApi.features
    .mockReset()
    .mockResolvedValue({ ...zGetFeaturesResponse.parse({}), im_centrally_managed: true })
  contactApi.contacts.mockReset().mockResolvedValue({ data: [], total: 7, page: 1, limit: 1 })
  contactApi.options
    .mockReset()
    .mockResolvedValue({ data: [contact, organizationContact], total: 2, page: 1, limit: 20 })
  contactApi.batch.mockReset().mockImplementation(async ({ query }) => ({
    data: [contact, organizationContact].filter((contact) =>
      query.contact_ids.includes(contact.id),
    ),
  }))
}
export function renderContactOwner(ui: ReactElement, role: 'owner' | 'editor' = 'owner') {
  const client = createTestQueryClient()
  client.setQueryData(testConsoleQuery.account.profile.get.queryKey() as readonly unknown[], {
    profile: {
      id: 'owner-1',
      name: 'Owner',
      email: 'owner@example.com',
      role,
      avatar: null,
      avatar_url: null,
      is_password_set: true,
    },
    meta: { currentVersion: null, currentEnv: null },
  })
  const store = createStore()
  store.set(queryClientAtom, client)
  return {
    ...render(ui, {
      wrapper: ({ children }) => (
        <Provider store={store}>
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        </Provider>
      ),
    }),
    client,
  }
}
