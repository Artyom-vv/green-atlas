import type { ComponentPropsWithRef, FC, ReactNode } from 'react';
import { Field, SurfaceBody, cx } from '@green/ui';

export interface InspectorBodyProps extends ComponentPropsWithRef<'div'> {
  children: ReactNode;
}

export const InspectorBody: FC<InspectorBodyProps> = ({
  className,
  children,
  ...props
}) => (
  <SurfaceBody
    className={cx('min-h-0 flex-1 overflow-auto', className)}
    {...props}
  >
    {children}
  </SurfaceBody>
);

export interface InspectorFooterProps extends ComponentPropsWithRef<'footer'> {
  children: ReactNode;
}

export const InspectorFooter: FC<InspectorFooterProps> = ({
  className,
  ...props
}) => (
  <footer
    className={cx(
      'flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-neutral-200 bg-white p-4',
      className,
    )}
    {...props}
  />
);

export interface InspectorSettingRowProps {
  label: ReactNode;
  children: ReactNode;
  className?: string;
}

export const InspectorSettingRow: FC<InspectorSettingRowProps> = (props) => (
  <Field {...props} />
);
