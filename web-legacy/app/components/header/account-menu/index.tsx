'use client'

import type { ReactElement } from 'react'
import { Avatar, AvatarFallback, AvatarImage } from '@langgenius/dify-ui/avatar'
import {
  Menu,
  MenuContent,
  MenuGroup,
  MenuItem,
  MenuTrigger,
} from '@langgenius/dify-ui/menu'
import { useSuspenseQuery } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import { useTranslation } from 'react-i18next'
import { userProfileQueryOptions } from '@/features/account-profile/client'
import { useLogout } from '@/service/use-common'
import { getAvatarInitial } from '@/utils/avatar'
import { MenuItemContent } from './menu-item-content'

type AccountMenuProps = {
  trigger: (props: { ariaLabel: string }) => ReactElement
}

const mainNavMenuPopupClassName = 'w-60 max-w-80 overflow-hidden p-0'

const subscribeHydrationState = () => () => {}
const getHydrationSnapshot = () => false
const getServerHydrationSnapshot = () => true

function MainNavMenuContent({ onLogout }: { onLogout: () => void }) {
  const { t } = useTranslation(['common', 'accountSettings'])
  const { data: userProfile } = useSuspenseQuery({
    ...userProfileQueryOptions(),
    select: (data) => data.profile,
  })

  return (
    <>
      <MenuGroup className="py-1">
        <div className="mx-1 flex items-center gap-3 rounded-xl bg-linear-to-b from-background-section-burn to-background-section p-3">
          <div className="flex min-w-0 grow flex-col gap-1">
            <div className="truncate body-md-medium text-text-primary" title={userProfile.name}>
              {userProfile.name}
            </div>
            <div className="truncate system-xs-regular text-text-tertiary" title={userProfile.email}>
              {userProfile.email}
            </div>
          </div>
          <Avatar size="lg" className="shrink-0" aria-hidden="true">
            <AvatarFallback>{getAvatarInitial(userProfile.name)}</AvatarFallback>
            <AvatarImage src={userProfile.avatar_url || undefined} alt="" />
          </Avatar>
        </div>
      </MenuGroup>
      <MenuGroup className="py-1">
        <MenuItem className="px-3 py-1" onClick={onLogout}>
          <MenuItemContent
            iconClassName="i-ri-shut-down-line"
            label={t(($) => $['userProfile.logout'], { ns: 'common' })}
          />
        </MenuItem>
      </MenuGroup>
    </>
  )
}

export default function AccountMenu({ trigger }: AccountMenuProps) {
  const isHydrating = useSyncExternalStore(
    subscribeHydrationState,
    getHydrationSnapshot,
    getServerHydrationSnapshot,
  )
  const { t } = useTranslation(['accountSettings'])
  const { mutate: logout } = useLogout()

  return (
    <div>
      <Menu>
        <MenuTrigger
          disabled={isHydrating}
          render={trigger({
            ariaLabel: t(($) => $['account.account'], { ns: 'accountSettings' }),
          })}
        />
        <MenuContent side="top" align="start" alignOffset={4} className={mainNavMenuPopupClassName}>
          <MainNavMenuContent onLogout={() => logout()} />
        </MenuContent>
      </Menu>
    </div>
  )
}
