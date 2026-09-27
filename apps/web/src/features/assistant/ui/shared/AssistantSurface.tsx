import { IconButton, Text, cx } from '@green/ui';
import { ArrowLeft, PanelLeftClose } from 'lucide-react';
import type { ComponentProps, FC, ReactNode } from 'react';

export interface AssistantSurfaceProps extends ComponentProps<'div'> {
  header: ReactNode;
  status?: ReactNode;
  footer?: ReactNode;
  bodyProps?: ComponentProps<'div'>;
}

/** The workbench owns width and visibility; this surface owns its scrollport. */
export const AssistantSurface: FC<AssistantSurfaceProps> = ({
  header,
  status,
  footer,
  bodyProps,
  children,
  className,
  ...props
}) => (
  <div
    className={cx('flex h-full min-h-0 min-w-0 flex-col bg-white', className)}
    {...props}
  >
    {header}
    {status}
    <div
      {...bodyProps}
      className={cx(
        'flex min-h-0 min-w-0 flex-1 flex-col gap-3 overflow-x-hidden overflow-y-auto overscroll-contain p-3',
        bodyProps?.className,
      )}
    >
      {children}
    </div>
    {footer}
  </div>
);

export interface AssistantHeaderProps {
  title: ReactNode;
  titleHint?: string;
  leading?: ReactNode;
  actions?: ReactNode;
  onBack?: () => void;
  backLabel?: string;
  onClose: () => void;
  closeLabel: string;
}

export const AssistantHeader: FC<AssistantHeaderProps> = ({
  title,
  titleHint,
  leading,
  actions,
  onBack,
  backLabel = 'Назад',
  onClose,
  closeLabel,
}) => (
  <header className="flex shrink-0 flex-wrap items-center gap-2 border-b border-neutral-200 p-2">
    {leading ??
      (onBack ? (
        <IconButton
          icon={<ArrowLeft />}
          variant="ghost"
          label={backLabel}
          onClick={onBack}
        />
      ) : null)}
    <Text
      as="h2"
      variant="heading"
      title={titleHint}
      className="min-w-24 flex-1 truncate"
    >
      {title}
    </Text>
    {actions}
    <IconButton
      icon={<PanelLeftClose />}
      variant="ghost"
      label={closeLabel}
      onClick={onClose}
    />
  </header>
);
