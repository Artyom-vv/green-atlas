import type { LucideIcon } from 'lucide-react';

interface HeaderOperationIconProps {
  icon: LucideIcon;
  pending?: boolean;
}

/** Keep the action recognizable while its confirmed operation is running. */
export function HeaderOperationIcon({
  icon: Glyph,
  pending = false,
}: HeaderOperationIconProps) {
  return (
    <span
      data-slot="header-operation-icon"
      data-pending={pending || undefined}
      aria-hidden="true"
      className="inline-flex size-4 shrink-0 items-center justify-center"
    >
      <Glyph
        data-slot="header-operation-glyph"
        size={16}
        className={pending ? 'motion-safe:animate-pulse' : undefined}
      />
    </span>
  );
}
