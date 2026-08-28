import type { ExportArtifact } from '@green/api-client';
import type { LucideIcon } from 'lucide-react';
import { Button, StatusIndicator, type ButtonVariant } from '@green/ui';

export function ExportArtifactSection({
  title,
  description,
  artifact,
  icon,
  variant = 'secondary',
  loading = false,
  prepareLabel,
  downloadLabel,
  readyLabel,
  onPrepare,
  onDownload,
}: {
  title: string;
  description: string;
  artifact?: ExportArtifact;
  icon?: LucideIcon;
  variant?: ButtonVariant;
  loading?: boolean;
  prepareLabel: string;
  downloadLabel: string;
  readyLabel: string;
  onPrepare: () => void;
  onDownload: (artifact: ExportArtifact) => void;
}) {
  return (
    <section className="export-artifact-section">
      <header><h3>{title}</h3><p>{description}</p></header>
      {artifact ? (
        <>
          <StatusIndicator tone="success" label={readyLabel} value={artifact.filename} />
          <Button variant={variant} icon={icon} onClick={() => onDownload(artifact)}>{downloadLabel}</Button>
        </>
      ) : (
        <Button variant={variant} icon={icon} loading={loading} onClick={onPrepare}>{prepareLabel}</Button>
      )}
    </section>
  );
}
