import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { forwardRef } from 'react'
import { Link as RouterLink } from 'react-router'

type LinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> & {
  href: string | { pathname?: string; search?: string; hash?: string }
  replace?: boolean
  scroll?: boolean
  prefetch?: boolean
  onNavigate?: (event: MouseEvent<HTMLAnchorElement>) => void
}

const Link = forwardRef<HTMLAnchorElement, LinkProps>(function Link(
  { href, replace, scroll: _scroll, prefetch: _prefetch, onNavigate, onClick, ...props },
  ref,
) {
  const to = typeof href === 'string' ? href : `${href.pathname ?? ''}${href.search ?? ''}${href.hash ?? ''}`
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event)
    if (!event.defaultPrevented) onNavigate?.(event)
  }
  return <RouterLink ref={ref} to={to} replace={replace} onClick={handleClick} {...props} />
})

export default Link
