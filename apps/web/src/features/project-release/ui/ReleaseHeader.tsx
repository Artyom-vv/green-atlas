import type { FC } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@green/ui';

export interface ReleaseHeaderProps {
  planVersion: number;
  showRelease: boolean;
  hasFiles: boolean;
  loading?: boolean;
  onShowFiles: () => void;
}

export const ReleaseHeader: FC<ReleaseHeaderProps> = ({
  planVersion,
  showRelease,
  hasFiles,
  loading,
  onShowFiles,
}) => (
  <header className="flex flex-wrap items-center gap-3">
    <div className="grid min-w-0 grow basis-48 gap-1">
      <strong className="text-sm font-semibold">
        {showRelease ? 'Файлы проекта' : `План версии ${planVersion}`}
      </strong>
      <ul
        aria-label="Состав пакета"
        className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-neutral-600"
      >
        <li>Чертёж</li>
        <li>Ведомость</li>
        <li>Дендроплан</li>
        <li>3D-сцена</li>
      </ul>
    </div>
    {!showRelease && hasFiles && (
      <Button
        variant="ghost"
        startIcon={<ArrowLeft />}
        aria-label="Вернуться к файлам пакета"
        disabled={loading}
        onClick={onShowFiles}
      >
        К файлам
      </Button>
    )}
  </header>
);
