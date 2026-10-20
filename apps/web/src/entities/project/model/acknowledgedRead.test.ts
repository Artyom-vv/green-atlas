import { QueryClient } from '@tanstack/react-query';
import type { Project } from '@green/api-client';
import { expect, it } from 'vitest';
import {
  acknowledgeProjectWrite,
  validateAcknowledgedProjectRead,
} from './acknowledgedRead';

it('rejects a read older than either acknowledged revision and never lowers the floor', () => {
  const client = new QueryClient();
  acknowledgeProjectWrite(client, 'saved', { stateVersion: 7, planVersion: 5 });
  acknowledgeProjectWrite(client, 'saved', { stateVersion: 4, planVersion: 3 });
  const project = {
    id: 'saved',
    state_version: 7,
    plan: { version: 5 },
  } as Project;
  expect(validateAcknowledgedProjectRead(client, 'saved', project)).toBe(
    project,
  );
  expect(() =>
    validateAcknowledgedProjectRead(client, 'saved', {
      ...project,
      state_version: 6,
    }),
  ).toThrow('старше');
  expect(() =>
    validateAcknowledgedProjectRead(client, 'saved', {
      ...project,
      plan: { ...project.plan!, version: 4 },
    }),
  ).toThrow('старше');
  client.clear();
});
