/** Общий экран чтения проекта: задержка, повторное чтение и возврат к библиотеке */
import { useEffect, useState, type FC } from 'react';
import { Button, InlineMessage, Progress } from '@green/ui';
import { AppHeader } from '@/shared/ui/AppHeader';
import {
  PROJECT_READ_MESSAGES as messages,
  PROJECT_READ_NOTICE_DELAY_MS,
} from '../config/projectRead';

interface ProjectReadStateProps {
  status: 'loading' | 'error' | 'missing' | 'stopped';
  projectName?: string;
  loadingLabel?: string;
  errorMessage?: string;
  onRetry: () => void;
  onBack: () => void;
}

export const ProjectReadState: FC<ProjectReadStateProps> = ({
  status,
  projectName,
  loadingLabel = messages.loading,
  errorMessage = messages.failed,
  onRetry,
  onBack,
}) => {
  const [delayed, setDelayed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    setDelayed(false);
    if (status !== 'loading') return;
    const timer = setTimeout(
      () => setDelayed(true),
      PROJECT_READ_NOTICE_DELAY_MS,
    );
    return () => clearTimeout(timer);
  }, [status, attempt]);
  const retry = () => {
    setDelayed(false);
    setAttempt((previous) => previous + 1);
    onRetry();
  };
  return (
    <div className="flex h-dvh min-h-0 flex-col">
      <AppHeader projectName={projectName} />
      <main className="flex flex-1 items-center justify-center p-6">
        <div className="grid w-full max-w-xl gap-3">
          {status === 'loading' ? (
            <>
              <Progress label={loadingLabel} />
              {delayed && (
                <p role="status" className="m-0 text-sm text-neutral-600">
                  {messages.delayed}
                </p>
              )}
            </>
          ) : status === 'error' ? (
            <InlineMessage tone="error">{errorMessage}</InlineMessage>
          ) : (
            <p role="status" className="m-0 text-sm text-neutral-600">
              {status === 'missing' ? messages.missing : messages.stopped}
            </p>
          )}
          {(status !== 'loading' || delayed) && (
            <div className="flex flex-wrap gap-2">
              {status !== 'missing' && (
                <Button variant="secondary" onClick={retry}>
                  {messages.retry}
                </Button>
              )}
              <Button variant="ghost" onClick={onBack}>
                {messages.back}
              </Button>
            </div>
          )}
        </div>
      </main>
    </div>
  );
};
