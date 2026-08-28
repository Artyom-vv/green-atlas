import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

const ImportPage = lazy(async () => ({ default: (await import('../pages/ImportPage')).ImportPage }));
const SetupPage = lazy(async () => ({ default: (await import('../pages/SetupPage')).SetupPage }));
const WorkspacePage = lazy(async () => ({ default: (await import('../pages/WorkspacePage')).WorkspacePage }));
const ProjectsPage = lazy(async () => ({ default: (await import('../pages/ProjectsPage')).ProjectsPage }));

export function App() {
  return (
    <Suspense fallback={<main className="app-loading" aria-live="polite">Открываем рабочее пространство</main>}>
      <Routes>
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/new/import" element={<ImportPage />} />
        <Route path="/projects/:projectId/import" element={<ImportPage />} />
        <Route path="/projects/:projectId/setup" element={<SetupPage />} />
        <Route path="/projects/:projectId/workspace" element={<WorkspacePage />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </Suspense>
  );
}
