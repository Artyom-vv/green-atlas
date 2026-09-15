import { Button, Toolbar } from '@green/ui';
import type { FC } from 'react';

const explorerTabs = [
  { id: 'project', label: 'Проект' },
  { id: 'layers', label: 'Слои' },
] as const;

export type ExplorerTab = (typeof explorerTabs)[number]['id'];

interface ExplorerNavigationProps {
  tab: ExplorerTab;
  onChange: (tab: ExplorerTab) => void;
}

export const ExplorerNavigation: FC<ExplorerNavigationProps> = ({
  tab,
  onChange,
}) => (
  <Toolbar label="Обозреватель проекта" className="rounded-none border-0 p-0">
    {explorerTabs.map((item) => (
      <Button
        key={item.id}
        variant="ghost"
        controlSize="compact"
        className="px-2 text-xs aria-pressed:bg-blue-100 aria-pressed:text-blue-700"
        aria-pressed={tab === item.id}
        onClick={() => onChange(item.id)}
      >
        {item.label}
      </Button>
    ))}
  </Toolbar>
);
