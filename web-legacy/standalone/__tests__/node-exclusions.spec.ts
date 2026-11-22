import {
  isExcludedCatalogNodeType,
  isExcludedNodeType,
} from '../node-exclusions.generated'

describe('standalone workflow node allowlists', () => {
  it.each([
    'llm',
    'question-classifier',
    'parameter-extractor',
    'human-input-v2',
    'agent',
  ])('excludes unsupported node type %s from the canvas catalog', (type) => {
    expect(isExcludedNodeType(type)).toBe(true)
    expect(isExcludedCatalogNodeType(type)).toBe(true)
  })

  it.each(['start', 'template-transform', 'code', 'if-else', 'variable-aggregator', 'list-operator', 'http-request', 'iteration', 'loop', 'document-extractor', 'human-input', 'end', 'tool', 'trigger-schedule', 'trigger-webhook'])(
    'allows supported node type %s to be created',
    (type) => {
      expect(isExcludedNodeType(type)).toBe(false)
      expect(isExcludedCatalogNodeType(type)).toBe(false)
    },
  )

  it.each(['start-placeholder', 'iteration-start', 'loop-start', 'loop-end', 'assigner'])(
    'keeps internal node type %s recognizable without allowing standalone creation',
    (type) => {
      expect(isExcludedNodeType(type)).toBe(false)
      expect(isExcludedCatalogNodeType(type)).toBe(true)
    },
  )
})
