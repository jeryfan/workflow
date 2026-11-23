import type { ModelConfig, VisionSetting } from '@/app/components/workflow/types'
import { act } from '@testing-library/react'
import { renderEditorHook as renderHook } from '@/app/components/workflow/editor-resource/__tests__/render'
import { Resolution } from '@/types/app'
import useConfigVision from '../use-config-vision'

const mockUseQuery = vi.hoisted(() => vi.fn())
let chatflowMode = false

vi.mock('@tanstack/react-query', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-query')>()),
  useQuery: (...args: unknown[]) => mockUseQuery(...args),
}))

const createModel = (overrides: Partial<ModelConfig> = {}): ModelConfig => ({
  provider: 'openai',
  name: 'gpt-4o',
  mode: 'chat',
  completion_params: {},
  ...overrides,
})

const createVisionPayload = (
  overrides: Partial<{ enabled: boolean; configs?: VisionSetting }> = {},
) => ({
  enabled: false,
  ...overrides,
})

describe('useConfigVision', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    chatflowMode = false
    mockUseQuery.mockReturnValue({ data: [] })
  })

  it('should expose vision capability and enable default chat configs for vision models', () => {
    const onChange = vi.fn()
    chatflowMode = true
    mockUseQuery.mockReturnValue({
      data: [{ id: 'gpt-4o', model: 'gpt-4o', capabilities: ['vision'] }],
    })

    const { result } = renderHook(
      () =>
        useConfigVision(createModel(), {
          payload: createVisionPayload(),
          onChange,
        }),
      {
        resource: {
          kind: 'app',
          appId: 'app-1',
          mode: chatflowMode ? 'advanced-chat' : 'workflow',
        },
      },
    )

    expect(result.current.isVisionModel).toBe(true)

    act(() => {
      result.current.handleVisionResolutionEnabledChange(true)
    })

    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      configs: {
        detail: Resolution.high,
        variable_selector: ['sys', 'files'],
      },
    })
  })

  it('should clear configs when disabling vision resolution', () => {
    const onChange = vi.fn()

    const { result } = renderHook(
      () =>
        useConfigVision(createModel(), {
          payload: createVisionPayload({
            enabled: true,
            configs: {
              detail: Resolution.low,
              variable_selector: ['node', 'files'],
            },
          }),
          onChange,
        }),
      {
        resource: {
          kind: 'app',
          appId: 'app-1',
          mode: chatflowMode ? 'advanced-chat' : 'workflow',
        },
      },
    )

    act(() => {
      result.current.handleVisionResolutionEnabledChange(false)
    })

    expect(onChange).toHaveBeenCalledWith({
      enabled: false,
    })
  })

  it('should update the resolution config payload directly', () => {
    const onChange = vi.fn()
    const config: VisionSetting = {
      detail: Resolution.low,
      variable_selector: ['upstream', 'images'],
    }

    const { result } = renderHook(
      () =>
        useConfigVision(createModel(), {
          payload: createVisionPayload({ enabled: true }),
          onChange,
        }),
      {
        resource: {
          kind: 'app',
          appId: 'app-1',
          mode: chatflowMode ? 'advanced-chat' : 'workflow',
        },
      },
    )

    act(() => {
      result.current.handleVisionResolutionChange(config)
    })

    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      configs: config,
    })
  })

  it('should disable vision settings when the selected model is no longer a vision model', () => {
    const onChange = vi.fn()

    const { result } = renderHook(
      () =>
        useConfigVision(createModel(), {
          payload: createVisionPayload({
            enabled: true,
            configs: {
              detail: Resolution.high,
              variable_selector: ['sys', 'files'],
            },
          }),
          onChange,
        }),
      {
        resource: {
          kind: 'app',
          appId: 'app-1',
          mode: chatflowMode ? 'advanced-chat' : 'workflow',
        },
      },
    )

    act(() => {
      result.current.handleModelChanged()
    })

    expect(onChange).toHaveBeenCalledWith({
      enabled: false,
    })
  })

  it('should reset enabled vision configs when the model changes but still supports vision', () => {
    const onChange = vi.fn()
    mockUseQuery.mockReturnValue({
      data: [{ id: 'gpt-4o', model: 'gpt-4o', capabilities: ['vision'] }],
    })

    const { result } = renderHook(
      () =>
        useConfigVision(createModel(), {
          payload: createVisionPayload({
            enabled: true,
            configs: {
              detail: Resolution.low,
              variable_selector: ['old', 'files'],
            },
          }),
          onChange,
        }),
      {
        resource: {
          kind: 'app',
          appId: 'app-1',
          mode: chatflowMode ? 'advanced-chat' : 'workflow',
        },
      },
    )

    act(() => {
      result.current.handleModelChanged()
    })

    expect(onChange).toHaveBeenCalledWith({
      enabled: true,
      configs: {
        detail: Resolution.high,
        variable_selector: [],
      },
    })
  })
})
