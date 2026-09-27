import { useEffect, useRef } from 'react';
import type { SourceObjectReviewItem } from '@green/api-client';
import { Button } from '@green/ui';

export function ObjectReviewQueue({
  items,
  index,
  total,
  offset,
  busy,
  onIndex,
  onPage,
}: {
  items: SourceObjectReviewItem[];
  index: number;
  total: number;
  offset: number;
  busy: boolean;
  onIndex: (index: number) => void;
  onPage: (direction: number) => void;
}) {
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    list.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    list.current
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView?.({ block: 'nearest' });
  }, [index]);
  return (
    <aside className="flex h-full min-h-0 flex-col border-r border-neutral-200 bg-white">
      <div className="flex shrink-0 justify-between px-3 py-2 text-xs text-neutral-500">
        <span>Очередь</span>
        <span>
          {total ? offset + index + 1 : 0} / {total}
        </span>
      </div>
      <div
        ref={list}
        role="listbox"
        aria-label="Линии для проверки"
        aria-activedescendant={
          items[index]
            ? `review-${items[index].route.replaceAll('/', '-')}`
            : undefined
        }
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset"
      >
        {items.map((item, i) => (
          <button
            key={item.route}
            role="option"
            id={`review-${item.route.replaceAll('/', '-')}`}
            aria-selected={i === index}
            tabIndex={-1}
            disabled={busy}
            onClick={() => onIndex(i)}
            className={`flex w-full items-center justify-between border-0 border-l-2 px-3 py-2 text-left text-xs ${i === index ? 'border-blue-600 bg-blue-50 text-blue-800' : 'border-transparent bg-white text-neutral-700 hover:bg-neutral-50'}`}
          >
            <span>{item.source.handle}</span>
            <span className="text-[10px] text-neutral-500">
              {item.interpretation === 'linear'
                ? 'Линия'
                : item.interpretation === 'reference'
                  ? 'Обозначение'
                  : item.entity_type === 'LINE'
                    ? 'Отрезок'
                    : 'Контур'}
            </span>
          </button>
        ))}
      </div>
      <div className="flex shrink-0 justify-between border-t border-neutral-200 p-1">
        <Button
          variant="ghost"
          controlSize="compact"
          disabled={busy || !offset}
          onClick={() => onPage(-1)}
        >
          Назад
        </Button>
        <Button
          variant="ghost"
          controlSize="compact"
          disabled={busy || offset + items.length >= total}
          onClick={() => onPage(1)}
        >
          Далее
        </Button>
      </div>
    </aside>
  );
}
