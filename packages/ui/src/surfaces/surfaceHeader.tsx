import type { ReactNode } from 'react';
import { SurfaceActions, SurfaceHeader } from './SurfaceParts';
export interface HeaderMeta {
  header?: ReactNode;
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}

export function defaultHeader({ title, description, actions }: HeaderMeta) {
  if (title === undefined && description === undefined && actions === undefined)
    return null;
  return (
    <SurfaceHeader>
      <div className="min-w-0">
        {title !== undefined && (
          <h2 className="m-0 text-sm leading-[18px] font-semibold">{title}</h2>
        )}
        {description !== undefined && (
          <p className="m-0 mt-0.5 text-xs leading-4 text-neutral-500">
            {description}
          </p>
        )}
      </div>
      {actions !== undefined && <SurfaceActions>{actions}</SurfaceActions>}
    </SurfaceHeader>
  );
}
