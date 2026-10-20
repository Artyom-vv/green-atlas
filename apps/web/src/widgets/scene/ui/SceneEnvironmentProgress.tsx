import { useEffect, useState } from 'react';
import { RefreshCw, Trees } from 'lucide-react';
import { Button, Progress } from '@green/ui';
import { SceneNotice } from './SceneStatus';

export function SceneEnvironmentErrorMessage({ error }: { error?: string }) {
  const networkFailure =
    error &&
    /failed to fetch|fetch failed|load failed|network\s?error|network request failed|err_internet_disconnected/i.test(
      error,
    );
  return (
    <>
      {networkFailure
        ? 'Не удалось связаться с сервисом. Повторите попытку.'
        : error || 'Не удалось загрузить окружение. Повторите попытку.'}
    </>
  );
}

export function SceneEnvironmentFailure({
  retained,
  error,
  onRetry,
}: {
  retained: boolean;
  error?: string;
  onRetry: () => void;
}) {
  return (
    <SceneNotice
      title={
        retained
          ? 'Не удалось обновить окружение'
          : 'Не удалось подготовить окружение'
      }
      error
      action={
        <Button
          icon={RefreshCw}
          controlSize="compact"
          aria-label="Повторить подготовку окружения"
          onClick={onRetry}
        >
          Повторить
        </Button>
      }
    >
      <SceneEnvironmentErrorMessage error={error} />
      {retained && (
        <p className="mt-1 mb-0">
          Показано предыдущее окружение. Рендер доступен.
        </p>
      )}
    </SceneNotice>
  );
}

export function SceneEnvironmentActivity({
  applying,
  startedAt,
}: {
  applying: boolean;
  startedAt?: number;
}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (startedAt === undefined) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt]);
  const elapsed =
    startedAt === undefined
      ? undefined
      : Math.max(0, Math.floor((now - startedAt) / 1000));
  return (
    <div aria-live="off">
      <Progress
        label={applying ? 'Загрузка моделей' : 'Подготовка окружения'}
        valueLabel={elapsed === undefined ? undefined : `Прошло ${elapsed} с`}
      />
    </div>
  );
}

export function SceneEnvironmentProgress({
  applying,
  retained,
  startedAt,
}: {
  applying: boolean;
  retained: boolean;
  startedAt?: number;
}) {
  return (
    <SceneNotice title="Окружение сцены" icon={Trees} busy>
      <SceneEnvironmentActivity applying={applying} startedAt={startedAt} />
      {retained && <p className="mt-2 mb-0">Показано предыдущее окружение.</p>}
    </SceneNotice>
  );
}
