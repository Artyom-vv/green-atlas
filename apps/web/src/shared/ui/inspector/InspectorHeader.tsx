import type { FC, ReactNode } from 'react';
import { PanelRightClose } from 'lucide-react';
import { IconButton, SurfaceHeader, Text } from '@green/ui';

export interface InspectorHeaderProps {
  title: ReactNode;
  meta?: ReactNode;
  action?: ReactNode;
  onClose?: () => void;
}

export const InspectorHeader: FC<InspectorHeaderProps> = ({
  title,
  meta,
  action,
  onClose,
}) => (
  <SurfaceHeader className="shrink-0 flex-wrap gap-2">
    <div className="min-w-0 flex-1 basis-32">
      <Text as="h2" variant="heading">
        {title}
      </Text>
      {meta ? (
        <Text as="p" variant="caption" className="mt-1">
          {meta}
        </Text>
      ) : null}
    </div>
    {action}
    {onClose ? (
      <IconButton
        icon={<PanelRightClose />}
        label="Свернуть боковую панель"
        variant="ghost"
        controlSize="compact"
        onClick={onClose}
      />
    ) : null}
  </SurfaceHeader>
);
