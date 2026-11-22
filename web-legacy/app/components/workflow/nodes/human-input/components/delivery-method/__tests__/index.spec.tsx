import type { DeliveryMethod } from '../../../types'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NuqsTestingAdapter } from 'nuqs/adapters/testing'
import { useState } from 'react'
import { renderWorkflowFlowComponent } from '@/app/components/workflow/__tests__/workflow-test-env'
import { createConsoleQueryWrapper } from '@/test/console/query-data'
import { DeliveryMethodType } from '../../../types'
import DeliveryMethodForm from '../index'

const prefix = 'workflowHumanInput.nodes.humanInput.deliveryMethod.'
function renderMethods(initial: DeliveryMethod[]) {
  function Form() {
    const [methods, setMethods] = useState(initial)
    return <DeliveryMethodForm nodeId="node-1" value={methods} onChange={setMethods} />
  }
  const { queryClient } = createConsoleQueryWrapper()
  return renderWorkflowFlowComponent(
    <NuqsTestingAdapter>
      <Form />
    </NuqsTestingAdapter>,
    {
      queryClient,
    },
  )
}

it('adds, changes, and removes Webapp delivery', async () => {
  const user = userEvent.setup()
  renderMethods([])
  await user.click(screen.getAllByRole('button', { name: `${prefix}title` })[1]!)
  await user.click(screen.getByText(`${prefix}types.webapp.title`))
  await user.keyboard('{Escape}')
  const toggle = screen.getByRole('switch')
  expect(toggle).toBeChecked()
  await user.click(toggle)
  expect(toggle).not.toBeChecked()
  await user.click(screen.getByRole('button', { name: 'common.operation.remove' }))
  expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  expect(screen.getByText(`${prefix}emptyTip`)).toBeInTheDocument()
})

it('does not expose email delivery or an upgrade entry', async () => {
  const user = userEvent.setup()
  renderMethods([])
  await user.click(screen.getAllByRole('button', { name: `${prefix}title` })[1]!)
  expect(screen.queryByText(`${prefix}types.email.title`)).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: `${prefix}upgradeTipHide` })).not.toBeInTheDocument()
  await user.keyboard('{Escape}')
})
