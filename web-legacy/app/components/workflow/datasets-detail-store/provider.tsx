import type { FC } from 'react'
import type { Node } from '../types'
import { createContext, useState } from 'react'
import { createDatasetsDetailStore } from './store'

type DatasetsDetailStoreApi = ReturnType<typeof createDatasetsDetailStore>

type DatasetsDetailContextType = DatasetsDetailStoreApi | undefined

export const DatasetsDetailContext = createContext<DatasetsDetailContextType>(undefined)

type DatasetsDetailProviderProps = {
  nodes: Node[]
  children: React.ReactNode
}

const DatasetsDetailProvider: FC<DatasetsDetailProviderProps> = ({ children }) => {
  const [store] = useState(createDatasetsDetailStore)

  return <DatasetsDetailContext.Provider value={store}>{children}</DatasetsDetailContext.Provider>
}

export default DatasetsDetailProvider
