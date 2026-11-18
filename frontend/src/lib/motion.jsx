import { useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';

export { AnimatePresence, motion, useReducedMotion };

export const EASE_OUT = [0.22, 1, 0.36, 1];
export const FAST_TRANSITION = { duration: 0.14, ease: EASE_OUT };
export const PANEL_TRANSITION = { duration: 0.19, ease: EASE_OUT };
export const LAYOUT_TRANSITION = { duration: 0.17, ease: EASE_OUT };
export const SPRING_SNAPPY = { type: 'spring', stiffness: 480, damping: 38, mass: 0.8 };

const INSTANT_TRANSITION = { duration: 0.01 };

export function CollapseSection({ open, children, className }) {
  const reduced = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          initial={reduced ? false : { height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
          transition={reduced ? INSTANT_TRANSITION : PANEL_TRANSITION}
          className={className ? `overflow-hidden ${className}` : 'overflow-hidden'}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function TabTransition({ children, transitionKey, className }) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      key={transitionKey}
      initial={reduced ? false : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? INSTANT_TRANSITION : FAST_TRANSITION}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function PageTransition({ routeKey, children, className }) {
  const reduced = useReducedMotion();
  return (
    <motion.div
      key={routeKey}
      initial={reduced ? false : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? INSTANT_TRANSITION : FAST_TRANSITION}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function SlidePanel({ open, children, className }) {
  const reduced = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.aside
          initial={reduced ? false : { opacity: 0, x: 14 }}
          animate={{ opacity: 1, x: 0 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, x: 14 }}
          transition={reduced ? INSTANT_TRANSITION : PANEL_TRANSITION}
          className={className}
        >
          {children}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

export function StageSwap({ stageKey, children, className }) {
  const reduced = useReducedMotion();
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={stageKey}
        initial={reduced ? false : { opacity: 0, y: 3 }}
        animate={{ opacity: 1, y: 0 }}
        exit={reduced ? { opacity: 0 } : { opacity: 0, y: -3 }}
        transition={reduced ? INSTANT_TRANSITION : { duration: 0.13, ease: EASE_OUT }}
        className={className}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}

export function LiveHorizon({ active = false, tone = 'accent', className = '' }) {
  return (
    <div
      aria-hidden="true"
      data-active={active ? 'true' : 'false'}
      data-tone={tone}
      className={className ? `u-horizon ${className}` : 'u-horizon'}
    />
  );
}

export function ExpandableText({
  children,
  text = '',
  threshold = 380,
  collapsedHeight = 'max-h-[156px]',
  className = '',
}) {
  const [expanded, setExpanded] = useState(false);
  const reduced = useReducedMotion();
  const str = typeof text === 'string' ? text : '';
  const lineCount = str ? str.split('\n').length : 0;
  const needsCollapse = str.length > threshold || lineCount > 7;

  if (!needsCollapse) {
    return <div className={className}>{children}</div>;
  }

  return (
    <div className={className}>
      <motion.div
        layout={!reduced}
        transition={reduced ? INSTANT_TRANSITION : PANEL_TRANSITION}
        className={expanded ? '' : `${collapsedHeight} overflow-hidden border-b border-dashed border-[#2A2A2A] pb-1 u-fade-collapse`}
      >
        {children}
      </motion.div>
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="mt-1.5 text-[11px] font-medium text-[#B98CE8] hover:text-[#DFC4F8] hover:underline cursor-pointer"
      >
        {expanded ? 'Свернуть' : 'Показать полностью'}
      </button>
    </div>
  );
}
