import type { FC } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { AppHeader } from '@/shared/ui/AppHeader';
import { FlowDocument, ProjectSteps } from '@/widgets/project-flow';
import { ProjectImportContent, useProjectImport } from '@/features/import';

export const ImportPage: FC = () => {
  const navigate = useNavigate();
  const { projectId } = useParams();
  const { key: routeKey, search } = useLocation();
  const importer = useProjectImport({
    projectId,
    routeKey,
    onNavigate: navigate,
  });
  const { sourceReadOnly, project } = importer;

  return (
    <div className="flex h-dvh min-h-0 min-w-0 flex-col overflow-hidden max-[760px]:h-auto max-[760px]:min-h-dvh max-[760px]:overflow-visible">
      <AppHeader projectName={project?.name} />
      <main
        className={
          sourceReadOnly
            ? 'mx-auto flex min-h-0 w-full max-w-280 flex-1 [&>section]:w-full'
            : 'grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)] max-[760px]:block min-[1100px]:grid-cols-[200px_minmax(0,1fr)] min-[1100px]:grid-rows-1 min-[1200px]:grid-cols-[240px_minmax(0,1fr)]'
        }
      >
        {!sourceReadOnly && (
          <>
            <div className="hidden min-[1100px]:contents">
              <ProjectSteps active={1} projectName={project?.name} />
            </div>
            <p
              aria-current="step"
              className="m-0 flex min-h-11 items-center gap-2.5 border-0 border-b border-solid border-neutral-200 bg-white px-6 text-sm min-[1100px]:hidden"
            >
              <span className="font-mono text-xs text-blue-700">01</span>
              <span className="font-medium text-neutral-800">
                Исходные данные
              </span>
            </p>
          </>
        )}
        <FlowDocument
          title={
            sourceReadOnly
              ? 'Исходный чертёж зафиксирован'
              : 'Добавьте исходный чертёж'
          }
          description={
            sourceReadOnly
              ? 'В проекте уже есть план. Новый исходный файл требует отдельного проекта.'
              : 'Выберите исходный файл, подключённый комплект CAD или ZIP-пакет выпуска.'
          }
        >
          <div className="flex max-w-186 flex-col gap-4">
            <ProjectImportContent
              key={routeKey}
              projectId={projectId}
              routeKey={routeKey}
              initialSource={new URLSearchParams(search).get('source')}
              onNavigate={navigate}
              importer={importer}
            />
          </div>
        </FlowDocument>
      </main>
    </div>
  );
};
