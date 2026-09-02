import type { ReactNode } from 'react';
import { PanelRightClose } from 'lucide-react';
import { IconButton } from '@green/ui';

export function InspectorHeader({ title, meta, action, onClose }: {
  title: ReactNode;
  meta?: ReactNode;
  action?: ReactNode;
  onClose: () => void;
}) {
  return <header className="inspector-header">
    <span className="inspector-header__copy">
      <h2>{title}</h2>
      {meta ? <small>{meta}</small> : null}
    </span>
    {action}
    <IconButton icon={PanelRightClose} label="Свернуть боковую панель" variant="ghost" controlSize="compact" onClick={onClose} />
  </header>;
}
