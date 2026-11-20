import { useEffect } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { EASE_OUT, FAST_TRANSITION, motion, PANEL_TRANSITION, useReducedMotion } from '@/lib/motion';

const sizeMap = {
  sm: 'max-w-[380px]',
  md: 'max-w-[520px]',
  lg: 'max-w-[720px]',
  xl: 'max-w-[1040px]',
};

const INSTANT_TRANSITION = { duration: 0.01 };

function clearStuckBodyPointerLock() {
  if (typeof document === 'undefined') return;
  if (!document.querySelector('[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"][data-state="open"]')) {
    if (document.body.style.pointerEvents === 'none') {
      document.body.style.pointerEvents = '';
    }
  }
}

export function Modal({ opened, open, onClose, title, description, size = 'md', children, className }) {
  const isOpen = Boolean(opened ?? open);
  const reduced = useReducedMotion();

  useEffect(() => {
    if (isOpen) {
      return () => {
        requestAnimationFrame(clearStuckBodyPointerLock);
      };
    }
    const id = requestAnimationFrame(clearStuckBodyPointerLock);
    return () => cancelAnimationFrame(id);
  }, [isOpen]);

  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(next) => { if (!next) onClose?.(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay asChild>
          <motion.div
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={reduced ? INSTANT_TRANSITION : FAST_TRANSITION}
            className="fixed inset-0 z-50 bg-black/75"
          />
        </DialogPrimitive.Overlay>
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 max-md:items-end max-md:p-0 pointer-events-none">
          <DialogPrimitive.Content
            asChild
            {...(!description ? { 'aria-describedby': undefined } : {})}
          >
            <motion.div
              initial={reduced ? false : { opacity: 0, y: 8, scale: 0.985 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={reduced ? INSTANT_TRANSITION : { duration: 0.16, ease: EASE_OUT }}
              className={cn(
                'pointer-events-auto w-full rounded-none bg-[#111111] border border-[#303030] text-[#EAEAEA] flex flex-col max-h-[calc(100dvh-24px)] max-md:max-w-none max-md:max-h-[92dvh] max-md:border-x-0 max-md:border-b-0 focus:outline-none',
                sizeMap[size] ?? sizeMap.md,
                className,
              )}
            >
              <div className="flex items-center justify-between gap-2 px-4 py-2.5 max-md:px-3.5 max-md:py-3 border-b border-[#222222] bg-[#141414] shrink-0">
                <DialogPrimitive.Title className="text-[14px] font-semibold text-[#EAEAEA] m-0 truncate">
                  {title}
                </DialogPrimitive.Title>
                <DialogPrimitive.Close asChild>
                  <button
                    type="button"
                    aria-label="Закрыть"
                    className="h-6 w-6 max-md:h-9 max-md:w-9 grid place-items-center text-[#929292] hover:text-[#EAEAEA] hover:bg-[#222222] cursor-pointer shrink-0"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </DialogPrimitive.Close>
              </div>
              {description && (
                <DialogPrimitive.Description className="px-4 pt-2 text-[12px] text-[#929292] m-0">
                  {description}
                </DialogPrimitive.Description>
              )}
              <div className="p-4 max-md:p-3.5 max-md:pb-[calc(1rem+env(safe-area-inset-bottom))] overflow-y-auto [overflow-wrap:anywhere] min-w-0">
                {children}
              </div>
            </motion.div>
          </DialogPrimitive.Content>
        </div>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

const drawerAxis = {
  right: { initial: { x: '100%' }, animate: { x: 0 } },
  left: { initial: { x: '-100%' }, animate: { x: 0 } },
  bottom: { initial: { y: '100%' }, animate: { y: 0 } },
};

export function Drawer({ opened, open, onClose, title, side = 'right', widthClass = 'w-[440px] max-w-[100vw]', children }) {
  const isOpen = Boolean(opened ?? open);
  const reduced = useReducedMotion();
  const sideClasses = {
    right: `right-0 top-0 bottom-0 border-l ${widthClass}`,
    left: `left-0 top-0 bottom-0 border-r ${widthClass}`,
    bottom: 'left-0 right-0 bottom-0 border-t max-h-[85dvh]',
  };
  const motionProps = drawerAxis[side] ?? drawerAxis.right;

  useEffect(() => {
    if (isOpen) {
      return () => {
        requestAnimationFrame(clearStuckBodyPointerLock);
      };
    }
    const id = requestAnimationFrame(clearStuckBodyPointerLock);
    return () => cancelAnimationFrame(id);
  }, [isOpen]);

  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(next) => { if (!next) onClose?.(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay asChild>
          <motion.div
            initial={reduced ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={reduced ? INSTANT_TRANSITION : FAST_TRANSITION}
            className="fixed inset-0 z-50 bg-black/70"
          />
        </DialogPrimitive.Overlay>
        <DialogPrimitive.Content asChild aria-describedby={undefined}>
          <motion.div
            initial={reduced ? false : motionProps.initial}
            animate={motionProps.animate}
            transition={reduced ? INSTANT_TRANSITION : PANEL_TRANSITION}
            className={cn(
              'fixed z-50 rounded-none bg-[#111111] border-[#303030] text-[#EAEAEA] flex flex-col focus:outline-none',
              sideClasses[side] ?? sideClasses.right,
            )}
          >
            <div className="flex items-center justify-between gap-2 px-4 py-2.5 max-md:px-3.5 max-md:py-3 border-b border-[#222222] bg-[#141414] shrink-0">
              <DialogPrimitive.Title className="text-[14px] font-semibold text-[#EAEAEA] m-0 truncate">
                {title}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close asChild>
                <button
                  type="button"
                  aria-label="Закрыть"
                  className="h-6 w-6 max-md:h-9 max-md:w-9 grid place-items-center text-[#929292] hover:text-[#EAEAEA] hover:bg-[#222222] cursor-pointer shrink-0"
                >
                  <X className="h-4 w-4" />
                </button>
              </DialogPrimitive.Close>
            </div>
            <div className="p-4 max-md:p-3.5 pb-[calc(1rem+env(safe-area-inset-bottom))] overflow-y-auto flex-1 min-w-0">
              {children}
            </div>
          </motion.div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
