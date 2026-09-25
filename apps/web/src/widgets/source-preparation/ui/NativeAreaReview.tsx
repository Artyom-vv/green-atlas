import { useState } from 'react';
import { Button, Disclosure } from '@green/ui';
import { NativeAreaPreviewDialog } from './NativeAreaPreviewDialog';

import {
  formatArea,
  formatGap,
  type AreaProposal,
} from './nativeAreaPresentation';
interface Props {
  projectId: string;
  sourceSha256: string;
  proposals: AreaProposal[];
  readOnly: boolean;
  busy: boolean;
  automatic?: boolean;
  saving?: boolean;
  error?: string;
  onRefresh?: () => void;
  onDecision: (
    proposal: AreaProposal,
    decision: 'accepted' | 'rejected',
  ) => void;
}

export function NativeAreaReview({
  projectId,
  sourceSha256,
  proposals,
  readOnly,
  busy,
  automatic = false,
  saving,
  error,
  onRefresh,
  onDecision,
}: Props) {
  const [selectedId, setSelectedId] = useState<string>();
  const selected = proposals.find((proposal) => proposal.id === selectedId);
  if (!proposals.length) return null;
  const pending = proposals.filter(
    (proposal) => proposal.decision === 'pending',
  ).length;
  return (
    <>
      <Disclosure
        variant="plain"
        title={automatic ? 'Ручная корректировка замыканий' : `Замыкание контуров (${pending} на проверке)`}
      >
        <ul className="m-0 space-y-2 p-0">
          {proposals.map((proposal) => (
            <li
              key={proposal.id}
              className="flex items-center gap-3 rounded border border-neutral-200 p-3"
            >
              <div className="min-w-0 flex-1 text-sm">
                <div className="font-medium wrap-anywhere">
                  {proposal.layer}
                </div>
                <div>Площадь {formatArea(proposal.area_m2)}</div>
                <div>Разрыв {formatGap(proposal.closure_gap_m)}</div>
                {proposal.decision !== 'pending' && (
                  <div
                    className={
                      proposal.decision === 'accepted'
                        ? 'text-green-700'
                        : 'text-neutral-600'
                    }
                  >
                    {proposal.decision === 'accepted'
                      ? 'Замыкание принято'
                      : 'Замыкание отклонено'}
                  </div>
                )}
              </div>
              <Button
                variant="secondary"
                onClick={() => setSelectedId(proposal.id)}
              >
                Показать контур
              </Button>
            </li>
          ))}
        </ul>
      </Disclosure>
      {selected && (
        <NativeAreaPreviewDialog
          projectId={projectId}
          sourceSha256={sourceSha256}
          proposal={selected}
          readOnly={readOnly}
          busy={busy}
          saving={saving}
          error={error}
          onRefresh={onRefresh}
          onClose={() => setSelectedId(undefined)}
          onDecision={onDecision}
        />
      )}
    </>
  );
}
