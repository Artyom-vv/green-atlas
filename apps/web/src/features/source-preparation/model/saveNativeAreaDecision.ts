import { preparationApi } from '../api/preparationApi';

type Port = Pick<typeof preparationApi, 'decideNativeArea' | 'getProject'>;
type Decision = Parameters<Port['decideNativeArea']>[1];

/** A lost response is not proof that the decision was rejected. Read the exact
 * capture/proposal decision once; never repeat an uncertain write automatically. */
export async function saveNativeAreaDecision(
  projectId: string,
  input: Decision,
  stateVersion: number,
  port: Port = preparationApi,
) {
  try {
    return await port.decideNativeArea(projectId, input, {
      expectedStateVersion: stateVersion,
    });
  } catch (error) {
    try {
      const current = await port.getProject(projectId, false);
      const decision = current.source_file?.native_area_proposals?.find(
        (item) =>
          item.id === input.proposal_id &&
          item.proposal_sha256 === input.proposal_sha256,
      );
      if (
        current.id === projectId &&
        (current.state_version ?? 0) > stateVersion &&
        current.source_file?.content_sha256 === input.source_sha256 &&
        decision?.decision === input.decision
      )
        return current;
    } catch {
      /* Preserve the original failure when the read is unavailable. */
    }
    throw error;
  }
}
