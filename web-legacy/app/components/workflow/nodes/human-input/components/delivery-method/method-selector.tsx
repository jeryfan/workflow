'use client'

import type { FC } from 'react'
import type { DeliveryMethod } from '../../types'
import { cn } from '@langgenius/dify-ui/cn'
import { IconButton } from '@langgenius/dify-ui/icon-button'
import { Popover, PopoverContent, PopoverTrigger } from '@langgenius/dify-ui/popover'
import { memo, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { v4 as uuid4 } from 'uuid'
import useWorkflowNodes from '@/app/components/workflow/store/workflow/use-nodes'
import { isTriggerWorkflow } from '@/app/components/workflow/utils/workflow-entry'
import { DeliveryMethodType } from '../../types'

const i18nPrefix = 'nodes.humanInput'

type MethodSelectorProps = {
  data: DeliveryMethod[]
  onAdd: (method: DeliveryMethod) => void
}

const MethodSelector: FC<MethodSelectorProps> = ({ data, onAdd }) => {
  const { t } = useTranslation(['workflowHumanInput'])
  const nodes = useWorkflowNodes()

  const webAppDeliveryInfo = useMemo(() => {
    const isTriggerMode = isTriggerWorkflow(nodes)
    return {
      disabled: isTriggerMode || data.some((method) => method.type === DeliveryMethodType.WebApp),
      added: data.some((method) => method.type === DeliveryMethodType.WebApp),
      isTriggerMode,
    }
  }, [data, nodes])

  return (
    <Popover>
      <PopoverTrigger
        render={
          <IconButton
            aria-label={t(($) => $[`${i18nPrefix}.deliveryMethod.title`], {
              ns: 'workflowHumanInput',
            })}
            className="data-popup-open:bg-state-base-hover"
          >
            <span aria-hidden="true" className="i-ri-add-line size-4" />
          </IconButton>
        }
      />
      <PopoverContent
        align="end"
        sideOffset={4}
        className="border-none bg-transparent p-0 shadow-none backdrop-blur-none"
      >
        <div className="w-90 rounded-xl border-[0.5px] border-components-panel-border bg-components-panel-bg-blur shadow-lg backdrop-blur-xs">
          <div className="p-1">
            <div
              className={cn(
                'relative flex cursor-pointer items-center gap-1 rounded-lg p-1 ps-3 hover:bg-state-base-hover',
                webAppDeliveryInfo.disabled &&
                  'cursor-not-allowed bg-transparent hover:bg-transparent',
              )}
              onClick={() => {
                if (webAppDeliveryInfo.disabled) return
                onAdd({
                  id: uuid4(),
                  type: DeliveryMethodType.WebApp,
                  enabled: true,
                })
              }}
            >
              <div
                className={cn(
                  'rounded-sm border border-divider-regular bg-components-icon-bg-indigo-solid p-1',
                  webAppDeliveryInfo.disabled && 'opacity-50',
                )}
              >
                <span
                  aria-hidden
                  className="i-ri-robot-2-fill size-4 text-text-primary-on-surface"
                />
              </div>
              <div className={cn('p-1', webAppDeliveryInfo.disabled && 'opacity-50')}>
                <div className="mb-0.5 truncate system-sm-medium text-text-primary">
                  {t(($) => $[`${i18nPrefix}.deliveryMethod.types.webapp.title`], {
                    ns: 'workflowHumanInput',
                  })}
                </div>
                <div className="truncate system-xs-regular text-text-tertiary">
                  {t(($) => $[`${i18nPrefix}.deliveryMethod.types.webapp.description`], {
                    ns: 'workflowHumanInput',
                  })}
                </div>
              </div>
              {webAppDeliveryInfo.added && (
                <div className="absolute inset-e-3 top-3.25 system-xs-regular text-text-tertiary">
                  {t(($) => $[`${i18nPrefix}.deliveryMethod.added`], { ns: 'workflowHumanInput' })}
                </div>
              )}
              {webAppDeliveryInfo.isTriggerMode && !webAppDeliveryInfo.added && (
                <div className="absolute inset-e-3 top-3.25 system-xs-regular text-text-tertiary">
                  {t(($) => $[`${i18nPrefix}.deliveryMethod.notAvailableInTriggerMode`], {
                    ns: 'workflowHumanInput',
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
export default memo(MethodSelector)
