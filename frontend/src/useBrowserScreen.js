import { useEffect, useState } from 'react';

export default function useBrowserScreen(enabled) {
  const [frame, setFrame] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled) return undefined;
    let stream = null;

    function connect() {
      if (stream || document.hidden) return;
      stream = new EventSource('/api/integrations/monocle/browser/stream');
      setError(false);
      stream.onmessage = (event) => {
        try {
          const next = JSON.parse(event.data);
          setFrame(next);
          setError(false);
        } catch {
          setError(true);
        }
      };
      stream.onerror = () => setError(true);
    }

    function disconnect() {
      if (!stream) return;
      stream.close();
      stream = null;
    }

    function handleVisibility() {
      if (document.hidden) disconnect();
      else connect();
    }

    connect();
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('pagehide', disconnect);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('pagehide', disconnect);
      disconnect();
    };
  }, [enabled]);
  return { frame, error };
}
