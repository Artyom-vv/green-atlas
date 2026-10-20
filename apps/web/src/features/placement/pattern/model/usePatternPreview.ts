/** Размещение: проверка сценария роста по черновику без изменения плана */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import type {
  PatternPreview,
  PatternPreviewRequest,
  Project,
} from '@green/api-client';
import { projectSourceIdentity } from '@/entities/project/model/useProjectWrite';
import { previewWorkspacePattern } from '@/features/workspace/api/workspacePreviews';

interface Options {
  projectId: string;
  project?: Project;
  tool: string;
  draftKey: string;
  getDraftKey: () => string;
  getYear: () => number | undefined;
  onAccepted: (preview: PatternPreview) => void;
}
const basis = (project?: Project) =>
  JSON.stringify([
    projectSourceIdentity(project),
    project?.plan?.version,
    project?.geometry_version,
    project?.state_version,
  ]);
interface Accepted {
  owner: string;
  basis: string;
  draftKey: string;
  data: PatternPreview;
}
interface Run {
  owner: string;
  basis: string;
  draftKey: string;
  controller: AbortController;
}
interface State {
  accepted?: Accepted;
  progress?: PatternPreview;
  variables?: PatternPreviewRequest;
  error?: unknown;
  isPending: boolean;
  phase?: 'checking';
  editing: boolean;
  submittedAt: number;
}
const idle: State = { isPending: false, editing: false, submittedAt: 0 };

export function usePatternPreview(options: Options) {
  const owner = JSON.stringify([
    options.projectId,
    options.tool,
    projectSourceIdentity(options.project),
  ]);
  const projectBasis = basis(options.project);
  const current = useRef({ ...options, owner, projectBasis });
  current.current = { ...options, owner, projectBasis };
  const running = useRef<Run | undefined>(undefined);
  const [state, setState] = useState<State>(idle);
  const retire = useCallback(() => {
    running.current?.controller.abort();
    running.current = undefined;
  }, []);
  const invalidate = useCallback(() => {
    retire();
    setState((value) => ({
      ...value,
      isPending: false,
      phase: undefined,
      editing: true,
      progress: undefined,
      error: undefined,
    }));
  }, [retire]);
  const reset = useCallback(() => {
    retire();
    setState(idle);
  }, [retire]);
  useEffect(() => retire, [retire]);
  useLayoutEffect(() => {
    const run = running.current;
    if (
      run &&
      (run.owner !== owner ||
        run.draftKey !== options.draftKey ||
        run.basis !== projectBasis)
    )
      invalidate();
  }, [owner, projectBasis, options.draftKey, invalidate]);

  const mutate = (request: PatternPreviewRequest) => {
    const captured = current.current;
    const draftKey = captured.getDraftKey();
    if (!captured.project?.plan) return;
    if (
      running.current?.draftKey === draftKey &&
      running.current.owner === captured.owner
    )
      return;
    retire();
    const run: Run = {
      owner: captured.owner,
      basis: captured.projectBasis,
      draftKey,
      controller: new AbortController(),
    };
    running.current = run;
    const isCurrent = () =>
      running.current === run &&
      !run.controller.signal.aborted &&
      current.current.owner === run.owner &&
      current.current.getDraftKey() === run.draftKey;
    setState((value) => ({
      ...value,
      variables: request,
      isPending: true,
      phase: 'checking',
      editing: true,
      error: undefined,
      progress: undefined,
      submittedAt: Date.now(),
    }));
    void (async () => {
      try {
        if (!isCurrent()) return;
        if (current.current.projectBasis !== captured.projectBasis) {
          invalidate();
          return;
        }
        const submitted = {
          ...request,
          scenario_horizon_year: captured.getYear() ?? 0,
          base_plan_version: captured.project!.plan!.version,
        };
        setState((value) => ({
          ...value,
          variables: submitted,
          phase: 'checking',
        }));
        const validResponse = () =>
          isCurrent() && current.current.projectBasis === run.basis;
        const data = await previewWorkspacePattern(
          captured.projectId,
          submitted,
          run.controller.signal,
          (progress) => {
            if (validResponse()) setState((value) => ({ ...value, progress }));
          },
        );
        if (!validResponse()) return;
        running.current = undefined;
        setState((value) => ({
          ...value,
          accepted: { owner: run.owner, basis: run.basis, draftKey, data },
          isPending: false,
          phase: undefined,
          editing: false,
          progress: undefined,
        }));
        current.current.onAccepted(data);
      } catch (error) {
        if (!isCurrent()) return;
        running.current = undefined;
        setState((value) => ({
          ...value,
          error,
          isPending: false,
          phase: undefined,
          editing: true,
        }));
      }
    })();
  };
  const data =
    state.accepted?.owner === owner && state.accepted.basis === projectBasis
      ? state.accepted.data
      : undefined;
  const isCurrent = () =>
    Boolean(
      data &&
      !running.current &&
      state.accepted?.draftKey === current.current.getDraftKey() &&
      state.accepted.basis === current.current.projectBasis,
    );
  return {
    ...state,
    data,
    isCurrent,
    editing: state.editing || !isCurrent(),
    mutate,
    reset,
    cancel: invalidate,
    edit: invalidate,
    invalidate,
  };
}
