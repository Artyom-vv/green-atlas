import type { FC, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Text } from '@green/ui';

export interface AppHeaderProps {
  projectName?: string;
  leading?: ReactNode;
  endActions?: ReactNode;
}

/** Navigation for document pages; the workbench has its own EditorHeader. */
export const AppHeader: FC<AppHeaderProps> = ({
  projectName,
  leading,
  endActions,
}) => (
  <header className="relative z-30 grid min-h-13 w-full shrink-0 grid-cols-[1fr_minmax(0,2fr)_1fr] items-center gap-3 border-b border-neutral-200 bg-white px-6 py-2">
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {leading === undefined ? (
        <Link
          className="text-sm text-blue-600 no-underline hover:underline"
          to="/projects"
        >
          Проекты
        </Link>
      ) : (
        leading
      )}
    </div>
    <Text
      as="strong"
      variant="label"
      className="min-w-0 truncate text-center text-sm"
    >
      {projectName ?? 'Новый проект озеленения'}
    </Text>
    <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
      {endActions}
    </div>
  </header>
);
