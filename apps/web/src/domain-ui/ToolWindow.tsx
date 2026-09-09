import type { ReactNode } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { IconButton } from '@green/ui';
import './workspace-modules.css';
export function ToolWindow({ title, collapsed, onToggle, children }: { title: string; collapsed: boolean; onToggle: () => void; children: ReactNode }) {
  return <section className={`tool-window${collapsed ? ' is-collapsed' : ''}`} aria-label={title}><header><strong>{title}</strong><IconButton icon={collapsed ? ChevronDown : ChevronUp} label={collapsed ? 'Развернуть настройки инструмента' : 'Свернуть настройки инструмента'} variant="ghost" onClick={onToggle} aria-expanded={!collapsed} /></header><div className="tool-window__body" hidden={collapsed}>{children}</div></section>;
}
