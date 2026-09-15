import type { FC } from 'react';
import { Button, Icon, Text } from '@green/ui';
import { FilePlus2, FolderOpen } from 'lucide-react';
interface ProjectListEmptyProps {
  onCreate: () => void;
}
export const ProjectListEmpty: FC<ProjectListEmptyProps> = ({ onCreate }) => (
  <div className="flex min-h-80 flex-col items-center justify-center gap-2 border-t border-neutral-200 text-center">
    <Icon icon={<FolderOpen />} size={24} />
    <Text variant="heading">Проектов пока нет</Text>
    <Text tone="muted" className="mb-2 max-w-95">
      Загрузите DXF, чтобы подготовить первый план озеленения.
    </Text>
    <Button variant="primary" startIcon={<FilePlus2 />} onClick={onCreate}>
      Загрузить DXF
    </Button>
  </div>
);
