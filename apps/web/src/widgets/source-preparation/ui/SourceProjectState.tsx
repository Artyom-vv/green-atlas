import type { FC } from 'react';
import { InlineMessage, Progress } from '@green/ui';
import { AppHeader } from '@/shared/ui/AppHeader';
interface SourceProjectStateProps {
  loading?: boolean;
}
export const SourceProjectState: FC<SourceProjectStateProps> = ({
  loading,
}) => (
  <div className="flex h-dvh min-h-0 flex-col">
    <AppHeader />
    <main className="grid flex-1 place-items-center p-6">
      {loading ? (
        <Progress label="Загрузка проекта" />
      ) : (
        <InlineMessage tone="error">Проект не найден.</InlineMessage>
      )}
    </main>
  </div>
);
