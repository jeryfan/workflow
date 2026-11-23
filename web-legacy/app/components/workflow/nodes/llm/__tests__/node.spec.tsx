import type { LLMNodeType } from '../types'
import { screen } from '@testing-library/react'
import { BlockEnum } from '@/app/components/workflow/types'
import { consoleQuery } from '@/service/console'
import { createConsoleQueryClient, renderWithConsoleQuery } from '@/test/console/query-data'
import { createNuqsTestRenderer } from '@/test/nuqs-testing'
import Node from '../node'

const render = createNuqsTestRenderer(renderWithConsoleQuery)
const createClient = () => {
  const client = createConsoleQueryClient()
  client.setQueryData(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryKey({
      input: { params: { model_type: 'llm' } },
    }),
    {
      data: [{
        id: 'openai-gpt-4o',
        name: 'GPT-4o',
        model: 'gpt-4o',
        provider: 'openai',
        mode: 'chat',
        context_length: 128000,
        max_output_tokens: 4096,
        capabilities: ['chat'],
        is_default: true,
      }],
    } as never,
  )
  return client
}

it('displays a cached environment model without workflow context and hides an unconfigured model', () => {
  const data: LLMNodeType = {
    title: 'LLM',
    desc: '',
    type: BlockEnum.LLM,
    model: { provider: 'openai', name: 'gpt-4o', mode: 'chat', completion_params: {} },
    model_selector: ['env', 'for_summarize'],
    prompt_template: [],
    context: { enabled: false, variable_selector: [] },
    vision: { enabled: false },
  }
  const { rerender } = render(<Node id="llm-node" data={data} />, { queryClient: createClient() })
  expect(screen.getByText('GPT-4o')).toBeInTheDocument()
  rerender(
    <Node id="llm-node" data={{ ...data, model: { ...data.model, provider: '', name: '' } }} />,
  )
  expect(screen.queryByText('GPT-4o')).not.toBeInTheDocument()
})
