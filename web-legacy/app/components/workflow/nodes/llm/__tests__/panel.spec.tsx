import type { ReactFlowInstance } from 'reactflow'
import type { LLMNodeType } from '../types'
import type { EditorResource } from '@/app/components/workflow/editor-resource/resource'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useNodes, useReactFlow } from 'reactflow'
import { createNode } from '@/app/components/workflow/__tests__/fixtures'
import { renderWorkflowFlowComponent } from '@/app/components/workflow/__tests__/workflow-test-env'
import { BlockEnum } from '@/app/components/workflow/types'
import { EventEmitterContextProvider } from '@/context/event-emitter-provider'
import { consoleQuery } from '@/service/console'
import {
  createCatalogModelFixture,
  createDefaultModelFixture,
  createModelCatalogFixture,
  seedModelProviderQueries,
} from '@/test/console/model-provider'
import { createConsoleQueryWrapper } from '@/test/console/query-data'
import { seedToolProviders } from '@/test/console/tool-providers'
import { createNuqsTestRenderer } from '@/test/nuqs-testing'
import Panel from '../panel'

const createData = (overrides: Partial<LLMNodeType> = {}): LLMNodeType => ({
  type: BlockEnum.LLM,
  title: 'LLM',
  desc: '',
  model: {
    provider: 'openai',
    name: 'gpt-4o',
    mode: 'chat',
    completion_params: { temperature: 0.7 },
  },
  prompt_template: [],
  context: { enabled: false, variable_selector: [] },
  vision: { enabled: false },
  ...overrides,
})

const renderPanel = ({
  data = createData(),
  resource = { kind: 'app', collaborationEnabled: false, appId: 'test-app', mode: 'workflow' },
  readOnly = false,
}: {
  data?: LLMNodeType
  resource?: EditorResource
  readOnly?: boolean
} = {}) => {
  const { queryClient } = createConsoleQueryWrapper()
  seedModelProviderQueries(queryClient, [
    createModelCatalogFixture({
      models: [
        createCatalogModelFixture({
          model: 'gpt-4o',
          label: { en_US: 'GPT-4o' },
          model_properties: { mode: 'chat' },
        }),
      ],
    }),
    createModelCatalogFixture({
      provider: 'anthropic',
      label: { en_US: 'Anthropic' },
      models: [
        createCatalogModelFixture({
          model: 'claude-3-5-sonnet',
          label: { en_US: 'Claude 3.5 Sonnet' },
          model_properties: { mode: 'chat' },
        }),
      ],
    }),
  ])
  queryClient.setQueryData(
    consoleQuery.workspaces.current.defaultModel.get.queryKey({
      input: { query: { model_type: 'llm' } },
    }),
    {
      data: createDefaultModelFixture({
        model: 'gpt-4o',
        provider: { provider: 'openai' },
      }),
    },
  )
  queryClient.setQueryData(consoleQuery.spec.schemaDefinitions.get.queryKey(), [])
  seedToolProviders(queryClient, {
    builtInTools: [],
    customTools: [],
    workflowTools: [],
    mcpTools: [],
  })
  let flow: ReactFlowInstance<LLMNodeType>
  function NodePanel() {
    flow = useReactFlow<LLMNodeType>()
    const node = useNodes<LLMNodeType>().find((node) => node.id === 'llm-node')
    if (!node) return null
    return (
      <Panel
        id={node.id}
        data={node.data}
        panelProps={{
          getInputVars: () => [],
          toVarInputs: () => [],
          runInputData: {},
          runInputDataRef: { current: {} },
          setRunInputData: () => {},
          runResult: null,
        }}
      />
    )
  }
  const render = createNuqsTestRenderer((ui) =>
    renderWorkflowFlowComponent(<EventEmitterContextProvider>{ui}</EventEmitterContextProvider>, {
      queryClient,
      resource,
      initialStoreState: { canvasReadOnly: readOnly },
      nodes: [createNode({ id: 'llm-node', data })],
    }),
  )
  return { ...render(<NodePanel />), getNodeData: () => flow.getNode('llm-node')!.data }
}

describe('LLM Panel', () => {
  it('selects a configured model without exposing provider or parameter management', async () => {
    const user = userEvent.setup()
    const { getNodeData } = renderPanel()
    const selector = await screen.findByRole('combobox', {
      name: 'workflowModels.nodes.llm.model',
    })
    expect(selector).toHaveTextContent('GPT-4o')
    await user.click(selector)
    await user.click(await screen.findByRole('option', { name: 'Claude 3.5 Sonnet' }))
    await waitFor(() =>
      expect(getNodeData().model).toEqual({
        provider: 'anthropic',
        name: 'claude-3-5-sonnet',
        mode: 'chat',
        completion_params: { temperature: 0.7 },
      }),
    )
    expect(
      screen.queryByRole('button', { name: 'plugin.detailPanel.configureModel' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /modelProvider.modelSettings/ })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('radio', { name: 'workflow.nodes.common.typeSwitch.variable' }),
    ).not.toBeInTheDocument()
  })

  it('hydrates the backend default for an unconfigured node', async () => {
    renderPanel({
      data: createData({ model: { provider: '', name: '', mode: 'chat', completion_params: {} } }),
    })
    const selector = await screen.findByRole('combobox', {
      name: 'workflowModels.nodes.llm.model',
    })
    await waitFor(() => expect(selector).toHaveTextContent('GPT-4o'))
  })

  it('keeps the configured model selector read-only', async () => {
    renderPanel({ readOnly: true })
    expect(
      await screen.findByRole('combobox', { name: 'workflowModels.nodes.llm.model' }),
    ).toBeDisabled()
  })
})
