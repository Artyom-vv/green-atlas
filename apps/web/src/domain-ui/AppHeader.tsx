import type { ReactNode } from 'react';
import { AlertTriangle, ArrowLeft, Download, Redo2, Undo2 } from 'lucide-react';
import { Button, IconButton } from '@green/ui';
import { Link } from 'react-router-dom';

type AppHeaderProps = {
  projectName?: string;
  workspace?: boolean;
  onReview?: () => void;
  onExport?: () => void;
  exporting?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
  undoLabel?: string | null;
  redoLabel?: string | null;
  historyBusy?: boolean;
  actionsDisabled?: boolean;
  backTo?: string;
  subtitle?: string;
  endActions?: ReactNode;
};

export function AppHeader({ projectName, workspace = false, onReview, onExport, exporting = false, onUndo, onRedo, undoLabel, redoLabel, historyBusy = false, actionsDisabled = false, backTo = '/projects', subtitle = 'План озеленения', endActions }: AppHeaderProps) {
  if (!workspace) {
    return (
      <header className="app-header app-header--flow">
        <div className="app-header__side"><Link className="header-link" to="/projects">Проекты</Link></div>
        <strong className="app-header__project">Новый проект озеленения</strong>
        <div className="app-header__side app-header__side--end"><span className="app-header__draft">Черновик</span></div>
      </header>
    );
  }

  return (
    <header className="app-header app-header--workspace">
      <div className="workspace-titlebar">
        <Link className="header-back" to={backTo} aria-label="Назад"><ArrowLeft size={16} /></Link>
        <div className="workspace-titlebar__name"><strong>{projectName ?? 'Новый проект'}</strong><span>{subtitle}</span></div>
      </div>
      <div className="app-header__actions">
        {endActions ?? <>
          <IconButton icon={Undo2} label={undoLabel ? `Отменить: ${undoLabel}` : 'Отменить'} variant="ghost" disabled={!onUndo || historyBusy || actionsDisabled} onClick={onUndo} />
          <IconButton icon={Redo2} label={redoLabel ? `Повторить: ${redoLabel}` : 'Повторить'} variant="ghost" disabled={!onRedo || historyBusy || actionsDisabled} onClick={onRedo} />
          <span className="header-divider" />
          {onReview ? <Button variant="secondary" icon={AlertTriangle} disabled={actionsDisabled} onClick={onReview}>Проверка</Button> : null}
          {onExport ? <Button variant="primary" icon={Download} loading={exporting} disabled={actionsDisabled} onClick={onExport}>Экспорт DXF</Button> : null}
        </>}
      </div>
    </header>
  );
}
