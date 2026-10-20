import { Button, Icon } from '@green/ui';
import { CircleAlert, LoaderCircle } from 'lucide-react';
import { PREPARATION_MESSAGES } from '../../config/sourceMessages';

type PassportLoadStateProps =
  { state: 'loading' } | { state: 'error'; onRetry: () => void };

/** Loading and recovery belong to the passport section and share its content axis. */
export function PassportLoadState(props: PassportLoadStateProps) {
  const loading = props.state === 'loading';
  return (
    <div className="min-w-0 border-b border-neutral-200">
      <div
        className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 text-sm leading-5 min-[720px]:px-6"
        role={loading ? 'status' : 'alert'}
      >
        <span className="inline-flex min-w-0 items-center gap-2">
          {loading ? (
            <span
              role="progressbar"
              aria-label={PREPARATION_MESSAGES.readingPassport}
            >
              <Icon
                icon={<LoaderCircle />}
                className="animate-spin text-neutral-500 motion-reduce:animate-none"
              />
            </span>
          ) : (
            <Icon icon={<CircleAlert />} className="text-(--error)" />
          )}
          <span className="min-w-0 text-neutral-600">
            {loading
              ? PREPARATION_MESSAGES.readingPassport
              : PREPARATION_MESSAGES.passportReadFailed}
          </span>
        </span>
        {props.state === 'error' && (
          <Button variant="ghost" onClick={props.onRetry}>
            Повторить
          </Button>
        )}
      </div>
    </div>
  );
}
