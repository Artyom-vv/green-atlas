import { useSyncExternalStore } from 'react';
import {
  localRuntimeAvailable,
  subscribeLocalRuntime,
} from '@green/api-client';

export function useLocalRuntime() {
  return useSyncExternalStore(subscribeLocalRuntime, localRuntimeAvailable);
}
