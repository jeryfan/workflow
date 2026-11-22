'use client'

import type { ReactNode } from 'react'
import type { MainNavProps } from './types'
import { cn } from '@langgenius/dify-ui/cn'
import { useSuspenseQuery } from '@tanstack/react-query'
import { DifyLogo } from '@/app/components/base/logo/dify-logo'
import { systemFeaturesQueryOptions } from '@/features/system-features/client'
import Link from '@/next/link'
import AccountSection from './components/account-section'

export function MainNavLogo() {
  const { data: branding } = useSuspenseQuery({
    ...systemFeaturesQueryOptions(),
    select: (data) => data.branding,
  })
  const appTitle = branding.enabled && branding.application_title ? branding.application_title : 'Dify'

  return (
    <Link
      href="/"
      className="flex h-8 shrink-0 items-center overflow-hidden focus-visible:ring-2 focus-visible:ring-state-accent-solid focus-visible:outline-hidden"
      aria-label={appTitle}
    >
      {branding.enabled && branding.workspace_logo ? (
        <img src={branding.workspace_logo} className="block h-5.5 w-auto object-contain" alt="" />
      ) : (
        <DifyLogo alt="" />
      )}
    </Link>
  )
}

export function MainNavPanel({ headerAction }: { headerAction: ReactNode }) {
  return (
    <div className="flex min-h-0 w-60 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between ps-4 pe-2 pt-3 pb-2">
        <MainNavLogo />
        {headerAction}
      </div>
    </div>
  )
}

export function MainNavFooter() {
  return (
    <div className="w-60">
      <div className="flex items-center justify-between bg-linear-to-b from-background-body-transparent to-background-body to-50% py-3 ps-3 pe-1 backdrop-blur-[2px]">
        <div className="flex min-w-0 items-center gap-1 overflow-hidden">
          <AccountSection />
        </div>
      </div>
    </div>
  )
}

export function MainNav({ className }: MainNavProps) {
  return (
    <aside
      className={cn(
        'relative flex h-full w-60 shrink-0 flex-col overflow-hidden bg-background-body transition-all',
        className,
      )}
    >
      <MainNavPanel headerAction={null} />
      <div className="isolate w-60 shrink-0">
        <MainNavFooter />
      </div>
    </aside>
  )
}
