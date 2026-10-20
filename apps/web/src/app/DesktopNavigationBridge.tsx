import { useEffect, useLayoutEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { desktopBridge } from '@/shared/desktop/desktopBridge';

export function DesktopNavigationBridge() {
  const { pathname } = useLocation();
  useLayoutEffect(() => {
    if (!/^\/projects\/[^/]+\/(?:workspace|setup)(?:\/|$)/.test(pathname))
      desktopBridge.updateState({
        hasDraft: false,
        writeOutcome: 'idle',
        readRecovery: false,
        editingBlocked: false,
      });
  }, [pathname]);
  useEffect(() => desktopBridge.start(), []);
  return null;
}
