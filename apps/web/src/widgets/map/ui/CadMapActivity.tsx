import { InlineMessage, Progress } from '@green/ui';
import type { CadRenderState } from '../model/cadSource';

export function CadMapActivity({ state }: { state?: CadRenderState }) {
  if (!state || state.status === 'ready') return null;
  return (
    <div className="rounded-card pointer-events-none absolute top-16 left-1/2 z-40 w-80 max-w-[calc(100%-2rem)] -translate-x-1/2 border border-neutral-200 bg-white p-3">
      {state.status === 'loading' ? (
        <Progress label="Открываем полный чертёж" />
      ) : (
        <InlineMessage tone="error">
          Не удалось открыть полный чертёж. {state.message}
        </InlineMessage>
      )}
    </div>
  );
}
