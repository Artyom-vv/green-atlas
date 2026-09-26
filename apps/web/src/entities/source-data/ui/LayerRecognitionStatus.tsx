import type { LayerRecognition } from '@green/api-client';
import { Button } from '@green/ui';

interface Props {
  recognition?: LayerRecognition;
  loading?: boolean;
  error?: boolean;
  retrying?: boolean;
  onRetry: () => void;
}

export function LayerRecognitionStatus({
  recognition,
  loading,
  error,
  retrying,
  onRetry,
}: Props) {
  const running = recognition?.status === 'running';
  const failed = error || recognition?.status === 'failed';
  const done = recognition?.processed_count ?? 0;
  const total = recognition?.total_count ?? 0;
  const model = recognition?.provider.includes('gpt-6-luna');
  return (
    <div
      className="grid gap-2 rounded-xl border border-neutral-200 bg-neutral-50 p-4"
      aria-busy={running || loading || retrying}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">
          {model ? 'Распознавание Luna' : 'Распознавание слоёв'}
        </span>
        {running && (
          <span className="text-xs text-neutral-600 tabular-nums">
            {done} из {total}
          </span>
        )}
        {failed && (
          <Button
            variant="secondary"
            controlSize="compact"
            disabled={retrying}
            onClick={onRetry}
          >
            {retrying ? 'Запускаем…' : 'Повторить'}
          </Button>
        )}
      </div>
      <p className="m-0 text-xs leading-5 text-neutral-600" role="status">
        {failed
          ? (recognition?.message ?? 'Предложения недоступны. Повторите запрос')
          : loading
            ? 'Получаем предложения…'
            : (recognition?.message ?? 'Предложения по названиям слоёв')}
      </p>
      {running && (
        <progress
          aria-label="Обработано слоёв"
          value={done}
          max={Math.max(1, total)}
          className="h-1.5 w-full accent-blue-600"
        />
      )}
    </div>
  );
}
