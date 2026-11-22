'use client'

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
} from '@langgenius/dify-ui/breadcrumb'
import { useTranslation } from 'react-i18next'
import { DetailSidebarToggleButton } from '@/app/components/detail-sidebar/toggle-button'
import Link from '@/next/link'

type AppDetailTopProps = {
  expand?: boolean
  onToggle?: () => void
}

export function AppDetailTop({ expand = true, onToggle }: AppDetailTopProps) {
  const { t } = useTranslation(['app', 'navigation'])

  if (!expand) {
    return (
      <div className="flex w-full items-center justify-center px-3 pt-2 pb-1">
        {onToggle && (
          <DetailSidebarToggleButton
            expand={expand}
            onToggle={onToggle}
            icon={
              <span aria-hidden className="i-custom-vender-line-arrows-sidebar-left-arrow size-4" />
            }
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex items-center py-2 ps-1 pe-2">
      <Breadcrumb aria-label={t(($) => $['menus.apps'], { ns: 'navigation' })} className="flex-1">
        <BreadcrumbList className="gap-px">
          <BreadcrumbItem className="shrink-0">
            <BreadcrumbLink
              render={<Link href="/" />}
              aria-label={t(($) => $['mainNav.home'], { ns: 'navigation' })}
              className="gap-0 rounded-lg py-2 ps-0.5 pe-1.5 hover:bg-background-default-hover"
            >
              <span aria-hidden className="i-ri-arrow-left-s-line size-4" />
              <span aria-hidden className="i-custom-vender-main-nav-app-home size-4" />
            </BreadcrumbLink>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      {onToggle && (
        <DetailSidebarToggleButton
          expand={expand}
          onToggle={onToggle}
          icon={
            <span aria-hidden className="i-custom-vender-line-arrows-sidebar-left-arrow size-4" />
          }
        />
      )}
    </div>
  )
}
