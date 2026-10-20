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
        variant="section"
        title={
          automatic
            ? 'Ручная корректировка замыканий'
            : `Замыкание контуров (${pending} на проверке)`
        }
      >
        <ul className="m-0 p-0">
          {proposals.map((proposal) => (
            <li
              key={proposal.id}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-neutral-200 py-3 last:border-b-0 max-[719px]:grid-cols-1"
            >
              <div className="min-w-0 flex-1 text-sm">
                <div className="font-medium wrap-anywhere">
                  {proposal.layer}
                </div>
                <div className="flex flex-wrap gap-x-4 text-xs leading-4 text-neutral-500">
                  <span className="font-mono tabular-nums">
                    {formatArea(proposal.area_m2)}
                  </span>
                  <span>
                    Разрыв{' '}
                    <span className="font-mono tabular-nums">
                      {formatGap(proposal.closure_gap_m)}
                    </span>
                  </span>
                </div>
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
                controlSize="default"
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
