import type { FC } from 'react';
import { ArrowLeft, PackageCheck } from 'lucide-react';
import { Button, Icon } from '@green/ui';

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
    <Icon icon={<PackageCheck />} size={20} />
    <div className="grid min-w-0 grow basis-48 gap-1">
      <strong className="text-sm font-semibold">
        {showRelease ? 'Файлы проекта' : `План версии ${planVersion}`}
      </strong>
      <span className="text-neutral-600">
        Чертёж DXF, ведомость, дендроплан и 3D-сцена.
      </span>
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
