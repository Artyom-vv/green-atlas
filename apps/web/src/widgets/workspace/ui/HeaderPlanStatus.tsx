import {
  Button,
  Icon,
  IconButton,
  Popover,
  PopoverDescription,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
  Tooltip,
} from '@green/ui';
import {
  CircleCheck,
  CircleHelp,
  FileWarning,
  PencilLine,
  RotateCw,
  Save,
  ScanSearch,
} from 'lucide-react';
import { HeaderOperationIcon } from './HeaderOperationIcon';

export type HeaderPersistenceState =
  'saved' | 'draft' | 'writing' | 'unknown' | 'recovery';

interface HeaderPlanStatusProps {
  state: HeaderPersistenceState;
  label: string;
  writeConfirmed: boolean;
  readIssue?: string;
  reading: boolean;
  onRetry: () => void;
  quiet?: boolean;
  activityLabel?: string;
}

/** Opening details never applies a draft, starts a write, or repeats a command. */
export function HeaderPlanStatus({
  state,
  label,
  writeConfirmed,
  readIssue,
  reading,
  onRetry,
  quiet = false,
  activityLabel,
}: HeaderPlanStatusProps) {
  const pending = state === 'writing' || reading || Boolean(activityLabel);
  const detail = [
    activityLabel ?? label,
    readIssue,
    reading ? 'Читаем текущую версию проекта' : undefined,
  ]
    .filter(Boolean)
    .join('. ');
  const triggerLabel = pending
    ? `${detail}${/[.!?…]$/u.test(detail) ? '' : '.'} Открыть сведения о состоянии проекта`
    : `Открыть сведения о состоянии проекта. ${detail}`;
  const glyph =
    activityLabel && state !== 'unknown'
      ? ScanSearch
      : reading
        ? RotateCw
        : state === 'writing'
          ? Save
          : readIssue || state === 'recovery'
            ? FileWarning
            : state === 'unknown'
              ? CircleHelp
              : state === 'draft'
                ? PencilLine
                : CircleCheck;
  const icon = <HeaderOperationIcon icon={glyph} pending={pending && !quiet} />;
  const tone =
    pending && !quiet
      ? 'text-blue-600'
      : readIssue || state === 'recovery'
        ? 'text-error'
        : state === 'unknown' || state === 'draft'
          ? 'text-warning'
          : 'text-neutral-600';

  if (state === 'saved' && !readIssue && !reading && !activityLabel)
    return (
      <Tooltip content={label}>
        <span
          data-slot="plan-status"
          role="status"
          aria-label={label}
          tabIndex={0}
          className="rounded-control inline-flex size-8 shrink-0 items-center justify-center text-neutral-600 outline-none focus-visible:ring-2 focus-visible:ring-(--focus)"
        >
          <Icon icon={icon} />
          <span className="sr-only">{label}</span>
        </span>
      </Tooltip>
    );

  return (
    <Popover>
      <span
        role="status"
        aria-live={quiet ? 'off' : 'polite'}
        aria-atomic="true"
        className="sr-only"
      >
        {detail}
      </span>
      <span className="inline-flex" aria-busy={pending || undefined}>
        <PopoverTrigger
          render={
            <IconButton
              icon={icon}
              label={triggerLabel}
              variant="ghost"
              controlSize="compact"
              className={tone}
            />
          }
        />
      </span>
      <PopoverPopup size="compact" align="end" sideOffset={8}>
        <PopoverTitle className="text-sm font-semibold">
          {readIssue
            ? writeConfirmed
              ? 'Запись подтверждена'
              : 'Ошибка чтения проекта'
            : state === 'recovery'
              ? 'Нужно обновить данные'
              : 'Состояние плана'}
        </PopoverTitle>
        <PopoverDescription className="mt-2 text-sm text-neutral-600">
          {label}
        </PopoverDescription>
        {readIssue && (
          <div className="mt-3 border-t border-neutral-200 pt-3">
            <p role="alert" className="text-error text-sm">
              {readIssue}
            </p>
            <Button
              variant="secondary"
              controlSize="compact"
              className="mt-3"
              startIcon={<RotateCw />}
              loading={reading}
              loadingIndicator={false}
              onClick={onRetry}
            >
              Повторить чтение
            </Button>
          </div>
        )}
      </PopoverPopup>
    </Popover>
  );
}
