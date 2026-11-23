import type { FC } from 'react'
import type { LLMModelOption, LLMNodeType } from './types'
import type { NodeProps } from '@/app/components/workflow/types'
import { useQuery } from '@tanstack/react-query'
import * as React from 'react'
import { consoleQuery } from '@/service/console'

const Node: FC<NodeProps<LLMNodeType>> = ({ data }) => {
  const { data: models = [] } = useQuery(
    consoleQuery.workspaces.current.models.modelTypes.byModelType.get.queryOptions({
      input: { params: { model_type: 'llm' } },
      select: response => response.data as unknown as LLMModelOption[],
    }),
  )
  const model = models.find(item => item.id === data.model?.id)
    ?? models.find(item => item.model === data.model?.name)

  if (!data.model?.name) return null

  return (
    <div className="mb-1 px-3 py-1">
      <div className="inline-flex h-6 max-w-full items-center gap-1 rounded-md px-1.5 text-xs text-text-secondary">
        <span className="truncate">{model?.name || data.model.name}</span>
        <span className="shrink-0 text-text-tertiary">CHAT</span>
      </div>
    </div>
  )
}

export default React.memo(Node)
