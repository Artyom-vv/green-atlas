import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type CadTransferDecision } from '@green/api-client';

export function useCadTransferReview(transferId: string) {
  const [code, setCode] = useState('');
  const client = useQueryClient();
  const key = ['cad-transfer-review', transferId];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => api.getCadTransferReview(transferId, signal),
    retry: false,
    refetchInterval: (current) =>
      current.state.data?.status === 'awaiting_approval' && !current.state.error
        ? 5000
        : false,
  });
  const decision = useMutation({
    mutationFn: (choice: CadTransferDecision['decision']) => {
      if (!query.data) throw new Error('Перепроверьте передачу.');
      return api.decideCadTransfer(transferId, {
        decision: choice,
        confirmation_code: choice === 'approve' ? code : '00000000',
        manifest_sha256: query.data.manifest_sha256,
      });
    },
    onSuccess: async (result) => {
      await client.cancelQueries({ queryKey: key });
      client.setQueryData(key, (previous: typeof query.data) =>
        previous ? { ...previous, ...result } : previous,
      );
    },
  });
  return { code, setCode, query, decision };
}
