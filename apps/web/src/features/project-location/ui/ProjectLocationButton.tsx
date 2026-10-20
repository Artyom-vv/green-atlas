import { Button } from '@green/ui';
import { MapPin } from 'lucide-react';
import { useState } from 'react';
import { ProjectLocationDialog } from './ProjectLocationDialog';

export function ProjectLocationButton({
  projectId,
  onApplied,
}: {
  projectId: string;
  onApplied?: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="ghost"
        startIcon={<MapPin />}
        onClick={() => setOpen(true)}
      >
        Местоположение
      </Button>
      <ProjectLocationDialog
        projectId={projectId}
        open={open}
        onClose={() => setOpen(false)}
        onApplied={onApplied ?? (() => {})}
      />
    </>
  );
}
