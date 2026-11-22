import type { ComponentType } from 'react'
import { lazy, Suspense } from 'react'

type Loader<P extends object> = () => Promise<ComponentType<P> | { default: ComponentType<P> }>
type DynamicOptions<P extends object> = {
  ssr?: boolean
  loading?: ComponentType<P>
  suspense?: boolean
}

export default function dynamic<P extends object = Record<string, never>>(
  loader: Loader<P>,
  options: DynamicOptions<P> = {},
) {
  const LazyComponent = lazy(async () => {
    const loaded = await loader()
    return 'default' in loaded ? loaded : { default: loaded }
  })

  function DynamicComponent(props: P) {
    const Loading = options.loading
    return (
      <Suspense fallback={Loading ? <Loading {...props} /> : null}>
        <LazyComponent {...props} />
      </Suspense>
    )
  }

  return DynamicComponent
}
