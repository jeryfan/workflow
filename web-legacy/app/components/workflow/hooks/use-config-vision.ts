import type { ModelConfig, VisionSetting } from '@/app/components/workflow/types'
import { produce } from 'immer'
import { useAtomValue } from 'jotai'
import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { isChatflowAtom } from '@/app/components/workflow/editor-resource/state'
import { consoleQuery } from '@/service/console'
import { Resolution } from '@/types/app'

type Payload = {
  enabled: boolean
  configs?: VisionSetting
}

type Params = {
  payload: Payload
  onChange: (payload: Payload) => void
}
const useConfigVision = (
  model: ModelConfig,
  {
    payload = {
      enabled: false,
    },
    onChange,
  }: Params,
) => {
  const { data: models = [] } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: response => response.data as unknown as {
        id: string
        model: string
        capabilities: string[]
      }[],
    }),
  )
  const currModel = models.find(item => item.id === model.id)
    ?? models.find(item => item.model === model.name)

  const isChatflow = useAtomValue(isChatflowAtom)

  const getIsVisionModel = useCallback(() => {
    return !!currModel?.capabilities?.includes('vision')
  }, [currModel])

  const isVisionModel = getIsVisionModel()

  const handleVisionResolutionEnabledChange = useCallback(
    (enabled: boolean) => {
      const newPayload = produce(payload, (draft) => {
        draft.enabled = enabled
        if (enabled && isChatflow) {
          draft.configs = {
            detail: Resolution.high,
            variable_selector: ['sys', 'files'],
          }
        } else if (!enabled) {
          delete draft.configs
        }
      })
      onChange(newPayload)
    },
    [isChatflow, onChange, payload],
  )

  const handleVisionResolutionChange = useCallback(
    (config: VisionSetting) => {
      const newPayload = produce(payload, (draft) => {
        draft.configs = config
      })
      onChange(newPayload)
    },
    [onChange, payload],
  )

  const handleModelChanged = useCallback(() => {
    const isVisionModel = getIsVisionModel()
    if (!isVisionModel) {
      handleVisionResolutionEnabledChange(false)
      return
    }
    if (payload.enabled) {
      onChange({
        enabled: true,
        configs: {
          detail: Resolution.high,
          variable_selector: [],
        },
      })
    }
  }, [getIsVisionModel, handleVisionResolutionEnabledChange, onChange, payload.enabled])

  return {
    isVisionModel,
    handleVisionResolutionEnabledChange,
    handleVisionResolutionChange,
    handleModelChanged,
  }
}

export default useConfigVision
