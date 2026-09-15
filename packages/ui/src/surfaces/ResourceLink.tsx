import { ExternalLink, Link2 } from 'lucide-react';
import type { FC, ReactNode } from 'react';
import { Icon as IconContent, type IconNode } from '../foundations/Icon';
import { cx } from '../foundations/utils';
export interface ResourceLinkProps {
  title: ReactNode;
  meta?: ReactNode;
  href?: string;
  description?: ReactNode;
  icon?: IconNode;
  className?: string;
}
export const ResourceLink: FC<ResourceLinkProps> = ({
  title,
  meta,
  href,
  description,
  icon = Link2,
  className,
}) => {
  const content = (
    <>
      <span className="flex h-6 w-6 shrink-0 items-center justify-center text-neutral-700">
        <IconContent icon={icon} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-px">
        <strong className="truncate text-[13px] leading-[18px] font-semibold">
          {title}
        </strong>
        {!!meta && (
          <small className="truncate font-mono text-[10px] leading-[14px] text-neutral-500">
            {meta}
          </small>
        )}
        {!!description && (
          <span className="max-w-[42ch] text-xs leading-4 text-neutral-600">
            {description}
          </span>
        )}
      </span>
      {!!href && (
        <span className="flex h-8 w-8 shrink-0 items-center justify-center text-neutral-600">
          <IconContent icon={ExternalLink} />
        </span>
      )}
    </>
  );
  return href ? (
    <a
      className={cx(
        'flex min-h-12 min-w-0 items-center gap-3 border-b border-(--border) bg-(--surface) py-2 text-inherit no-underline hover:[&>span>strong]:text-blue-700',
        className,
      )}
      href={href}
      target="_blank"
      rel="noreferrer"
    >
      {content}
    </a>
  ) : (
    <div
      className={cx(
        'flex min-h-12 min-w-0 items-center gap-3 border-b border-(--border) bg-(--surface) py-2 text-inherit',
        className,
      )}
    >
      {content}
    </div>
  );
};
