import { useId, useState } from 'react';
import { Button, Icon, IconButton, Tooltip } from '@green/ui';
import {
  ChevronDown,
  CircleAlert,
  Crosshair,
  LockKeyhole,
  TriangleAlert,
} from 'lucide-react';
import {
  RESULTS_ACTION_RAIL,
  RESULTS_CONTENT_INSET,
} from '@/shared/ui/results/resultsLayout';
import type { WorkspaceIssueGroup } from '../model/issueGroups';
import { CheckIssueRow } from './CheckIssueRow';

export interface CheckIssueGroupProps {
  group: WorkspaceIssueGroup;
  disabled?: boolean;
  onLocate: (ids: string[]) => void;
  onAssign: (ids: string[]) => void;
}

function GroupTitle({ group }: { group: WorkspaceIssueGroup }) {
  return (
    <span className="block text-xs leading-4 font-medium text-neutral-800">
      {group.title}
    </span>
  );
}
function GroupSummary({ group }: { group: WorkspaceIssueGroup }) {
  const plural = new Intl.PluralRules('ru-RU');
  const noun = (
    count: number,
    forms: { one: string; few: string; many: string },
  ) => {
    const form = plural.select(count);
    return form === 'one' ? forms.one : form === 'few' ? forms.few : forms.many;
  };
  const severity = group.hasError ? 'Ошибка' : 'Предупреждение';
  return (
    <span className="flex items-center gap-2 text-xs leading-4 font-normal text-neutral-600">
      <Tooltip content={severity}>
        <span
          className="inline-flex shrink-0"
          aria-hidden={!group.missing || undefined}
          role={group.missing ? 'img' : undefined}
          aria-label={group.missing ? severity : undefined}
          tabIndex={group.missing ? 0 : undefined}
        >
          <Icon
            icon={group.hasError ? <CircleAlert /> : <TriangleAlert />}
            size={16}
            className={group.hasError ? 'text-error' : 'text-warning-strong'}
          />
        </span>
      </Tooltip>
      <span className="min-w-0">
        <span className="font-mono tabular-nums">{group.items.length}</span>{' '}
        {noun(group.items.length, {
          one: 'замечание',
          few: 'замечания',
          many: 'замечаний',
        })}
        {group.ids.length > 0 && (
          <>
            , <span className="font-mono tabular-nums">{group.ids.length}</span>{' '}
            {noun(group.ids.length, {
              one: 'посадка',
              few: 'посадки',
              many: 'посадок',
            })}
          </>
        )}
      </span>
    </span>
  );
}

const checkActionRail = RESULTS_ACTION_RAIL;

export function CheckIssueGroup({
  group,
  disabled,
  onLocate,
  onAssign,
}: CheckIssueGroupProps) {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const severity = group.hasError ? 'Ошибка' : 'Предупреждение';
  const label = `${group.title}. ${severity}. Замечаний ${group.items.length}. Посадок ${group.ids.length}`;
  const header = (
    <>
      <span className={`grid min-w-0 gap-1 ${RESULTS_CONTENT_INSET} py-3`}>
        <GroupTitle group={group} />
        <GroupSummary group={group} />
      </span>
      <span aria-hidden="true" className={checkActionRail}>
        <span className="col-start-3 inline-flex h-8 w-8 items-center justify-center">
          {!group.missing && (
            <Icon
              icon={<ChevronDown />}
              size={16}
              className={`transition-transform ${expanded ? 'rotate-180' : ''}`}
            />
          )}
        </span>
      </span>
    </>
  );
  // Both cause types share map and disclosure lanes; a contextual action cannot move them.
  return (
    <section
      role="group"
      aria-label={group.title}
      className="border-b border-neutral-200"
    >
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center hover:bg-neutral-100">
        {group.missing ? (
          <div className="col-span-full row-start-1 grid grid-cols-subgrid items-center">
            {header}
          </div>
        ) : (
          <button
            type="button"
            aria-label={label}
            aria-expanded={expanded}
            aria-controls={contentId}
            onClick={() => setExpanded(!expanded)}
            className="col-span-full row-start-1 m-0 grid appearance-none grid-cols-subgrid items-center border-0 bg-transparent p-0 text-left text-xs text-neutral-800 focus-visible:rounded-sm focus-visible:outline-2 focus-visible:-outline-offset-1 focus-visible:outline-(--focus)"
          >
            {header}
          </button>
        )}
        <div
          className={`pointer-events-none relative col-start-2 row-start-1 ${checkActionRail}`}
        >
          <span className="pointer-events-auto col-start-1">
            {group.missing && group.ids.length > 0 && !group.locked && (
              <Button
                variant="secondary"
                controlSize="compact"
                disabled={disabled}
                onClick={() => onAssign(group.ids)}
              >
                Назначить виды
              </Button>
            )}
            {group.missing && group.locked && (
              <Tooltip content="Снимите закрепление перед назначением">
                <span
                  tabIndex={0}
                  aria-label="Закреплены. Снимите закрепление перед назначением"
                  className="inline-flex h-8 items-center gap-1 text-xs text-neutral-600"
                >
                  <Icon icon={<LockKeyhole />} size={12} />
                  Закреплены
                </span>
              </Tooltip>
            )}
          </span>
          <span className="pointer-events-auto col-start-2">
            {group.ids.length > 0 && (
              <IconButton
                variant="ghost"
                controlSize="compact"
                icon={Crosshair}
                label={`Показать на карте: ${group.title}, посадок: ${group.ids.length}`}
                onClick={() => onLocate(group.ids)}
              />
            )}
          </span>
          <span aria-hidden="true" className="col-start-3 h-8 w-8" />
        </div>
      </div>
      {!group.missing && (
        <div id={contentId} hidden={!expanded} inert={!expanded}>
          <CheckIssueDetails group={group} onLocate={onLocate} />
        </div>
      )}
    </section>
  );
}

function CheckIssueDetails({
  group,
  onLocate,
}: {
  group: WorkspaceIssueGroup;
  onLocate: CheckIssueGroupProps['onLocate'];
}) {
  const hasMetrics = group.items.some(
    (issue) => issue.actual != null || issue.required != null,
  );
  const columns = hasMetrics
    ? 'grid grid-cols-[minmax(0,1fr)_5rem_5rem] items-center gap-3'
    : 'grid grid-cols-1';
  const dataCell = `min-w-0 ${RESULTS_CONTENT_INSET} py-2`;
  return (
    <div
      role="table"
      aria-label={`Подробности: ${group.title}`}
      className="pb-3 text-xs"
    >
      <div
        role="row"
        className="grid grid-cols-[minmax(0,1fr)_auto] items-center text-neutral-500"
      >
        <div className={dataCell}>
          <div className={columns}>
            <span role="columnheader">Замечание</span>
            {hasMetrics && (
              <>
                <span role="columnheader" className="text-right">
                  Факт
                </span>
                <span role="columnheader" className="text-right">
                  Норма
                </span>
              </>
            )}
          </div>
        </div>
        <div aria-hidden="true" className={checkActionRail}>
          <span className="col-start-2 h-8 w-8" />
          <span className="col-start-3 h-8 w-8" />
        </div>
        <span role="columnheader" className="sr-only">
          На карте
        </span>
      </div>
      <div role="rowgroup">
        {group.items.map((issue, index) => (
          <CheckIssueRow
            key={issue.id ?? index}
            issue={issue}
            hasMetrics={hasMetrics}
            onLocate={onLocate}
          />
        ))}
      </div>
    </div>
  );
}
