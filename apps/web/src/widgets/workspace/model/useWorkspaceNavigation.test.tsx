import { useState, type FC } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { afterEach, expect, it } from 'vitest';
import { manualWorkspaceWork } from '@/features/workspace/manualWorkspaceWork';
import { LeaveWorkspaceDialog } from '@/widgets/workbench/ui/dialogs/LeaveWorkspaceDialog';
import { useWorkspaceNavigation } from './useWorkspaceNavigation';

afterEach(cleanup);

it('blocks navigation and discard after the draft is committed until recovery finishes', async () => {
  let finishRecovery: () => void = () => undefined;
  const Workspace: FC = () => {
    const [recoveryPending, setRecoveryPending] = useState(true);
    finishRecovery = () => setRecoveryPending(false);
    const work = manualWorkspaceWork({
      tool: 'select',
      placementPanelOpen: false,
      hasPlan: true,
      hasPreview: false,
      hasPendingZone: false,
      initialZoneDrafts: 0,
      brushStrokes: 0,
      brushDrawing: false,
      hasRowAxis: false,
      rowDrawingPoints: 0,
      placementAreaDrawing: false,
      zoneDrawing: false,
      mutationPending: false,
      brushPreviewPending: false,
      createPlanPending: false,
      releasePending: false,
      recoveryPending,
    });
    const blocker = useWorkspaceNavigation({
      projectId: 'a',
      blocksLeaving: work.blocksLeaving,
      hasUnsavedWork: work.hasDraft,
      assistantWritePending: false,
      hasAssistantProposal: false,
    });
    return (
      <LeaveWorkspaceDialog
        open={blocker.state === 'blocked'}
        pending={work.pending}
        onStay={() => blocker.reset?.()}
        onLeave={() => blocker.proceed?.()}
      />
    );
  };
  const router = createMemoryRouter(
    [
      { path: '/projects/a/workspace', element: <Workspace /> },
      { path: '/projects', element: <p>Проекты</p> },
    ],
    { initialEntries: ['/projects/a/workspace'] },
  );
  render(<RouterProvider router={router} />);
  await act(async () => router.navigate('/projects'));
  expect(router.state.location.pathname).toBe('/projects/a/workspace');
  expect(
    screen.getByRole('dialog', { name: 'Дождитесь завершения операции' }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole('button', { name: 'Уйти без применения' }),
  ).not.toBeInTheDocument();
  const reload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(reload);
  expect(reload.defaultPrevented).toBe(true);

  act(() => finishRecovery());
  const recoveredReload = new Event('beforeunload', { cancelable: true });
  window.dispatchEvent(recoveredReload);
  expect(recoveredReload.defaultPrevented).toBe(false);
  expect(
    screen.getByRole('button', { name: 'Уйти без применения' }),
  ).toBeEnabled();
  router.dispose();
});
