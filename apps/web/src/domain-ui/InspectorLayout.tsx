import type { HTMLAttributes, ReactNode } from 'react';

const classes = (...values: Array<string | undefined>) => values.filter(Boolean).join(' ');

export function InspectorBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={classes('inspector-body', className)} {...props} />;
}

export function InspectorFooter({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <footer className={classes('inspector-footer', className)} {...props} />;
}

export function InspectorSettingRow({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return <div className={classes('inspector-setting-row', className)}><span>{label}</span>{children}</div>;
}
