import type { FC } from 'react'
import type { LLMNodeType } from './types'
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

  const { data: modelCatalog = [] } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: (response) => response.data,
    }),
  )
  const availableModels = modelCatalog.flatMap((providerItem) =>
    providerItem.models.map((modelItem) => ({ provider: providerItem.provider, model: modelItem })),
  )
  const modelValue = model.provider && model.name ? `${model.provider}:${model.name}` : null
  const selectedModel = availableModels.find(
    (item) => `${item.provider}:${item.model.model}` === modelValue,
  )
  const hasModelWarning = !selectedModel
  const handleModelSelect = (value: string | null) => {
    const selected = availableModels.find((item) => `${item.provider}:${item.model.model}` === value)
    if (!selected) return
    const mode = selected.model.model_properties.mode
    handleModelChanged({
      provider: selected.provider,
      modelId: selected.model.model,
      mode: typeof mode === 'string' ? mode : 'chat',
    })
  }

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
              {selectedModel?.model.label.en_US || selectedModel?.model.model}
            </SelectTrigger>
            <SelectContent>
              {availableModels.map((item) => {
                const value = `${item.provider}:${item.model.model}`
                return (
                  <SelectItem key={value} value={value}>
                    <SelectItemText>{item.model.label.en_US || item.model.model}</SelectItemText>
                    <SelectItemIndicator />
                  </SelectItem>
                )
              })}
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
