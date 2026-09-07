import { createBrowserRouter } from 'react-router-dom'
import { Layout } from './Layout'
import { Kit } from '../screens/Kit'
import { ProjectsScreen } from '../screens/projects/ProjectsScreen'
import { TaxonomyScreen } from '../screens/taxonomy/TaxonomyScreen'
import { PromptsScreen } from '../screens/prompts/PromptsScreen'
import { ResponsesScreen } from '../screens/responses/ResponsesScreen'
import { RejectedScreen } from '../screens/rejected/RejectedScreen'
import { JudgeScreen } from '../screens/judge/JudgeScreen'
import { FilterScreen } from '../screens/filter/FilterScreen'
import { ReviewScreen } from '../screens/review/ReviewScreen'
import { ExportScreen } from '../screens/export/ExportScreen'
import { SettingsScreen } from '../screens/settings/SettingsScreen'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <ProjectsScreen /> },
      { path: 'settings', element: <SettingsScreen /> },
      { path: '_kit', element: <Kit /> },
      { path: 'p/:projectId/1', element: <TaxonomyScreen /> },
      { path: 'p/:projectId/2', element: <PromptsScreen /> },
      { path: 'p/:projectId/3', element: <ResponsesScreen /> },
      { path: 'p/:projectId/4', element: <RejectedScreen /> },
      { path: 'p/:projectId/5', element: <JudgeScreen /> },
      { path: 'p/:projectId/6', element: <FilterScreen /> },
      { path: 'p/:projectId/7', element: <ReviewScreen /> },
      { path: 'p/:projectId/8', element: <ExportScreen /> },
    ],
  },
])
