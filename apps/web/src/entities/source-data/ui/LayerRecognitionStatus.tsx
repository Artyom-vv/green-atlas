/** Видимый ход распознавания: серверные этапы, сохранение назначений и явное восстановление. */
import { Button, Disclosure, Progress } from '@green/ui';
import type { Layer } from '@green/api-client';
import type { ReactNode } from 'react';
import { useClock } from '@/shared/time/useClock';
import { CLOCK_INTERVAL_MS } from '@/shared/time/duration';
import { RECOGNITION_MESSAGES as messages } from '../config/sourceMessages';
import {
  recognitionProgress,
  recognitionTiming,
  recognitionTask,
  type RecognitionProgressInput,
} from '../model/recognitionProgress';

interface Props extends RecognitionProgressInput {
  layers?: Layer[];
  onRetry: () => void;
  onReview?: () => void;
  variant?: 'default' | 'popover';
  showCount?: boolean;
  reviewLabel?: string;
  children?: ReactNode;
}

export function LayerRecognitionStatus(props: Props) {
  const { recognition, retrying, onRetry, onReview, assignmentsSaved } = props;
  const { phase, done, total, primaryDone, value, busy } =
    recognitionProgress(props);
  const now = useClock(CLOCK_INTERVAL_MS, busy);
  const timing = recognitionTiming(recognition, now);
  const resumable = recognition?.status === 'failed' && primaryDone > 0;
  const recoverable = ['unknown', 'failed', 'not_started'].includes(phase);
  const checking = phase === 'checking';
  const title =
    phase === 'failed' && recognition?.message
      ? recognition.message.replace(/[.\s]+$/, '')
      : ['classifying', 'reviewing'].includes(phase) &&
          (recognition?.active_layer_ids?.length ?? 0) > 1 &&
          recognition?.provider !== 'local-rules/1'
        ? messages.waitingForModel
        : phase === 'completed' && assignmentsSaved
          ? messages.assignmentsSaved
          : phase === 'resuming' && resumable
            ? messages.resumeMessage
            : messages.phases[phase].title;
  const task = ['classifying', 'reviewing'].includes(phase)
    ? recognitionTask(title, recognition, props.layers)
    : title;
  const showCount =
    total > 0 &&
    ['classifying', 'reviewing', 'finishing', 'failed'].includes(phase);
  const count = showCount
    ? `${phase === 'finishing' ? `${messages.primaryCount}: ` : ''}${done} из ${total}`
    : undefined;
  const showProgress = busy && !checking;
  const retryLabel = checking
    ? messages.check
    : phase === 'resuming'
      ? resumable
        ? messages.resuming
        : messages.starting
      : phase === 'unknown'
        ? messages.checkAction
        : phase === 'not_started'
          ? messages.startAction
          : resumable
            ? messages.resumeAction
            : messages.retryAction;
  const hasTechnicalDetails =
    (phase === 'failed' && !!recognition?.message) ||
    (!!timing.elapsed && timing.elapsed !== '0 с');
  if (props.variant === 'popover')
    return (
      <section
        className="@container grid min-w-0 grid-cols-1 gap-3 text-sm leading-5"
        aria-label="Распознавание слоёв"
        aria-busy={busy}
      >
        <div role="status">
          {busy ? (
            <Progress
              label={task}
              value={value}
              valueLabel={props.showCount === false ? '' : (count ?? '')}
            />
          ) : (
            <p className="m-0 text-neutral-600">
              {phase === 'failed' ? messages.phases.failed.title : title}
            </p>
          )}
        </div>
        {props.children}
        {hasTechnicalDetails && (
          <Disclosure variant="plain" title={messages.details}>
            {timing.elapsed && timing.elapsed !== '0 с' && (
              <dl className="m-0 grid grid-cols-[1fr_auto] gap-4 text-neutral-600">
                <dt>{messages.duration}</dt>
                <dd className="m-0 tabular-nums">{timing.elapsed}</dd>
              </dl>
            )}
            {phase === 'failed' && recognition?.message && (
              <p className="m-0 mt-2 wrap-anywhere text-neutral-600">
                {recognition.message.replace(/[.\s]+$/, '')}
              </p>
            )}
          </Disclosure>
        )}
        <div className="mt-1 grid grid-cols-1 gap-2 empty:hidden @min-[17rem]:grid-cols-2 [&>*:only-child]:col-span-full">
          {recoverable && !busy && (
            <Button
              variant="secondary"
              disabled={retrying || checking}
              onClick={onRetry}
            >
              {retryLabel}
            </Button>
          )}
          {(phase === 'completed' || phase === 'failed') && onReview && (
            <Button variant="secondary" onClick={onReview}>
              {props.reviewLabel ?? messages.review}
            </Button>
          )}
        </div>
      </section>
    );
  return (
    <section
      className="grid min-w-0 gap-2"
      aria-label="Распознавание слоёв"
      aria-busy={busy}
    >
      <div role="status">
        {showProgress ? (
          <Progress label={task} value={value} valueLabel={count ?? ''} />
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-neutral-600">
            <span>{task}</span>
            {count && <span className="tabular-nums">{count}</span>}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 empty:hidden">
        {(busy || phase === 'completed' || phase === 'failed') &&
          timing.elapsed && (
            <span className="text-xs text-neutral-500 tabular-nums">
              {messages.elapsed} {timing.elapsed}
            </span>
          )}
        {(recoverable || checking || phase === 'resuming') && (
          <Button
            variant="secondary"
            controlSize="compact"
            disabled={retrying || checking}
            onClick={onRetry}
          >
            {retryLabel}
          </Button>
        )}
        {(phase === 'completed' || phase === 'failed') && onReview && (
          <Button variant="ghost" controlSize="compact" onClick={onReview}>
            {props.reviewLabel ?? messages.review}
          </Button>
        )}
      </div>
    </section>
  );
}
