import type { MapFocusResult } from '@/entities/editor/model/mapFocus';
import type { AgentMapControl } from '@/features/assistant/model/autonomous/autonomousControl';
import { controlFixture, controlGeometry } from '@/test/controlFixture';
import type { MapViewportHandle } from '@/widgets/map/model/mapContracts';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useAssistantMapBridge } from './useAssistantMapBridge';

describe('committed workspace map bridge', () => {
  it('rejects a late result after switching projects while old query data remains', async () => {
    const { project, command } = controlFixture();
    let adapter: AgentMapControl | undefined;
    let complete: ((result: MapFocusResult) => void) | undefined;
    const unregister = vi.fn();
    const registerMapControl = vi.fn((next: AgentMapControl) => {
      adapter = next;
      return unregister;
    });
    const focusGeometry = vi.fn(
      () =>
        new Promise<MapFocusResult>((resolve) => {
          complete = resolve;
        }),
    );
    const mapViewportRef = { current: { focusGeometry } };
    const { rerender } = renderHook(
      ({ projectId }) =>
        useAssistantMapBridge({
          projectId,
          project,
          sceneOpen: false,
          mapViewportRef,
          registerMapControl,
        }),
      { initialProps: { projectId: command.project_id } },
    );
    const operation = adapter!(
      command,
      controlGeometry,
      new AbortController().signal,
    );
    rerender({ projectId: 'next-project' });
    await act(async () => complete!({ status: 'completed' }));
    expect(await operation).toEqual({
      status: 'failed',
      error_code: 'CONTROL_STALE',
    });
    expect(unregister).toHaveBeenCalledOnce();
    expect(focusGeometry).toHaveBeenCalledOnce();
  });

  it('cancels the active camera operation when the scene replaces the map', async () => {
    const { project, command } = controlFixture();
    let adapter: AgentMapControl | undefined;
    let cameraSignal: AbortSignal | undefined;
    const registerMapControl = (next: AgentMapControl) => {
      adapter = next;
      return vi.fn();
    };
    const focusGeometry = vi.fn<MapViewportHandle['focusGeometry']>(
      (_geometry, _revision, signal) => {
        cameraSignal = signal;
        return new Promise<MapFocusResult>((resolve) =>
          signal.addEventListener(
            'abort',
            () =>
              resolve({ status: 'cancelled', error_code: 'FOCUS_INTERRUPTED' }),
            { once: true },
          ),
        );
      },
    );
    const mapViewportRef = { current: { focusGeometry } };
    const { rerender } = renderHook(
      ({ sceneOpen }) =>
        useAssistantMapBridge({
          projectId: command.project_id,
          project,
          sceneOpen,
          mapViewportRef,
          registerMapControl,
        }),
      { initialProps: { sceneOpen: false } },
    );
    const operation = adapter!(
      command,
      controlGeometry,
      new AbortController().signal,
    );
    rerender({ sceneOpen: true });
    expect(cameraSignal?.aborted).toBe(true);
    expect(await operation).toEqual({
      status: 'cancelled',
      error_code: 'FOCUS_INTERRUPTED',
    });
  });
});
