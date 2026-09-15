import type { FC } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ControlProvider } from '@green/ui';
import { useProjectAssistant } from '@/features/assistant';
import { useSourcePreparation } from '@/features/source-preparation';
import { SourceProjectState } from '@/widgets/source-preparation/ui/SourceProjectState';
import { SourcePreparationView } from '@/widgets/source-preparation/ui/SourcePreparationView';
import { SourcePreparationViewPropsFor } from '@/widgets/source-preparation/ui/SourcePreparationView.props';
import { SourceReadOnlyView } from '@/widgets/source-preparation/ui/SourceReadOnlyView';
import { SourceReadOnlyDocumentPropsFor } from '@/widgets/source-preparation/ui/SourceReadOnlyDocument.props';
export const SetupPage: FC = () => {
  const { projectId = '' } = useParams();
  return (
    <ControlProvider size="compact">
      <SetupSession key={projectId} projectId={projectId} />
    </ControlProvider>
  );
};
interface SetupSessionProps {
  projectId: string;
}
const SetupSession: FC<SetupSessionProps> = ({ projectId }) => {
  const assistant = useProjectAssistant();
  const navigate = useNavigate();
  const state = useSourcePreparation({ projectId, navigate });
  const project = state.projectQuery.data;
  if (state.projectQuery.isLoading) return <SourceProjectState loading />;
  if (!project) return <SourceProjectState />;
  const onPlan = () => navigate(`/projects/${projectId}/workspace`);
  if (state.sourceReadOnly)
    return (
      <SourceReadOnlyView
        {...SourceReadOnlyDocumentPropsFor(state)}
        projectName={project.name}
        assistant={assistant}
        onPlan={onPlan}
        onBack={() => navigate('/projects')}
      />
    );
  return (
    <SourcePreparationView
      {...SourcePreparationViewPropsFor(state)}
      projectName={project.name}
      mapReady={project.map_ready}
      onPlan={onPlan}
      onImport={() => navigate(`/projects/${projectId}/import`)}
    />
  );
};
