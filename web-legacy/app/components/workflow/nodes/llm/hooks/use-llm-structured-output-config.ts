import type { RefObject } from 'react'
import type { LLMNodeType, StructuredOutput } from '../types'
import { useQuery } from '@tanstack/react-query'
import { produce } from 'immer'
import { useCallback, useState } from 'react'
import { consoleQuery } from '@/service/console'

type Params = {
  id: string
  model: LLMNodeType['model']
  inputRef: RefObject<LLMNodeType>
  setInputs: (inputs: LLMNodeType) => void
  deleteNodeInspectorVars: (nodeId: string) => void
}

const useLLMStructuredOutputConfig = ({
  id,
  model,
  inputRef,
  setInputs,
  deleteNodeInspectorVars,
}: Params) => {
  const { data: modelList = [] } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: response => response.data as unknown as {
        id: string
        model: string
        capabilities: string[]
      }[],
    }),
  )
  const currentModel = modelList.find(item => item.id === model.id)
    ?? modelList.find(item => item.model === model.name)
  const isModelSupportStructuredOutput = currentModel?.capabilities?.includes('structured-output')

  const [structuredOutputCollapsed, setStructuredOutputCollapsed] = useState(true)

  const handleStructureOutputEnableChange = useCallback(
    (enabled: boolean) => {
      const nextInputs = produce(inputRef.current, (draft) => {
        draft.structured_output_enabled = enabled
      })
      setInputs(nextInputs)
      if (enabled) setStructuredOutputCollapsed(false)
      deleteNodeInspectorVars(id)
    },
    [deleteNodeInspectorVars, id, inputRef, setInputs],
  )

  const handleStructureOutputChange = useCallback(
    (newOutput: StructuredOutput) => {
      const nextInputs = produce(inputRef.current, (draft) => {
        draft.structured_output = newOutput
      })
      setInputs(nextInputs)
      deleteNodeInspectorVars(id)
    },
    [deleteNodeInspectorVars, id, inputRef, setInputs],
  )

  const handleReasoningFormatChange = useCallback(
    (reasoningFormat: 'tagged' | 'separated') => {
      const nextInputs = produce(inputRef.current, (draft) => {
        draft.reasoning_format = reasoningFormat
      })
      setInputs(nextInputs)
    },
    [inputRef, setInputs],
  )

  return {
    isModelSupportStructuredOutput,
    structuredOutputCollapsed,
    setStructuredOutputCollapsed,
    handleStructureOutputEnableChange,
    handleStructureOutputChange,
    handleReasoningFormatChange,
  }
}

export default useLLMStructuredOutputConfig
