import type { CommonNodeType } from '../../types'
import { ModelFeatureEnum } from '@/app/components/header/account-setting/model-provider-page/declarations'
import { consoleQuery } from '@/service/console'
import {
  createCatalogModelFixture,
  createModelCatalogFixture,
  seedModelProviderQueries,
} from '@/test/console/model-provider'
import { createConsoleQueryWrapper } from '@/test/console/query-data'
import { seedToolProviders } from '@/test/console/tool-providers'
import { createEdge, createNode, createStartNode } from '../../__tests__/fixtures'
import { InputVarType, VarType } from '../../types'

// Both single-run form owners resolve vision capabilities and file types from these real inputs.
export function createSingleRunGraphOptions(payload: CommonNodeType, vision = true) {
  const { queryClient } = createConsoleQueryWrapper()
  seedModelProviderQueries(queryClient, [
    createModelCatalogFixture({
      models: [
        createCatalogModelFixture({
          model: 'gpt-4o',
          features: vision ? [ModelFeatureEnum.vision] : [],
        }),
      ],
    }),
  ])
  queryClient.setQueryData(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryKey({
      input: { params: { model_type: 'llm' } },
    }),
    {
      data: [{
        id: 'gpt-4o',
        model: 'gpt-4o',
        capabilities: vision ? ['chat', 'vision'] : ['chat'],
      }],
    } as never,
  )
  seedToolProviders(queryClient, {
    builtInTools: [],
    customTools: [],
    workflowTools: [],
    mcpTools: [],
  })
  queryClient.setQueryData(consoleQuery.spec.schemaDefinitions.get.queryKey(), [])
  const nodes = [
    createStartNode({
      id: 'node-3',
      data: {
        title: 'Vision Source',
        variables: [
          { variable: 'image', label: 'Image', type: InputVarType.singleFile, required: false },
        ],
      },
    }),
    createNode({ id: 'node-1', data: { outputs: { query: { type: VarType.string } } } }),
    createNode({ id: 'node-2', data: { outputs: { answer: { type: VarType.string } } } }),
    createNode({ id: 'current-node', data: payload }),
  ]
  const edges = nodes
    .slice(1)
    .map((node, i) => createEdge({ source: nodes[i]!.id, target: node.id }))
  return { queryClient, nodes, edges }
}
