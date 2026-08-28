import { Button, InlineMessage } from '@green/ui';
import { RefreshCw } from 'lucide-react';
import { isProjectConflict } from './projectConflict';

export function ProjectConflictNotice({ error, onReload, reloading = false }: { error: unknown; onReload: () => void; reloading?: boolean }) {
  if (!isProjectConflict(error)) return null;
  return <InlineMessage tone="warning" title="Проект обновлён в другой вкладке">
    <div className="project-conflict-notice__body">
      <span>Ваше действие не применено. Загрузите актуальную версию и повторите его.</span>
      <Button variant="secondary" controlSize="compact" icon={RefreshCw} loading={reloading} onClick={onReload}>Обновить проект</Button>
    </div>
  </InlineMessage>;
}
