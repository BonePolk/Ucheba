import { useEffect, useRef, useState } from 'react';

// Анимация уже полученного текста; не имитирует поток от модели.
export default function AgentExplanation({ text = '', animate = true, initialText = text }) {
  const [visible, setVisible] = useState(() => animate ? initialText : text);
  const current = useRef(visible);
  useEffect(() => {
    if (!animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      current.current = text;
      setVisible(text);
      return;
    }
    if (!text.startsWith(current.current)) current.current = '';
    const letters = Array.from(text);
    const start = Array.from(current.current).length;
    const started = performance.now();
    const duration = Math.min(1600, Math.max(180, (letters.length - start) * 16));
    let frame;
    function reveal(now) {
      const count = Math.min(letters.length, start + Math.ceil((now - started) / duration * (letters.length - start)));
      current.current = letters.slice(0, count).join('');
      setVisible(current.current);
      if (count < letters.length) frame = requestAnimationFrame(reveal);
    }
    frame = requestAnimationFrame(reveal);
    return () => cancelAnimationFrame(frame);
  }, [text, animate]);
  return <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{animate ? visible : text}</span>;
}
