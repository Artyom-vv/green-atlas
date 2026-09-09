import type { ReactNode } from 'react';
import { ArrowLeft, Map } from 'lucide-react';
import { IconButton } from '@green/ui';

export function EditorHeader({ name, onBack, children }: { name: string; onBack: () => void; children: ReactNode }) {
  return <header className="editor-header"><div className="editor-header__project"><IconButton icon={ArrowLeft} label="К списку проектов" variant="ghost" controlSize="compact" onClick={onBack} /><Map size={17} aria-hidden="true" /><h1 title={name}>{name}</h1></div><div className="editor-header__actions">{children}</div></header>;
}
