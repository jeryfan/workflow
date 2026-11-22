import type { FC } from 'react'
import { useTranslation } from 'react-i18next'

const Empty: FC = () => {
  const { t } = useTranslation(['workflowDebug'])

  return (
    <div className="flex h-full flex-col gap-3 rounded-xl bg-background-section p-8">
      <div className="flex h-10 w-10 items-center justify-center rounded-[10px] border-[0.5px] border-components-card-border bg-components-card-bg shadow-lg backdrop-blur-xs">
        <span
          aria-hidden
          className="i-custom-vender-solid-development-variable-02 size-5 text-text-accent"
        />
      </div>
      <div className="flex flex-col gap-1">
        <div className="system-sm-semibold text-text-secondary">
          {t(($) => $['debug.variableInspect.title'], { ns: 'workflowDebug' })}
        </div>
        <div className="system-xs-regular text-text-tertiary">
          {t(($) => $['debug.variableInspect.emptyTip'], { ns: 'workflowDebug' })}
        </div>
      </div>
    </div>
  )
}

export default Empty
