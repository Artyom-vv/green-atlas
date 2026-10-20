import { Button } from '@green/ui';

export interface WorkspaceReadStatusProps {
  issue?: string;
  reading: boolean;
  onRetry: () => void;
}

export function WorkspaceReadStatus({
  issue,
  reading,
  onRetry,
}: WorkspaceReadStatusProps) {
  if (!issue) return null;
  return (
    <div
      role="alert"
      className="text-warning flex flex-wrap items-center gap-2 text-sm"
    >
      <span>{issue}</span>
      <Button
        variant="secondary"
        controlSize="compact"
        loading={reading}
        onClick={onRetry}
      >
        Повторить чтение
      </Button>
    </div>
  );
}
