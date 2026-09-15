import type { FC } from 'react';
import { Button, InlineMessage } from '@green/ui';
import { RefreshCw } from 'lucide-react';
import { isProjectConflict } from '../model/projectConflict';

export interface ProjectConflictNoticeProps {
  error: unknown;
  onReload: () => void;
  reloading?: boolean;
}

export const ProjectConflictNotice: FC<ProjectConflictNoticeProps> = ({
  error,
  onReload,
  reloading = false,
}) => {
  if (!isProjectConflict(error)) return null;
  return (
    <InlineMessage tone="warning" title="Проект обновлён в другой вкладке">
      <div className="flex flex-wrap items-center gap-3">
        <span>
          Ваше действие не применено. Загрузите актуальную версию и повторите
          его.
        </span>
        <Button
          variant="secondary"
          controlSize="compact"
          icon={<RefreshCw />}
          loading={reloading}
          onClick={onReload}
        >
          Обновить проект
        </Button>
      </div>
    </InlineMessage>
  );
};
