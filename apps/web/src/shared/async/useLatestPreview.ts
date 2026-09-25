import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

interface PreviewState<Request, Response> {
  ticket: number;
  scopeKey: string;
  data?: Response;
  progress?: Response;
  variables?: Request;
  error?: unknown;
  isPending: boolean;
  submittedAt: number;
}

export interface LatestPreviewOptions<Request, Response> {
  scopeKey?: string;
  execute: (
    request: Request,
    signal: AbortSignal,
    publish: (progress: Response) => void,
  ) => Promise<Response>;
  onAccepted?: (response: Response, request: Request) => void;
}

/** Read-only preview requests share cancellation semantics. Each invocation
 * captures its own ticket, including error/finally paths. Writes use a separate
 * controller because aborting a write does not prove that it was not committed. */
export function useLatestPreview<Request, Response>({
  execute,
  onAccepted,
  scopeKey = 'default',
}: LatestPreviewOptions<Request, Response>) {
  const generation = useRef(0);
  const currentScope = useRef(scopeKey);
  const controller = useRef<AbortController | undefined>(undefined);
  const [state, setState] = useState<PreviewState<Request, Response>>({
    ticket: -1,
    scopeKey,
    isPending: false,
    submittedAt: 0,
  });

  const invalidate = useCallback(() => {
    generation.current += 1;
    controller.current?.abort();
    controller.current = undefined;
  }, []);
  useEffect(() => invalidate, [invalidate]);
  useLayoutEffect(() => {
    if (currentScope.current !== scopeKey) invalidate();
    currentScope.current = scopeKey;
  }, [scopeKey, invalidate]);

  const reset = useCallback(() => {
    invalidate();
    // Repeated idle pointer cancellation still retires requests, but must not
    // publish a fresh empty object and rerender the whole editor each frame.
    setState((current) =>
      current.ticket === -1
        ? current
        : { ticket: -1, scopeKey: '', isPending: false, submittedAt: 0 },
    );
  }, [invalidate]);

  const mutate = useCallback(
    (request: Request) => {
      invalidate();
      const ticket = generation.current;
      const ownController = new AbortController();
      controller.current = ownController;
      const submittedAt = Date.now();
      setState({
        ticket,
        scopeKey,
        variables: request,
        isPending: true,
        submittedAt,
      });
      const isCurrent = () =>
        generation.current === ticket &&
        currentScope.current === scopeKey &&
        !ownController.signal.aborted;
      void Promise.resolve()
        .then(() =>
          execute(request, ownController.signal, (progress) => {
            if (!isCurrent()) return;
            setState({
              ticket,
              scopeKey,
              progress,
              variables: request,
              isPending: true,
              submittedAt,
            });
          }),
        )
        .then((data) => {
          if (!isCurrent()) return;
          onAccepted?.(data, request);
          if (!isCurrent()) return;
          setState({
            ticket,
            scopeKey,
            data,
            variables: request,
            isPending: false,
            submittedAt,
          });
        })
        .catch((error: unknown) => {
          if (!isCurrent()) return;
          setState((current) => ({
            ticket,
            scopeKey,
            error,
            progress: current.ticket === ticket ? current.progress : undefined,
            variables: request,
            isPending: false,
            submittedAt,
          }));
        });
    },
    [execute, invalidate, onAccepted, scopeKey],
  );

  const valid =
    state.scopeKey === scopeKey &&
    state.ticket === generation.current &&
    !controller.current?.signal.aborted;
  return {
    ...state,
    data: valid ? state.data : undefined,
    progress: valid ? state.progress : undefined,
    error: valid ? state.error : undefined,
    isPending: valid && state.isPending,
    mutate,
    reset,
    cancel: reset,
    invalidate,
  };
}
