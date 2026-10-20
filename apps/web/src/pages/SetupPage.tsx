/** Настройка проекта: импорт открывает форму, подготовку запускает пользователь */
import { useState, type FC } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { ControlProvider } from '@green/ui';
import { useQueryClient } from '@tanstack/react-query';
import { ProjectWorkProvider } from '@/features/project-navigation/model/ProjectWorkProvider';
import { useSourcePreparation } from '@/features/source-preparation';
import { ProjectReadState } from '@/entities/project/ui/ProjectReadState';
import { restartProjectRead } from '@/entities/project/api/savedProjectQuery';
import { SourcePreparationView } from '@/widgets/source-preparation/ui/SourcePreparationView';
import { SourcePreparationViewPropsFor } from '@/widgets/source-preparation/ui/SourcePreparationView.props';
import { SourceReadOnlyView } from '@/widgets/source-preparation/ui/SourceReadOnlyView';
import { SourceReadOnlyDocumentPropsFor } from '@/widgets/source-preparation/ui/SourceReadOnlyDocument.props';
export const SetupPage: FC = () => {
  const { projectId = '' } = useParams();
  return (
    <ControlProvider size="compact">
      <ProjectWorkProvider key={projectId} projectId={projectId}>
        <SetupSession projectId={projectId} />
      </ProjectWorkProvider>
    </ControlProvider>
  );
};
interface SetupSessionProps {
  projectId: string;
}
const SetupSession: FC<SetupSessionProps> = ({ projectId }) => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [editingSection, setEditingSection] = useState<'02' | '03' | '04'>();
  const initialSection =
    editingSection ??
    (searchParams.get('section') === 'layers' ? '03' : undefined);
  const queryClient = useQueryClient();
  const state = useSourcePreparation({
    projectId,
    navigate,
    editingRequested:
      searchParams.get('section') === 'layers' &&
      searchParams.get('edit') === 'assignments',
  });
  const project = state.projectQuery.data;
  if (!project)
    return (
      <ProjectReadState
        key={projectId}
        status={
          state.projectQuery.isFetching
            ? 'loading'
            : state.projectNotFound
              ? 'missing'
              : state.projectQuery.isError
                ? 'error'
                : 'stopped'
        }
        onRetry={() =>
          void restartProjectRead(queryClient, ['setup-project', projectId])
        }
        onBack={() => navigate('/projects')}
      />
    );
  const onPlan = () => navigate(`/projects/${projectId}/workspace`);
  if (state.sourceReadOnly)
    return (
      <SourceReadOnlyView
        {...SourceReadOnlyDocumentPropsFor(state)}
        projectName={project.name}
        initialSection={initialSection}
        startSourceEditing={(section) => {
          setEditingSection(section);
          state.startSourceEditing();
        }}
        onPlan={onPlan}
        onBack={() => navigate('/projects')}
      />
    );
  return (
    <SourcePreparationView
      {...SourcePreparationViewPropsFor(state)}
      projectName={project.name}
      initialSection={initialSection}
      mapReady={project.map_ready}
      onPlan={onPlan}
      onImport={() => navigate('/projects/new')}
    />
  );
};
