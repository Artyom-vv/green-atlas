import { lazy, Suspense, type FC } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { ProjectAssistantProvider } from '@/features/assistant';

const ImportPage = lazy(async () => ({
  default: (await import('@/pages/ImportPage')).ImportPage,
}));
const SetupPage = lazy(async () => ({
  default: (await import('@/pages/SetupPage')).SetupPage,
}));
const WorkspacePage = lazy(async () => ({
  default: (await import('@/pages/WorkspacePage')).WorkspacePage,
}));
const ProjectsPage = lazy(async () => ({
  default: (await import('@/pages/ProjectsPage')).ProjectsPage,
}));
const AutoCadConnectPage = lazy(async () => ({
  default: (await import('@/pages/AutoCadConnectPage')).AutoCadConnectPage,
}));

export const App: FC = () => {
  return (
    <ProjectAssistantProvider>
      <Suspense
        fallback={
          <main
            className="text-ink-700 grid min-h-dvh place-items-center text-sm"
            aria-live="polite"
          >
            Открываем рабочее пространство
          </main>
        }
      >
        <Routes>
          <Route
            path="/connect/autocad/:transferId"
            element={<AutoCadConnectPage />}
          />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/new/import" element={<ImportPage />} />
          <Route path="/projects/:projectId/import" element={<ImportPage />} />
          <Route path="/projects/:projectId/setup" element={<SetupPage />} />
          <Route
            path="/projects/:projectId/workspace"
            element={<WorkspacePage />}
          />
          <Route
            path="/projects/:projectId/workspace/ide"
            element={<Navigate to=".." relative="path" replace />}
          />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Routes>
      </Suspense>
    </ProjectAssistantProvider>
  );
};
