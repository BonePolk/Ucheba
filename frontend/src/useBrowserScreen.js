import { useEffect, useState } from 'react';

export default function useBrowserScreen(enabled) {
  const [frame, setFrame] = useState(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (!enabled) return;
    const stream = new EventSource('/api/integrations/context/browser/stream');
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
    return () => stream.close();
  }, [enabled]);
  return { frame, error };
}
