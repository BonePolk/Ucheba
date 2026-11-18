import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';

export { AnimatePresence, motion };

export const EASE_OUT = [0.22, 1, 0.36, 1];
export const FAST_TRANSITION = { duration: 0.14, ease: EASE_OUT };
export const PANEL_TRANSITION = { duration: 0.19, ease: EASE_OUT };
export const LAYOUT_TRANSITION = { duration: 0.17, ease: EASE_OUT };

export function CollapseSection({ open, children, className }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={PANEL_TRANSITION}
          className={className ? `overflow-hidden ${className}` : 'overflow-hidden'}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function TabTransition({ children, transitionKey, className }) {
  return (
    <motion.div
      key={transitionKey}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={FAST_TRANSITION}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function PageTransition({ routeKey, children, className }) {
  return (
    <motion.div
      key={routeKey}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={FAST_TRANSITION}
      className={className}
    >
      {children}
    </motion.div>
  );
}

export function SlidePanel({ open, children, className }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.aside
          initial={{ opacity: 0, x: 14 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 14 }}
          transition={PANEL_TRANSITION}
          className={className}
        >
          {children}
        </motion.aside>
      )}
    </AnimatePresence>
  );
}

export function StageSwap({ stageKey, children, className }) {
  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={stageKey}
        initial={{ opacity: 0, y: 3 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -3 }}
        transition={{ duration: 0.13, ease: EASE_OUT }}
        className={className}
      >
        {children}
      </motion.div>
    </AnimatePresence>
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
  const str = typeof text === 'string' ? text : '';
  const lineCount = str ? str.split('\n').length : 0;
  const needsCollapse = str.length > threshold || lineCount > 7;

  if (!needsCollapse) {
    return <div className={className}>{children}</div>;
  }

  return (
    <div className={className}>
      <motion.div
        layout
        transition={PANEL_TRANSITION}
        className={expanded ? '' : `${collapsedHeight} overflow-hidden border-b border-dashed border-[#2A2A2A] pb-1`}
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
