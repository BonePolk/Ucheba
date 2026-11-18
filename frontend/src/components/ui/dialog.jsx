import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { AnimatePresence, EASE_OUT, FAST_TRANSITION, motion, PANEL_TRANSITION, useReducedMotion } from '@/lib/motion';

const sizeMap = {
  sm: 'max-w-[380px]',
  md: 'max-w-[520px]',
  lg: 'max-w-[720px]',
  xl: 'max-w-[1040px]',
};

const INSTANT_TRANSITION = { duration: 0.01 };

export function Modal({ opened, open, onClose, title, description, size = 'md', children, className }) {
  const isOpen = Boolean(opened ?? open);
  const reduced = useReducedMotion();
  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(next) => { if (!next) onClose?.(); }}>
      <AnimatePresence>
        {isOpen && (
          <DialogPrimitive.Portal key="modal-portal" forceMount>
            <DialogPrimitive.Overlay forceMount asChild>
              <motion.div
                key="modal-overlay"
                initial={reduced ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={reduced ? INSTANT_TRANSITION : FAST_TRANSITION}
                className="fixed inset-0 z-50 bg-black/75"
              />
            </DialogPrimitive.Overlay>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-3 pointer-events-none">
              <DialogPrimitive.Content
                forceMount
                asChild
                {...(!description ? { 'aria-describedby': undefined } : {})}
              >
                <motion.div
                  key="modal-content"
                  initial={reduced ? false : { opacity: 0, y: 8, scale: 0.985 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={reduced ? { opacity: 0 } : { opacity: 0, y: 6, scale: 0.985 }}
                  transition={reduced ? INSTANT_TRANSITION : { duration: 0.16, ease: EASE_OUT }}
                  className={cn(
                    'pointer-events-auto w-full rounded-none bg-[#111111] border border-[#303030] text-[#EAEAEA] flex flex-col max-h-[calc(100dvh-24px)] focus:outline-none',
                    sizeMap[size] ?? sizeMap.md,
                    className,
                  )}
                >
                  <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-[#222222] bg-[#141414] shrink-0">
                    <DialogPrimitive.Title className="text-[14px] font-semibold text-[#EAEAEA] m-0 truncate">
                      {title}
                    </DialogPrimitive.Title>
                    <DialogPrimitive.Close asChild>
                      <button
                        type="button"
                        aria-label="Закрыть"
                        className="h-6 w-6 grid place-items-center text-[#929292] hover:text-[#EAEAEA] hover:bg-[#222222] cursor-pointer shrink-0"
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
                  <div className="p-4 overflow-y-auto [overflow-wrap:anywhere] min-w-0">
                    {children}
                  </div>
                </motion.div>
              </DialogPrimitive.Content>
            </div>
          </DialogPrimitive.Portal>
        )}
      </AnimatePresence>
    </DialogPrimitive.Root>
  );
}

const drawerAxis = {
  right: { initial: { x: '100%' }, animate: { x: 0 }, exit: { x: '100%' } },
  left: { initial: { x: '-100%' }, animate: { x: 0 }, exit: { x: '-100%' } },
  bottom: { initial: { y: '100%' }, animate: { y: 0 }, exit: { y: '100%' } },
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
  return (
    <DialogPrimitive.Root open={isOpen} onOpenChange={(next) => { if (!next) onClose?.(); }}>
      <AnimatePresence>
        {isOpen && (
          <DialogPrimitive.Portal key="drawer-portal" forceMount>
            <DialogPrimitive.Overlay forceMount asChild>
              <motion.div
                key="drawer-overlay"
                initial={reduced ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={reduced ? INSTANT_TRANSITION : FAST_TRANSITION}
                className="fixed inset-0 z-50 bg-black/70"
              />
            </DialogPrimitive.Overlay>
            <DialogPrimitive.Content forceMount asChild aria-describedby={undefined}>
              <motion.div
                key="drawer-content"
                initial={reduced ? false : motionProps.initial}
                animate={motionProps.animate}
                exit={reduced ? { opacity: 0 } : motionProps.exit}
                transition={reduced ? INSTANT_TRANSITION : PANEL_TRANSITION}
                className={cn(
                  'fixed z-50 rounded-none bg-[#111111] border-[#303030] text-[#EAEAEA] flex flex-col focus:outline-none',
                  sideClasses[side] ?? sideClasses.right,
                )}
              >
                <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-[#222222] bg-[#141414] shrink-0">
                  <DialogPrimitive.Title className="text-[14px] font-semibold text-[#EAEAEA] m-0 truncate">
                    {title}
                  </DialogPrimitive.Title>
                  <DialogPrimitive.Close asChild>
                    <button
                      type="button"
                      aria-label="Закрыть"
                      className="h-6 w-6 grid place-items-center text-[#929292] hover:text-[#EAEAEA] hover:bg-[#222222] cursor-pointer shrink-0"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </DialogPrimitive.Close>
                </div>
                <div className="p-4 overflow-y-auto flex-1 min-w-0">
                  {children}
                </div>
              </motion.div>
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        )}
      </AnimatePresence>
    </DialogPrimitive.Root>
  );
}
