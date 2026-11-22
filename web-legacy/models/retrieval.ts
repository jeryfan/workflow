import type {
  DatasetDetailWithPartialMembersResponse,
  DatasetDocMetadataResponse,
} from '@dify/contracts/api/console/datasets/types.gen'
import type {
  ModelConfig,
  Node,
  NodeOutPutVar,
} from '@/app/components/workflow/types'
import type { SelectedDatasetsMode } from '@/models/datasets'
import { uniq } from 'es-toolkit/array'
import { xorBy } from 'es-toolkit/compat'
import { DATASET_DEFAULT } from '@/config'
import { DEFAULT_WEIGHTED_SCORE, RerankingModeEnum, WeightedScoreEnum } from '@/models/datasets'
import { RETRIEVE_METHOD } from '@/types/app'

export type MultipleRetrievalConfig = {
  top_k: number
  score_threshold: number | null | undefined
  reranking_model?: {
    provider: string
    model: string
  }
  reranking_mode?: RerankingModeEnum
  weights?: {
    weight_type: WeightedScoreEnum
    vector_setting: {
      vector_weight: number
      embedding_provider_name: string
      embedding_model_name: string
    }
    keyword_setting: {
      keyword_weight: number
    }
  }
  reranking_enable?: boolean
}

export type SingleRetrievalConfig = {
  model: ModelConfig
}

export const LogicalOperator = {
  and: 'and',
  or: 'or',
} as const

export type LogicalOperator = (typeof LogicalOperator)[keyof typeof LogicalOperator]

export const ComparisonOperator = {
  contains: 'contains',
  notContains: 'not contains',
  startWith: 'start with',
  endWith: 'end with',
  is: 'is',
  isNot: 'is not',
  empty: 'empty',
  notEmpty: 'not empty',
  equal: '=',
  notEqual: '≠',
  largerThan: '>',
  lessThan: '<',
  largerThanOrEqual: '≥',
  lessThanOrEqual: '≤',
  isNull: 'is null',
  isNotNull: 'is not null',
  in: 'in',
  notIn: 'not in',
  allOf: 'all of',
  exists: 'exists',
  notExists: 'not exists',
  before: 'before',
  after: 'after',
} as const

export type ComparisonOperator = (typeof ComparisonOperator)[keyof typeof ComparisonOperator]

export const MetadataFilteringModeEnum = {
  disabled: 'disabled',
  automatic: 'automatic',
  manual: 'manual',
} as const

export type MetadataFilteringModeEnum =
  (typeof MetadataFilteringModeEnum)[keyof typeof MetadataFilteringModeEnum]

export const MetadataFilteringVariableType = {
  string: 'string',
  number: 'number',
  time: 'time',
  select: 'select',
} as const

export type MetadataFilteringVariableType =
  (typeof MetadataFilteringVariableType)[keyof typeof MetadataFilteringVariableType]

export type MetadataFilteringCondition = {
  id: string
  name: string
  metadata_id?: string
  comparison_operator: ComparisonOperator
  value?: string | number | string[] | null
}

export type MetadataFilteringConditions = {
  logical_operator: LogicalOperator
  conditions: MetadataFilteringCondition[]
}

export type HandleAddCondition = (metadataItem: DatasetDocMetadataResponse) => void
export type HandleRemoveCondition = (id: string) => void
export type HandleUpdateCondition = (id: string, newCondition: MetadataFilteringCondition) => void
export type HandleToggleConditionLogicalOperator = () => void

export type MetadataShape = {
  metadataList?: DatasetDocMetadataResponse[]
  selectedDatasetsLoaded?: boolean
  metadataFilteringConditions?: MetadataFilteringConditions
  handleAddCondition: HandleAddCondition
  handleRemoveCondition: HandleRemoveCondition
  handleToggleConditionLogicalOperator: HandleToggleConditionLogicalOperator
  handleUpdateCondition: HandleUpdateCondition
  metadataModelConfig?: ModelConfig
  handleMetadataModelChange?: (model: {
    modelId: string
    provider: string
    mode?: string
    features?: string[]
  }) => void
  handleMetadataCompletionParamsChange?: (params: Record<string, any>) => void
  availableStringVars?: NodeOutPutVar[]
  availableStringNodesWithParent?: Node[]
  availableNumberVars?: NodeOutPutVar[]
  availableNumberNodesWithParent?: Node[]
  isCommonVariable?: boolean
  availableCommonStringVars?: { name: string; type: string; value: string }[]
  availableCommonNumberVars?: { name: string; type: string; value: string }[]
}

export const getSelectedDatasetsMode = (
  datasets: DatasetDetailWithPartialMembersResponse[] = [],
) => {
  if (datasets === null) datasets = []
  let allHighQuality = true
  let allHighQualityVectorSearch = true
  let allHighQualityFullTextSearch = true
  let allEconomic = true
  let mixtureHighQualityAndEconomic = true
  let allExternal = true
  let allInternal = true
  let mixtureInternalAndExternal = true
  let inconsistentEmbeddingModel = false
  if (!datasets.length) {
    allHighQuality = false
    allHighQualityVectorSearch = false
    allHighQualityFullTextSearch = false
    allEconomic = false
    mixtureHighQualityAndEconomic = false
    allExternal = false
    allInternal = false
    mixtureInternalAndExternal = false
  }
  datasets.forEach((dataset) => {
    if (dataset.indexing_technique === 'economy') {
      allHighQuality = false
      allHighQualityVectorSearch = false
      allHighQualityFullTextSearch = false
    }
    if (dataset.indexing_technique === 'high_quality') {
      allEconomic = false

      if (dataset.retrieval_model_dict.search_method !== RETRIEVE_METHOD.semantic)
        allHighQualityVectorSearch = false

      if (dataset.retrieval_model_dict.search_method !== RETRIEVE_METHOD.fullText)
        allHighQualityFullTextSearch = false
    }
    if (dataset.provider !== 'external') {
      allExternal = false
    } else {
      allInternal = false
      allHighQuality = false
      allHighQualityVectorSearch = false
      allHighQualityFullTextSearch = false
      mixtureHighQualityAndEconomic = false
    }
  })

  if (allExternal || allInternal) mixtureInternalAndExternal = false

  if (allHighQuality || allEconomic) mixtureHighQualityAndEconomic = false

  if (allHighQuality)
    // Weighted score needs one embedding space, so the provider matters as much as the model name.
    inconsistentEmbeddingModel =
      uniq(datasets.map((item) => `${item.embedding_model_provider}/${item.embedding_model}`))
        .length > 1

  return {
    allHighQuality,
    allHighQualityVectorSearch,
    allHighQualityFullTextSearch,
    allEconomic,
    mixtureHighQualityAndEconomic,
    allInternal,
    allExternal,
    mixtureInternalAndExternal,
    inconsistentEmbeddingModel,
  } as SelectedDatasetsMode
}

export const getMultipleRetrievalConfig = (
  multipleRetrievalConfig: MultipleRetrievalConfig,
  selectedDatasets: DatasetDetailWithPartialMembersResponse[],
  originalDatasets: DatasetDetailWithPartialMembersResponse[],
  fallbackRerankModel?: { provider?: string; model?: string },
) => {
  const isDatasetsChanged = xorBy(selectedDatasets, originalDatasets, 'id').length > 0
  const isFallbackRerankModelValid = !!(fallbackRerankModel?.provider && fallbackRerankModel?.model)

  const {
    allHighQuality,
    allHighQualityVectorSearch,
    allHighQualityFullTextSearch,
    allEconomic,
    mixtureHighQualityAndEconomic,
    allInternal,
    allExternal,
    mixtureInternalAndExternal,
    inconsistentEmbeddingModel,
  } = getSelectedDatasetsMode(selectedDatasets)

  const {
    top_k = DATASET_DEFAULT.top_k,
    score_threshold,
    reranking_mode,
    reranking_model,
    weights,
    reranking_enable,
  } = multipleRetrievalConfig || { top_k: DATASET_DEFAULT.top_k }

  const result = {
    top_k,
    score_threshold,
    reranking_mode,
    reranking_model,
    weights,
    reranking_enable,
  }

  const setDefaultWeights = () => {
    result.weights = {
      weight_type: WeightedScoreEnum.Customized,
      vector_setting: {
        vector_weight: allHighQualityVectorSearch
          ? DEFAULT_WEIGHTED_SCORE.allHighQualityVectorSearch.semantic
          : allHighQualityFullTextSearch
            ? DEFAULT_WEIGHTED_SCORE.allHighQualityFullTextSearch.semantic
            : DEFAULT_WEIGHTED_SCORE.other.semantic,
        embedding_provider_name: selectedDatasets[0]!.embedding_model_provider ?? '',
        embedding_model_name: selectedDatasets[0]!.embedding_model ?? '',
      },
      keyword_setting: {
        keyword_weight: allHighQualityVectorSearch
          ? DEFAULT_WEIGHTED_SCORE.allHighQualityVectorSearch.keyword
          : allHighQualityFullTextSearch
            ? DEFAULT_WEIGHTED_SCORE.allHighQualityFullTextSearch.keyword
            : DEFAULT_WEIGHTED_SCORE.other.keyword,
      },
    }
  }

  if ((allEconomic && allInternal) || allExternal) {
    result.reranking_mode = RerankingModeEnum.RerankingModel
    if (
      (!result.reranking_model?.provider || !result.reranking_model?.model) &&
      isFallbackRerankModelValid
    ) {
      result.reranking_model = {
        provider: fallbackRerankModel.provider || '',
        model: fallbackRerankModel.model || '',
      }
    }
    result.reranking_enable = reranking_enable
  }

  if (mixtureHighQualityAndEconomic || inconsistentEmbeddingModel || mixtureInternalAndExternal) {
    result.reranking_mode = RerankingModeEnum.RerankingModel
    if (
      (!result.reranking_model?.provider || !result.reranking_model?.model) &&
      isFallbackRerankModelValid
    ) {
      result.reranking_model = {
        provider: fallbackRerankModel.provider || '',
        model: fallbackRerankModel.model || '',
      }
    }
    result.reranking_enable = true
  }

  if (allHighQuality && !inconsistentEmbeddingModel && allInternal) {
    if (!reranking_mode) {
      if (isFallbackRerankModelValid) {
        result.reranking_mode = RerankingModeEnum.RerankingModel
        result.reranking_enable = true

        result.reranking_model = {
          provider: fallbackRerankModel.provider || '',
          model: fallbackRerankModel.model || '',
        }
      } else {
        result.reranking_mode = RerankingModeEnum.WeightedScore
        result.reranking_enable = false
        setDefaultWeights()
      }
    }

    if (reranking_mode === RerankingModeEnum.WeightedScore) {
      result.reranking_enable = false
      if (!weights) setDefaultWeights()
    }
    if (reranking_mode === RerankingModeEnum.RerankingModel) {
      if (
        (!result.reranking_model?.provider || !result.reranking_model?.model) &&
        isFallbackRerankModelValid
      ) {
        result.reranking_model = {
          provider: fallbackRerankModel.provider || '',
          model: fallbackRerankModel.model || '',
        }
      }
      result.reranking_enable = true
    }

    if (reranking_mode === RerankingModeEnum.WeightedScore && weights && isDatasetsChanged) {
      if (
        (result.reranking_model?.provider && result.reranking_model?.model) ||
        isFallbackRerankModelValid
      ) {
        result.reranking_mode = RerankingModeEnum.RerankingModel
        result.reranking_enable = true

        if (
          (!result.reranking_model?.provider || !result.reranking_model?.model) &&
          isFallbackRerankModelValid
        ) {
          result.reranking_model = {
            provider: fallbackRerankModel.provider || '',
            model: fallbackRerankModel.model || '',
          }
        }
      } else {
        setDefaultWeights()
      }
    }
    if (
      reranking_mode === RerankingModeEnum.RerankingModel &&
      (!result.reranking_model?.provider || !result.reranking_model?.model) &&
      !isFallbackRerankModelValid &&
      isDatasetsChanged
    ) {
      result.reranking_mode = RerankingModeEnum.WeightedScore
      result.reranking_enable = false
      setDefaultWeights()
    }
  }

  return result
}

export const checkoutRerankModelConfiguredInRetrievalSettings = (
  datasets: DatasetDetailWithPartialMembersResponse[],
  multipleRetrievalConfig?: MultipleRetrievalConfig,
) => {
  if (!multipleRetrievalConfig) return true

  const { allEconomic, allExternal, allInternal } = getSelectedDatasetsMode(datasets)

  const { reranking_enable, reranking_mode, reranking_model } = multipleRetrievalConfig

  if (
    reranking_mode === RerankingModeEnum.RerankingModel &&
    (!reranking_model?.provider || !reranking_model?.model)
  )
    return ((allEconomic && allInternal) || allExternal) && !reranking_enable

  return true
}
