import type { AgentNodeType } from '../nodes/agent/types'
import type { DataSourceNodeType } from '../nodes/data-source/types'
import type { LLMModelOption, LLMNodeType } from '../nodes/llm/types'
import type { ToolNodeType } from '../nodes/tool/types'
import type { PluginTriggerNodeType } from '../nodes/trigger-plugin/types'
import type {
  CommonEdgeType,
  CommonNodeType,
  Edge,
  EnvironmentVariable,
  Node,
  ValueSelector,
} from '../types'
import type { Emoji } from '@/app/components/tools/types'
import type { EditorResource } from '@/app/components/workflow/editor-resource/resource'
import type { AgentToolPublishIssue } from '@/features/agent-v2/agent-detail/configure/tool-provider-catalog'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import isDeepEqual from 'fast-deep-equal'
import { useAtomValue } from 'jotai'
import { useCallback, useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useEdges, useStoreApi } from 'reactflow'
import { selectToolsWithProvider } from '@/app/components/tools/utils/query-select'
import {
  editorResourceAtom,
  editorResourceKindAtom,
} from '@/app/components/workflow/editor-resource/state'
import useNodes from '@/app/components/workflow/store/workflow/use-nodes'
import { toast } from '@/app/notifications'
import { ENABLE_HITL_IM, MAX_TREE_DEPTH } from '@/config'
import { useGetLanguage } from '@/context/i18n'
import { currentWorkspaceIdAtom } from '@/context/workspace-state'
import { agentSoulConfigToFormState } from '@/features/agent-v2/agent-composer/conversions'
import {
  createAgentToolProviderCatalog,
  getAgentToolPublishIssues,
  useAgentToolPresentation,
} from '@/features/agent-v2/agent-detail/configure/tool-provider-catalog'
import { consoleQuery } from '@/service/console'
import { useAllTriggerPlugins } from '@/service/use-triggers'
import { CUSTOM_NODE } from '../constants'
import { getNodeUsedVars, isSpecialVar } from '../nodes/_base/components/variable/utils'
import { hasValidInlineAgentBinding, isAgentV2NodeData } from '../nodes/agent-v2/types'
import AgentDefault from '../nodes/agent/default'
import { getContactRecipientQueryOptions } from '../nodes/human-input-v2/contact-queries'
import { isHumanInputV2NodeData } from '../nodes/human-input-v2/types'
import { resolveLLMNodeModel } from '../nodes/llm/utils'
import { useStore, useWorkflowStore } from '../store'
import { BlockEnum } from '../types'
import {
  getDataSourceCheckParams,
  getNodeCatalogType,
  getToolCheckParams,
  getValidTreeNodes,
} from '../utils'
import { isNodePluginMissing } from '../utils/plugin-install-check'
import { getTriggerCheckParams } from '../utils/trigger'
import { normalizeWorkflowOutputName } from '../utils/variable'
import useNodesAvailableVarList, {
  useGetNodesAvailableVarList,
} from './use-nodes-available-var-list'
import { useNodesMetaData } from './use-nodes-meta-data'
import { useGetToolIcon } from './use-tool-icon'

const EMPTY_MODELS: LLMModelOption[] = []

export type ChecklistItem = {
  id: string
  type: BlockEnum | string
  title: string
  toolIcon?: string | Emoji
  unConnected?: boolean
  errorMessages: string[]
  canNavigate: boolean
  disableGoTo?: boolean
  isPluginMissing?: boolean
  pluginUniqueIdentifier?: string
  openInlineAgentPanel?: boolean
}

type CheckValidExtraData = Record<string, unknown> | undefined
type NodeValidator = NonNullable<
  ReturnType<typeof useNodesMetaData>['nodesMap']
>[BlockEnum]['checkValid']

const EMPTY_ENVIRONMENT_VARIABLES: EnvironmentVariable[] = []

const getHumanInputContactIds = (nodes: Node[]) => {
  if (!ENABLE_HITL_IM) return []

  return [
    ...new Set(
      nodes.flatMap((node) =>
        node.type === CUSTOM_NODE && isHumanInputV2NodeData(node.data)
          ? node.data.recipients_spec.flatMap((recipient) =>
              recipient.type === 'contact' && recipient.contact_id.trim()
                ? [recipient.contact_id]
                : [],
            )
          : [],
      ),
    ),
  ].sort()
}

const withResourceKind = (
  moreDataForCheckValid: CheckValidExtraData,
  resourceKind: EditorResource['kind'],
) => {
  return {
    ...(moreDataForCheckValid ?? {}),
    resourceKind,
  }
}

const resolveNodeValidator = (
  data: CommonNodeType,
  nodesExtraData: ReturnType<typeof useNodesMetaData>['nodesMap'],
): NodeValidator | undefined => {
  const validator = nodesExtraData?.[getNodeCatalogType(data)]?.checkValid
  if (validator) return validator

  if (data.type === BlockEnum.Agent && !isAgentV2NodeData(data)) return AgentDefault.checkValid
}

const START_NODE_TYPES: BlockEnum[] = [
  BlockEnum.Start,
  BlockEnum.TriggerSchedule,
  BlockEnum.TriggerWebhook,
  BlockEnum.TriggerPlugin,
]

const getDuplicateEndOutputMessages = (
  nodes: Node[],
  t: ReturnType<typeof useTranslation>['t'],
) => {
  const variableOccurrences = new Map<string, string[]>()

  nodes.forEach((node) => {
    if (node.type !== CUSTOM_NODE || node.data.type !== BlockEnum.End) return

    const outputs = (node.data as { outputs?: Array<{ variable?: string }> }).outputs || []
    outputs.forEach((output) => {
      const variable = normalizeWorkflowOutputName(output.variable)
      if (!variable) return

      const occurrences = variableOccurrences.get(variable) || []
      occurrences.push(node.id)
      variableOccurrences.set(variable, occurrences)
    })
  })

  const nodeMessages = new Map<string, string[]>()
  variableOccurrences.forEach((nodeIds, variable) => {
    if (nodeIds.length <= 1) return

    Array.from(new Set(nodeIds)).forEach((nodeId) => {
      const messages = nodeMessages.get(nodeId) || []
      messages.push(t(($) => $['errorMsg.duplicateOutputVariable'], { ns: 'workflow', variable }))
      nodeMessages.set(nodeId, messages)
    })
  })

  return nodeMessages
}

export const useChecklist = (nodes: Node[], edges: Edge[]) => {
  const { t } = useTranslation(['agentV2', 'common', 'workflow', 'modelProvider'])
  const language = useGetLanguage()
  const workspaceId = useAtomValue(currentWorkspaceIdAtom)
  const contactIds = useMemo(() => getHumanInputContactIds(nodes), [nodes])
  const contactQuery = useQuery(getContactRecipientQueryOptions(workspaceId, contactIds))
  const contactLookup = useMemo(
    () => ({
      resolvedContactIds: contactQuery.isSuccess
        ? new Set(contactQuery.data.data.map((contact) => contact.id))
        : undefined,
      contactLookupFailed: contactQuery.isError,
    }),
    [contactQuery.data, contactQuery.isError, contactQuery.isSuccess],
  )
  const { nodesMap: nodesExtraData } = useNodesMetaData()
  const { data: buildInTools } = useQuery(
    consoleQuery.workspaces.current.tools.builtin.get.queryOptions({
      select: selectToolsWithProvider,
    }),
  )
  const { data: customTools } = useQuery(
    consoleQuery.workspaces.current.tools.api.get.queryOptions({ select: selectToolsWithProvider }),
  )
  const { data: workflowTools } = useQuery(
    consoleQuery.workspaces.current.tools.workflow.get.queryOptions({
      select: selectToolsWithProvider,
    }),
  )
  const { data: mcpTools } = useQuery(
    consoleQuery.workspaces.current.tools.mcp.get.queryOptions({ select: selectToolsWithProvider }),
  )
  const inlineAgentToolProviderCatalog = useMemo(
    () => createAgentToolProviderCatalog({ buildInTools, customTools, mcpTools, workflowTools }),
    [buildInTools, customTools, mcpTools, workflowTools],
  )
  const dataSourceList = useStore((s) => s.dataSourceList)
  const environmentVariables =
    useStore((s) => s.environmentVariables) ?? EMPTY_ENVIRONMENT_VARIABLES
  const { data: strategyProviders } = useQuery(
    consoleQuery.workspaces.current.agentProviders.get.queryOptions(),
  )
  const { data: triggerPlugins } = useAllTriggerPlugins()
  const getToolIcon = useGetToolIcon()
  const resourceKind = useAtomValue(editorResourceKindAtom)
  const shouldCheckStartNode = resourceKind === 'app'
  const { data: models = EMPTY_MODELS } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: response => response.data as unknown as LLMModelOption[],
    }),
  )
  const workflowStore = useWorkflowStore()
  const resource = useAtomValue(editorResourceAtom)

  const map = useNodesAvailableVarList(nodes)
  const inlineAgentNodes = useMemo(
    () =>
      nodes.filter(
        (node) =>
          node.type === CUSTOM_NODE &&
          isAgentV2NodeData(node.data) &&
          hasValidInlineAgentBinding(node.data),
      ),
    [nodes],
  )
  const inlineAgentConfigurationIssues = useQueries({
    queries:
      resource.kind !== 'app' && resource.kind !== 'snippet'
        ? []
        : inlineAgentNodes.map((node) =>
            resource.kind === 'snippet'
              ? consoleQuery.snippets.bySnippetId.workflows.draft.nodes.byNodeId.agentComposer.get.queryOptions(
                  {
                    input: {
                      params: {
                        snippet_id: resource.snippetId,
                        node_id: node.id,
                      },
                    },
                  },
                )
              : consoleQuery.apps.byAppId.workflows.draft.nodes.byNodeId.agentComposer.get.queryOptions(
                  {
                    input: {
                      params: {
                        app_id: resource.appId,
                        node_id: node.id,
                      },
                    },
                  },
                ),
          ),
    combine: (results) => {
      const issuesByNodeId: Record<
        string,
        {
          hasMissingModel: boolean
          hasMissingFiles: boolean
          hasMissingSkills: boolean
          toolIssues: AgentToolPublishIssue[]
        }
      > = {}

      results.forEach((result, index) => {
        const nodeId = inlineAgentNodes[index]?.id
        const agentSoul = result.data?.agent_soul
        if (!nodeId || !agentSoul) return

        const agentSoulFormState = agentSoulConfigToFormState(agentSoul)
        const hasMissingModel = !agentSoulFormState.model
        const hasMissingFiles = agentSoul.config_files?.some((file) => file.is_missing === true)
        const hasMissingSkills = agentSoul.config_skills?.some((skill) => skill.is_missing === true)
        const toolIssues = getAgentToolPublishIssues(
          agentSoulFormState.tools,
          inlineAgentToolProviderCatalog,
        )
        if (!hasMissingModel && !hasMissingFiles && !hasMissingSkills && toolIssues.length === 0)
          return

        issuesByNodeId[nodeId] = {
          hasMissingModel,
          hasMissingFiles: !!hasMissingFiles,
          hasMissingSkills: !!hasMissingSkills,
          toolIssues,
        }
      })

      return issuesByNodeId
    },
  })
  const inlineAgentIssueTools = useMemo(
    () =>
      Object.values(inlineAgentConfigurationIssues).flatMap((issues) =>
        issues.toolIssues.map((issue) => issue.tool),
      ),
    [inlineAgentConfigurationIssues],
  )
  const inlineAgentToolPresentation = useAgentToolPresentation(
    inlineAgentIssueTools,
    inlineAgentToolProviderCatalog,
  )
  const needWarningNodes = useMemo<ChecklistItem[]>(() => {
    const list: ChecklistItem[] = []
    const filteredNodes = nodes.filter((node) => node.type === CUSTOM_NODE)
    const duplicateEndOutputMessages = getDuplicateEndOutputMessages(filteredNodes, t)
    const { validNodes } = getValidTreeNodes(filteredNodes, edges)

    for (let i = 0; i < filteredNodes.length; i++) {
      const node = filteredNodes[i]
      let moreDataForCheckValid: CheckValidExtraData
      let usedVars: ValueSelector[] = []

      if (node!.data.type === BlockEnum.Tool)
        moreDataForCheckValid = getToolCheckParams(
          node!.data as ToolNodeType,
          buildInTools || [],
          customTools || [],
          workflowTools || [],
          language,
        )

      if (node!.data.type === BlockEnum.DataSource)
        moreDataForCheckValid = getDataSourceCheckParams(
          node!.data as DataSourceNodeType,
          dataSourceList || [],
          language,
        )

      if (node!.data.type === BlockEnum.TriggerPlugin)
        moreDataForCheckValid = getTriggerCheckParams(
          node!.data as PluginTriggerNodeType,
          triggerPlugins,
          language,
        )

      const toolIcon = getToolIcon(node!.data)
      if (node!.data.type === BlockEnum.Agent && !isAgentV2NodeData(node!.data)) {
        const data = node!.data as AgentNodeType
        const isReadyForCheckValid = !!strategyProviders
        const provider = strategyProviders?.find(
          (provider) => provider.declaration.identity.name === data.agent_strategy_provider_name,
        )
        const strategy = provider?.declaration.strategies?.find(
          (s) => s.identity.name === data.agent_strategy_name,
        )
        moreDataForCheckValid = {
          provider,
          strategy,
          language,
          isReadyForCheckValid,
        }
      } else {
        usedVars = getNodeUsedVars(node!, { forExecution: true }).filter((v) => v.length > 0)
      }

      if (node!.data.type === BlockEnum.LLM) {
        moreDataForCheckValid = {
          ...(moreDataForCheckValid ?? {}),
          environmentVariables,
        }
      }

      if (isHumanInputV2NodeData(node!.data)) moreDataForCheckValid = contactLookup

      if (node!.type === CUSTOM_NODE) {
        const validator = resolveNodeValidator(node!.data, nodesExtraData)
        const isPluginMissing = isNodePluginMissing(node!.data, {
          builtInTools: buildInTools,
          customTools,
          workflowTools,
          mcpTools,
          triggerPlugins,
          dataSourceList,
        })

        const errorMessages: string[] = []
        const inlineAgentIssues = inlineAgentConfigurationIssues[node!.id]

        if (isPluginMissing) {
          errorMessages.push(t(($) => $['nodes.common.pluginNotInstalled'], { ns: 'workflow' }))
        } else {
          if (node!.data.type === BlockEnum.LLM) {
            const llmNodeData = node!.data as LLMNodeType
            const resolvedModel = resolveLLMNodeModel(
              llmNodeData.model,
              llmNodeData.model_selector,
              environmentVariables,
            )
            const hasConfiguredModel = resolvedModel && (
              resolvedModel.id
                ? models.some(item => item.id === resolvedModel.id)
                : models.some(item => item.model === resolvedModel.name)
            )
            if (resolvedModel?.provider && models.length > 0 && !hasConfiguredModel)
              errorMessages.push(t(($) => $['errorMsg.configureModel'], { ns: 'workflow' }))
          }

          if (validator) {
            const validationError = validator(
              node!.data,
              t,
              withResourceKind(moreDataForCheckValid, resourceKind),
            ).errorMessage
            if (validationError) errorMessages.push(validationError)
          }

          if (inlineAgentIssues?.hasMissingModel)
            errorMessages.push(t(($) => $['nodes.agent.modelNotSelected'], { ns: 'workflow' }))
          if (inlineAgentIssues?.hasMissingFiles)
            errorMessages.push(
              t(($) => $['agentDetail.configure.files.missing'], { ns: 'agentV2' }),
            )
          if (inlineAgentIssues?.hasMissingSkills)
            errorMessages.push(
              t(($) => $['agentDetail.configure.skills.missing'], { ns: 'agentV2' }),
            )
          for (const toolIssue of inlineAgentIssues?.toolIssues ?? []) {
            const toolName =
              inlineAgentToolPresentation.toolDisplayNameById.get(toolIssue.tool.id) ??
              toolIssue.tool.name
            errorMessages.push(
              toolIssue.type === 'uninstalled'
                ? t(($) => $['nodes.agent.toolNotInstallTooltip'], {
                    ns: 'workflow',
                    tool: toolName,
                  })
                : t(($) => $['nodes.agent.toolNotAuthorizedTooltip'], {
                    ns: 'workflow',
                    tool: toolName,
                  }),
            )
          }

          const availableVars = map[node!.id]!.availableVars
          let hasInvalidVar = false
          for (const variable of usedVars) {
            if (hasInvalidVar) break
            if (isSpecialVar(variable[0]!)) continue
            const usedNode = availableVars.find((v) => v.nodeId === variable?.[0])
            if (!usedNode || !usedNode.vars.some((v) => v.variable === variable?.[1]))
              hasInvalidVar = true
          }
          if (hasInvalidVar)
            errorMessages.push(t(($) => $['errorMsg.invalidVariable'], { ns: 'workflow' }))

          errorMessages.push(...(duplicateEndOutputMessages.get(node!.id) || []))
        }

        const isStartNodeMeta =
          nodesExtraData?.[node!.data.type as BlockEnum]?.metaData.isStart ?? false
        const isStartPlaceholderNode = node!.data.type === BlockEnum.StartPlaceholder
        const canSkipConnectionCheck = shouldCheckStartNode
          ? isStartNodeMeta || isStartPlaceholderNode
          : true

        const isUnconnected = !validNodes.some((n) => n.id === node!.id)
        const shouldShowError =
          errorMessages.length > 0 || (isUnconnected && !canSkipConnectionCheck)

        if (shouldShowError) {
          list.push({
            id: node!.id,
            type: node!.data.type,
            title: node!.data.title,
            toolIcon,
            unConnected: isUnconnected && !canSkipConnectionCheck,
            errorMessages,
            canNavigate: !isPluginMissing,
            disableGoTo: isPluginMissing,
            isPluginMissing,
            pluginUniqueIdentifier: isPluginMissing
              ? (node!.data as { plugin_unique_identifier?: string }).plugin_unique_identifier
              : undefined,
            ...(inlineAgentIssues ? { openInlineAgentPanel: true } : {}),
          })
        }
      }
    }

    // Check for start nodes (including triggers)
    if (shouldCheckStartNode) {
      const startNodesFiltered = nodes.filter((node) => START_NODE_TYPES.includes(node.data.type))
      const hasStartPlaceholderNode = nodes.some(
        (node) => node.data.type === BlockEnum.StartPlaceholder,
      )
      if (startNodesFiltered.length === 0 && !hasStartPlaceholderNode) {
        list.push({
          id: 'start-node-required',
          type: BlockEnum.Start,
          title: t(($) => $['panel.startNode'], { ns: 'workflow' }),
          errorMessages: [t(($) => $['common.needStartNode'], { ns: 'workflow' })],
          canNavigate: false,
        })
      }
    }

    const isRequiredNodesType = Object.entries(nodesExtraData!)
      .filter(([, node]) => node.metaData.isRequired)
      .map(([type]) => type as BlockEnum)

    isRequiredNodesType.forEach((type) => {
      if (!filteredNodes.some((node) => node.data.type === type)) {
        list.push({
          id: `${type}-need-added`,
          type,
          title: t(($) => $[`blocks.${type}`], {
            ns: 'workflow',
          }),
          errorMessages: [
            t(($) => $['common.needAdd'], {
              ns: 'workflow',
              node: t(($) => $[`blocks.${type}`], {
                ns: 'workflow',
              }),
            }),
          ],
          canNavigate: false,
        })
      }
    })

    return list
  }, [
    nodes,
    edges,
    shouldCheckStartNode,
    nodesExtraData,
    buildInTools,
    customTools,
    workflowTools,
    mcpTools,
    language,
    dataSourceList,
    environmentVariables,
    triggerPlugins,
    getToolIcon,
    strategyProviders,
    t,
    map,
    models,
    inlineAgentConfigurationIssues,
    inlineAgentToolPresentation.toolDisplayNameById,
    resourceKind,
    contactLookup,
  ])

  useEffect(() => {
    const currentChecklistItems = workflowStore.getState().checklistItems
    if (isDeepEqual(currentChecklistItems, needWarningNodes)) return

    workflowStore.setState({ checklistItems: needWarningNodes })
  }, [needWarningNodes, workflowStore])

  return needWarningNodes
}

export const useChecklistBeforePublish = () => {
  const { t } = useTranslation(['common', 'workflow', 'modelProvider'])
  const language = useGetLanguage()
  const queryClient = useQueryClient()
  const workspaceId = useAtomValue(currentWorkspaceIdAtom)
  const store = useStoreApi()
  const { nodesMap: nodesExtraData } = useNodesMetaData()
  const { data: strategyProviders } = useQuery(
    consoleQuery.workspaces.current.agentProviders.get.queryOptions(),
  )
  const { data: models = EMPTY_MODELS } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: response => response.data as unknown as LLMModelOption[],
    }),
  )
  const workflowStore = useWorkflowStore()
  const { getNodesAvailableVarList } = useGetNodesAvailableVarList()
  const { data: buildInTools } = useQuery(
    consoleQuery.workspaces.current.tools.builtin.get.queryOptions({
      select: selectToolsWithProvider,
    }),
  )
  const { data: customTools } = useQuery(
    consoleQuery.workspaces.current.tools.api.get.queryOptions({ select: selectToolsWithProvider }),
  )
  const { data: workflowTools } = useQuery(
    consoleQuery.workspaces.current.tools.workflow.get.queryOptions({
      select: selectToolsWithProvider,
    }),
  )
  const resourceKind = useAtomValue(editorResourceKindAtom)
  const shouldCheckStartNode = resourceKind === 'app'

  const handleCheckBeforePublish = useCallback(async () => {
    const { getNodes, edges } = store.getState()
    const { dataSourceList, environmentVariables = [] } = workflowStore.getState()
    const nodes = getNodes()
    const filteredNodes = nodes.filter((node) => node.type === CUSTOM_NODE)
    const duplicateEndOutputMessages = getDuplicateEndOutputMessages(filteredNodes, t)
    const { validNodes, maxDepth } = getValidTreeNodes(filteredNodes, edges)

    if (maxDepth > MAX_TREE_DEPTH) {
      toast.error(t(($) => $['common.maxTreeDepth'], { ns: 'workflow', depth: MAX_TREE_DEPTH }))
      return false
    }

    const fetchLatestContactLookup = async () => {
      const contactIds = getHumanInputContactIds(filteredNodes)
      if (!contactIds.length) return { resolvedContactIds: new Set<string>() }
      if (!workspaceId) return { contactLookupFailed: true }

      try {
        const response = await queryClient.query(
          getContactRecipientQueryOptions(workspaceId, contactIds),
        )
        return { resolvedContactIds: new Set(response.data.map((contact) => contact.id)) }
      } catch {
        return { contactLookupFailed: true }
      }
    }

    const contactLookup = await fetchLatestContactLookup()

    const map = getNodesAvailableVarList(nodes)
    for (let i = 0; i < filteredNodes.length; i++) {
      const node = filteredNodes[i]
      let moreDataForCheckValid: CheckValidExtraData
      let usedVars: ValueSelector[] = []
      if (node!.data.type === BlockEnum.Tool)
        moreDataForCheckValid = getToolCheckParams(
          node!.data as ToolNodeType,
          buildInTools || [],
          customTools || [],
          workflowTools || [],
          language,
        )

      if (node!.data.type === BlockEnum.DataSource)
        moreDataForCheckValid = getDataSourceCheckParams(
          node!.data as DataSourceNodeType,
          dataSourceList || [],
          language,
        )

      if (node!.data.type === BlockEnum.Agent && !isAgentV2NodeData(node!.data)) {
        const data = node!.data as AgentNodeType
        const isReadyForCheckValid = !!strategyProviders
        const provider = strategyProviders?.find(
          (provider) => provider.declaration.identity.name === data.agent_strategy_provider_name,
        )
        const strategy = provider?.declaration.strategies?.find(
          (s) => s.identity.name === data.agent_strategy_name,
        )
        moreDataForCheckValid = {
          provider,
          strategy,
          language,
          isReadyForCheckValid,
        }
      } else {
        usedVars = getNodeUsedVars(node!, { forExecution: true }).filter((v) => v.length > 0)
      }

      if (node!.data.type === BlockEnum.LLM) {
        moreDataForCheckValid = {
          ...(moreDataForCheckValid ?? {}),
          environmentVariables,
        }
      }

      if (node!.data.type === BlockEnum.LLM) {
        const llmNodeData = node!.data as LLMNodeType
        const resolvedModel = resolveLLMNodeModel(
          llmNodeData.model,
          llmNodeData.model_selector,
          environmentVariables,
        )
        const hasConfiguredModel = resolvedModel && (
          resolvedModel.id
            ? models.some(item => item.id === resolvedModel.id)
            : models.some(item => item.model === resolvedModel.name)
        )
        if (resolvedModel?.provider && models.length > 0 && !hasConfiguredModel) {
          toast.error(
            `[${node!.data.title}] ${t(($) => $['errorMsg.configureModel'], { ns: 'workflow' })}`,
          )
          return false
        }
      }

      if (isHumanInputV2NodeData(node!.data)) moreDataForCheckValid = contactLookup

      const validator = resolveNodeValidator(node!.data, nodesExtraData)
      if (validator) {
        const { errorMessage } = validator(
          node!.data,
          t,
          withResourceKind(moreDataForCheckValid, resourceKind),
        )

        if (errorMessage) {
          toast.error(`[${node!.data.title}] ${errorMessage}`)
          return false
        }
      }

      const duplicateOutputMessages = duplicateEndOutputMessages.get(node!.id) || []
      if (duplicateOutputMessages.length > 0) {
        toast.error(`[${node!.data.title}] ${duplicateOutputMessages[0]}`)
        return false
      }

      const availableVars = map[node!.id]!.availableVars

      for (const variable of usedVars) {
        const isSpecialVars = isSpecialVar(variable[0]!)
        if (!isSpecialVars) {
          const usedNode = availableVars.find((v) => v.nodeId === variable?.[0])
          if (usedNode) {
            const usedVar = usedNode.vars.find((v) => v.variable === variable?.[1])
            if (!usedVar) {
              toast.error(
                `[${node!.data.title}] ${t(($) => $['errorMsg.invalidVariable'], { ns: 'workflow' })}`,
              )
              return false
            }
          } else {
            toast.error(
              `[${node!.data.title}] ${t(($) => $['errorMsg.invalidVariable'], { ns: 'workflow' })}`,
            )
            return false
          }
        }
      }

      const isStartNodeMeta =
        nodesExtraData?.[node!.data.type as BlockEnum]?.metaData.isStart ?? false
      const canSkipConnectionCheck = shouldCheckStartNode ? isStartNodeMeta : true
      const isUnconnected = !validNodes.some((n) => n.id === node!.id)

      if (isUnconnected && !canSkipConnectionCheck) {
        toast.error(
          `[${node!.data.title}] ${t(($) => $['common.needConnectTip'], { ns: 'workflow' })}`,
        )
        return false
      }
    }

    if (shouldCheckStartNode) {
      const startNodesFiltered = nodes.filter((node) =>
        START_NODE_TYPES.includes(node.data.type as BlockEnum),
      )
      if (startNodesFiltered.length === 0) {
        toast.error(t(($) => $['common.needStartNode'], { ns: 'workflow' }))
        return false
      }
    }

    const isRequiredNodesType = Object.entries(nodesExtraData!)
      .filter(([, node]) => node.metaData.isRequired)
      .map(([type]) => type as BlockEnum)

    for (const type of isRequiredNodesType) {
      if (!filteredNodes.some((node) => node.data.type === type)) {
        toast.error(
          t(($) => $['common.needAdd'], {
            ns: 'workflow',
            node: t(($) => $[`blocks.${type}`], {
              ns: 'workflow',
            }),
          }),
        )
        return false
      }
    }

    return true
  }, [
    store,
    workflowStore,
    getNodesAvailableVarList,
    shouldCheckStartNode,
    nodesExtraData,
    t,
    buildInTools,
    customTools,
    workflowTools,
    language,
    queryClient,
    strategyProviders,
    models,
    resourceKind,
    workspaceId,
  ])

  return {
    handleCheckBeforePublish,
  }
}

export const useWorkflowRunValidation = () => {
  const { t } = useTranslation(['workflow'] as const)
  const nodes = useNodes()
  const edges = useEdges<CommonEdgeType>()
  const needWarningNodes = useChecklist(nodes, edges)

  const validateBeforeRun = useCallback(() => {
    if (needWarningNodes.length > 0) {
      toast.error(t(($) => $['panel.checklistTip'], { ns: 'workflow' }))
      return false
    }
    return true
  }, [needWarningNodes, t])

  return {
    validateBeforeRun,
    hasValidationErrors: needWarningNodes.length > 0,
    warningNodes: needWarningNodes,
  }
}
