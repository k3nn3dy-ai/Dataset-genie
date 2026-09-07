import { createBrowserRouter } from 'react-router-dom'
import { Layout } from './Layout'
import { Placeholder } from '../screens/Placeholder'
import { STAGES } from '../lib/types'

// Screen modules are owned by the frontend track; the lead only wires routes.
export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Placeholder title="Projects" /> },
      { path: 'settings', element: <Placeholder title="Settings" /> },
      { path: '_kit', element: <Placeholder title="Component kit" /> },
      ...STAGES.map((s) => ({ path: `p/:projectId/${s.n}`, element: <Placeholder title={s.title} stage={s.n} /> })),
    ],
  },
])
