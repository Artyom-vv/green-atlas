import type { FC } from 'react';
import type { ReleasePackage } from '@green/api-client';
import {
  Download,
  FileArchive,
  FileCode2,
  FileSpreadsheet,
  Map,
} from 'lucide-react';
import { Button, Disclosure } from '@green/ui';

const artifactPresentation = {
  bundle: { label: 'Полный пакет', icon: FileArchive },
  dxf: { label: 'План в DXF', icon: Map },
  cad: { label: 'Чертёж DWG с подосновами', icon: Map },
  schedule: { label: 'Посадочная ведомость', icon: FileSpreadsheet },
  manifest: { label: 'Основания и пробелы данных', icon: FileCode2 },
  scene: { label: 'Снимок 3D-сцены', icon: FileCode2 },
  dendroplan: { label: 'Дендроплан', icon: Map },
} as const satisfies Record<
  NonNullable<ReleasePackage['artifacts']>[number]['kind'],
  { label: string; icon: typeof Map }
>;

function artifactSize(bytes: number) {
  const unit = bytes >= 1024 ** 3 ? 3 : bytes >= 1024 ** 2 ? 2 : 1;
  const value = Math.max(unit === 1 ? 1 : 0, bytes / 1024 ** unit);
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: unit === 1 ? 0 : 1 })} ${['', 'КБ', 'МБ', 'ГБ'][unit]}`;
}

export interface ReleaseDownloadsProps {
  artifacts: NonNullable<ReleasePackage['artifacts']>;
  onDownload: (path: string) => void;
}

export const ReleaseDownloads: FC<ReleaseDownloadsProps> = ({
  artifacts,
  onDownload,
}) => {
  const bundle = artifacts.find((artifact) => artifact.kind === 'bundle');
  return (
    <>
      {bundle && (
        <div className="grid gap-2">
          <Button
            variant="primary"
            startIcon={<Download />}
            onClick={() => onDownload(bundle.download_url)}
          >
            Скачать полный пакет
          </Button>
          <span className="text-center text-xs text-neutral-600 tabular-nums">
            ZIP, {artifactSize(bundle.size)}
          </span>
        </div>
      )}
      <Disclosure variant="plain" title="Отдельные файлы">
        {artifacts.some((artifact) => artifact.kind === 'cad') && (
          <p className="m-0 text-xs text-neutral-600">
            Распакуйте CAD-пакет и откройте planting-plan.dwg. Подосновы должны
            оставаться рядом с чертежом.
          </p>
        )}
        <section className="grid gap-1" aria-label="Файлы выпуска">
          {artifacts
            .filter((artifact) => artifact.kind !== 'bundle')
            .map((artifact) => {
              const { label, icon: ArtifactIcon } =
                artifactPresentation[artifact.kind];
              return (
                <Button
                  key={artifact.id}
                  variant="ghost"
                  startIcon={<ArtifactIcon />}
                  endIcon={<Download />}
                  className="justify-start"
                  onClick={() => onDownload(artifact.download_url)}
                >
                  {label}
                  <span className="text-xs font-normal tabular-nums">
                    {' '}
                    {artifactSize(artifact.size)}
                  </span>
                </Button>
              );
            })}
        </section>
      </Disclosure>
    </>
  );
};
