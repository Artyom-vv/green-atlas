/** Компактное состояние фонового распознавания в открытом проекте */
import type { Project } from '@green/api-client';
import { Button } from '@green/ui';
import { useWorkspaceRecognitionActivity } from '../model/useWorkspaceRecognitionActivity';

interface Props {
  project: Project;
  onReview: () => void;
}

export function WorkspaceRecognitionActivity(props: Props) {
  if (!props.project.layers?.some((layer) => layer.mapping_confirmed !== true))
    return null;
  return <ObservedRecognitionActivity {...props} />;
}

function ObservedRecognitionActivity({ project, onReview }: Props) {
  const status = useWorkspaceRecognitionActivity(project);
  if (!status) return null;
  return (
    <Button
      variant="ghost"
      controlSize="compact"
      aria-label={status.label}
      title="Открыть состояние распознавания и назначения слоёв"
      onClick={onReview}
    >
      <span role="status">{status.label}</span>
    </Button>
  );
}
