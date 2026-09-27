import { Disclosure, Icon, Text, cx } from '@green/ui';
import { Check, Circle, CircleAlert } from 'lucide-react';
import type { FC } from 'react';

export interface ActivityItem {
  id: string;
  title: string;
  detail?: string;
  tone: 'neutral' | 'success' | 'warning' | 'error';
}
export interface ActivityTimelineProps {
  items: ActivityItem[];
}
const iconTones = {
  neutral: 'bg-neutral-100 text-neutral-600',
  success: 'bg-green-100 text-green-700',
  warning: 'bg-warning-soft text-warning-strong',
  error: 'bg-error-soft text-error',
} as const;

export const ActivityTimeline: FC<ActivityTimelineProps> = ({ items }) => {
  if (!items.length) return null;
  return (
    <Disclosure
      variant="plain"
      title={
        <span className="inline-flex items-center gap-2">
          Ход работы{' '}
          <Text variant="caption" mono>
            {items.length}
          </Text>
        </span>
      }
    >
      <ol className="m-0 grid list-none gap-3 border-l border-neutral-200 py-2 pl-3">
        {items.map((item) => (
          <li
            key={item.id}
            data-tone={item.tone}
            className="grid grid-cols-[20px_minmax(0,1fr)] items-start gap-2"
          >
            <span
              className={cx(
                'grid size-5 shrink-0 place-items-center rounded-full',
                iconTones[item.tone],
              )}
            >
              <Icon
                size={12}
                icon={
                  item.tone === 'success' ? (
                    <Check />
                  ) : item.tone === 'error' || item.tone === 'warning' ? (
                    <CircleAlert />
                  ) : (
                    <Circle />
                  )
                }
              />
            </span>
            <div className="grid min-w-0 gap-1">
              <Text variant="label">{item.title}</Text>
              {!!item.detail && (
                <Text variant="caption" className="wrap-anywhere">
                  {item.detail}
                </Text>
              )}
            </div>
          </li>
        ))}
      </ol>
    </Disclosure>
  );
};
