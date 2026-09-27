import { useState } from 'react';
import { useIsMutating } from '@tanstack/react-query';
import { SegmentedControl } from '@green/ui';
import { CadIntakePanel } from '@/features/cad-intake';
import type { ProjectImportOptions } from '../useProjectImport';
import { DxfUploader, type DxfUploaderProps } from './DxfUploader';

interface Props extends DxfUploaderProps, ProjectImportOptions {
  initialSource?: string | null;
  projectStateVersion?: number;
  hasSource?: boolean;
}

export function ProjectImportChoices({
  projectId,
  routeKey,
  onNavigate,
  initialSource,
  projectStateVersion,
  hasSource,
  ...upload
}: Props) {
  const [source, setSource] = useState(
    initialSource === 'file' ? 'file' : 'cad',
  );
  const checking = useIsMutating({
    mutationKey: ['cad-intake-start', routeKey, projectId],
    exact: true,
  });
  const switchingDisabled = upload.loading || checking > 0;
  return (
    <div className="grid min-w-0 gap-4">
      <SegmentedControl
        label="Источник проекта"
        value={source}
        onChange={setSource}
        options={[
          {
            value: 'file',
            label: 'DXF или выпуск',
            disabled: switchingDisabled,
          },
          { value: 'cad', label: 'Комплект CAD', disabled: switchingDisabled },
        ]}
      />
      {source === 'cad' ? (
        <CadIntakePanel
          key={routeKey}
          projectId={projectId}
          projectStateVersion={projectStateVersion}
          hasSource={hasSource}
          routeKey={routeKey}
          onNavigate={onNavigate}
        />
      ) : (
        <DxfUploader {...upload} />
      )}
    </div>
  );
}
