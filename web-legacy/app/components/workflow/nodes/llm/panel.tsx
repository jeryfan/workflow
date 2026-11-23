import type { FC } from 'react'
import type { LLMModelOption, LLMNodeType } from './types'
import type { NodePanelProps } from '@/app/components/workflow/types'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectItemIndicator,
  SelectItemText,
  SelectTrigger,
} from '@langgenius/dify-ui/select'
import { useQuery } from '@tanstack/react-query'
import { useAtomValue } from 'jotai'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { editorResourceKindAtom } from '@/app/components/workflow/editor-resource/state'
import Field from '@/app/components/workflow/nodes/_base/components/field'
import Split from '@/app/components/workflow/nodes/_base/components/split'
import VarList from '@/app/components/workflow/nodes/_base/components/variable/var-list'
import { consoleQuery } from '@/service/console'
import ConfigVision from '../_base/components/config-vision'
import VarReferencePicker from '../_base/components/variable/var-reference-picker'
import ConfigPrompt from './components/config-prompt'
import PanelMemorySection from './components/panel-memory-section'
import PanelOutputSection from './components/panel-output-section'
import ReasoningFormatConfig from './components/reasoning-format-config'
import useConfig from './use-config'

const i18nPrefix = 'nodes.llm'

const Panel: FC<NodePanelProps<LLMNodeType>> = ({ id, data }) => {
  const { t } = useTranslation(['common', 'workflow', 'workflowModels'])
  const resourceKind = useAtomValue(editorResourceKindAtom)
  const {
    readOnly,
    inputs,
    model,
    isChatModel,
    isChatflow,
    isCompletionModel,
    shouldShowContextTip,
    isVisionModel,
    handleModelChanged,
    hasSetBlockStatus,
    handleContextVarChange,
    filterInputVar,
    filterVar,
    availableVars,
    availableNodesWithParent,
    isShowVars,
    handlePromptChange,
    handleAddEmptyVariable,
    handleAddVariable,
    handleVarListChange,
    handleVarNameChange,
    handleSyeQueryChange,
    handleMemoryChange,
    handleVisionResolutionEnabledChange,
    handleVisionResolutionChange,
    isModelSupportStructuredOutput,
    structuredOutputCollapsed,
    setStructuredOutputCollapsed,
    handleStructureOutputEnableChange,
    handleStructureOutputChange,
    filterJinja2InputVar,
    handleReasoningFormatChange,
  } = useConfig(id, data)

  const { data: availableModels = [] } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: response => response.data as unknown as LLMModelOption[],
    }),
  )
  const selectedModel = availableModels.find(item => item.id === model.id)
    ?? availableModels.find(item => item.model === model.name)
  const modelValue = selectedModel?.id ?? null
  const hasModelWarning = availableModels.length > 0 && !selectedModel
  const handleModelSelect = (value: string | null) => {
    const selected = availableModels.find(item => item.id === value)
    if (!selected) return
    handleModelChanged({
      id: selected.id,
      provider: selected.provider,
      modelId: selected.model,
      mode: selected.mode,
    })
  }

  React.useEffect(() => {
    if (readOnly || model.id || model.name) return
    const selected = availableModels.find(item => item.is_default) ?? availableModels[0]
    if (!selected) return
    handleModelChanged({
      id: selected.id,
      provider: selected.provider,
      modelId: selected.model,
      mode: selected.mode,
    })
  }, [availableModels, handleModelChanged, model.id, model.name, readOnly])

  return (
    <div className="mt-2">
      <div className="space-y-4 px-4 pb-4">
        <Field
          title={t(($) => $[`${i18nPrefix}.model`], { ns: 'workflowModels' })}
          required
          warningDot={hasModelWarning}
        >
          <Select value={modelValue} disabled={readOnly} onValueChange={handleModelSelect}>
            <SelectTrigger
              aria-label={t(($) => $[`${i18nPrefix}.model`], { ns: 'workflowModels' })}
              className="w-full"
            >
              {selectedModel?.name}
            </SelectTrigger>
            <SelectContent>
              {availableModels.map(item => (
                <SelectItem key={item.id} value={item.id}>
                  <SelectItemText>{item.name}</SelectItemText>
                  <SelectItemIndicator />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        {/* knowledge */}
        <Field
          title={t(($) => $[`${i18nPrefix}.context`], { ns: 'workflowModels' })}
          tooltip={t(($) => $[`${i18nPrefix}.contextTooltip`], { ns: 'workflowModels' })!}
        >
          <>
            <VarReferencePicker
              readonly={readOnly}
              nodeId={id}
              isShowNodeName
              value={inputs.context?.variable_selector || []}
              onChange={handleContextVarChange}
              filterVar={filterVar}
            />
            {shouldShowContextTip && (
              <div className="text-xs leading-4.5 font-normal text-[#DC6803]">
                {t(($) => $[`${i18nPrefix}.notSetContextInPromptTip`], { ns: 'workflowModels' })}
              </div>
            )}
          </>
        </Field>

        {/* Prompt */}
        {model.name && (
          <ConfigPrompt
            readOnly={readOnly}
            nodeId={id}
            filterVar={isShowVars ? filterJinja2InputVar : filterInputVar}
            isChatModel={isChatModel}
            isChatApp={isChatflow}
            isShowContext
            payload={inputs.prompt_template}
            onChange={handlePromptChange}
            hasSetBlockStatus={hasSetBlockStatus}
            varList={inputs.prompt_config?.jinja2_variables || []}
            handleAddVariable={handleAddVariable}
            modelConfig={model}
          />
        )}

        {isShowVars && (
          <Field
            title={t(($) => $['nodes.templateTransform.inputVars'], { ns: 'workflow' })}
            operations={
              !readOnly ? (
                <button
                  type="button"
                  aria-label={`${t(($) => $['operation.add'], { ns: 'common' })} ${t(($) => $['nodes.templateTransform.inputVars'], { ns: 'workflow' })}`}
                  className="cursor-pointer rounded-md border-none bg-transparent p-1 select-none hover:bg-state-base-hover focus-visible:ring-1 focus-visible:ring-components-input-border-active focus-visible:outline-hidden"
                  onClick={handleAddEmptyVariable}
                >
                  <span className="i-ri-add-line size-4 text-text-tertiary" aria-hidden="true" />
                </button>
              ) : undefined
            }
          >
            <VarList
              nodeId={id}
              readonly={readOnly}
              list={inputs.prompt_config?.jinja2_variables || []}
              onChange={handleVarListChange}
              onVarNameChange={handleVarNameChange}
              filterVar={filterJinja2InputVar}
              isSupportFileVar={false}
            />
          </Field>
        )}

        {isChatflow && (
          <>
            <Split />
            <PanelMemorySection
              readOnly={readOnly}
              isChatflow={isChatflow}
              isChatModel={isChatModel}
              isCompletionModel={isCompletionModel}
              inputs={inputs}
              hasSetBlockStatus={hasSetBlockStatus}
              availableVars={availableVars}
              availableNodesWithParent={availableNodesWithParent}
              resourceKind={resourceKind}
              handleSyeQueryChange={handleSyeQueryChange}
              handleMemoryChange={handleMemoryChange}
            />
          </>
        )}

        {/* Vision: GPT4-vision and so on */}
        <ConfigVision
          nodeId={id}
          readOnly={readOnly}
          isVisionModel={isVisionModel}
          enabled={inputs.vision?.enabled}
          onEnabledChange={handleVisionResolutionEnabledChange}
          config={inputs.vision?.configs}
          onConfigChange={handleVisionResolutionChange}
        />

        {/* Reasoning Format */}
        <ReasoningFormatConfig
          // Default to tagged for backward compatibility
          value={inputs.reasoning_format || 'tagged'}
          onChange={handleReasoningFormatChange}
          readonly={readOnly}
        />
      </div>
      <PanelOutputSection
        readOnly={readOnly}
        inputs={inputs}
        isModelSupportStructuredOutput={isModelSupportStructuredOutput}
        structuredOutputCollapsed={structuredOutputCollapsed}
        setStructuredOutputCollapsed={setStructuredOutputCollapsed}
        handleStructureOutputEnableChange={handleStructureOutputEnableChange}
        handleStructureOutputChange={handleStructureOutputChange}
      />
    </div>
  )
}

export default React.memo(Panel)
