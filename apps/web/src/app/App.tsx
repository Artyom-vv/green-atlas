import { lazy, Suspense, type FC } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { EditorSessionProvider } from '@/entities/editor';
import { DesktopNavigationBridge } from './DesktopNavigationBridge';
import { LocalRuntimeBoundary } from './LocalRuntimeBoundary';

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
const NewProjectPage = lazy(async () => ({
  default: (await import('@/pages/NewProjectPage')).NewProjectPage,
}));

export const App: FC = () => {
  const { pathname } = useLocation();
  const projectId = pathname.match(/^\/projects\/([^/]+)\//)?.[1] ?? '';
  return (
    <>
      <DesktopNavigationBridge />
      <EditorSessionProvider
        key={projectId}
        projectId={projectId === 'new' ? '' : projectId}
      >
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
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/projects/new" element={<NewProjectPage />} />
            <Route
              path="/projects/new/import"
              element={<Navigate to="/projects/new" replace />}
            />
            <Route
              path="/projects/:projectId/import"
              element={<ImportPage />}
            />
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
      </EditorSessionProvider>
      <LocalRuntimeBoundary />
    </>
  );
};
