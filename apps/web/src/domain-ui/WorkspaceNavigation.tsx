import { AlertTriangle, Leaf, MapPinned } from 'lucide-react';

export type WorkspaceDestination = 'zones' | 'plantings' | 'issues';

type WorkspaceNavigationProps = {
  active: WorkspaceDestination;
  onChange: (destination: WorkspaceDestination) => void;
  issueCount?: number;
  hasPlan?: boolean;
};

const destinations: Array<{ id: WorkspaceDestination; label: string; icon: typeof MapPinned }> = [
  { id: 'zones', label: 'Участки', icon: MapPinned },
  { id: 'plantings', label: 'Посадки', icon: Leaf },
  { id: 'issues', label: 'Проверка', icon: AlertTriangle },
];

/** Always-visible inspector navigation. It is deliberately a tab bar, not a second back-stack. */
export function WorkspaceNavigation({ active, onChange, issueCount = 0, hasPlan = true }: WorkspaceNavigationProps) {
  return (
    <nav className="workspace-nav" aria-label="Разделы рабочего пространства">
      {destinations.map(({ id, label, icon: Icon }) => {
        const disabled = !hasPlan && id !== 'zones';
        return (
          <button
            key={id}
            className={`workspace-nav__item ${active === id ? 'is-active' : ''}`}
            type="button"
            aria-current={active === id ? 'page' : undefined}
            aria-label={label}
            disabled={disabled}
            onClick={() => onChange(id)}
          >
            <Icon size={16} aria-hidden="true" />
            <span>{label}</span>
            {id === 'issues' && issueCount > 0 ? <b aria-label={`${issueCount} замечаний`}>{issueCount}</b> : null}
          </button>
        );
      })}
    </nav>
  );
}
