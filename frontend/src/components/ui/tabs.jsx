import { createContext, forwardRef, useContext, useId, useState } from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cn } from '@/lib/utils';
import { motion, SPRING_SNAPPY, useReducedMotion } from '@/lib/motion';

const TabsContext = createContext({ groupId: 'tabs', value: undefined });

export function Tabs({ value, defaultValue, onValueChange, children, ...props }) {
  const [internal, setInternal] = useState(defaultValue);
  const current = value !== undefined ? value : internal;
  const groupId = useId();

  return (
    <TabsContext.Provider value={{ groupId, value: current }}>
      <TabsPrimitive.Root
        value={value}
        defaultValue={defaultValue}
        onValueChange={(next) => {
          if (value === undefined) setInternal(next);
          onValueChange?.(next);
        }}
        {...props}
      >
        {children}
      </TabsPrimitive.Root>
    </TabsContext.Provider>
  );
}

export const TabsList = forwardRef(function TabsList({ className, ...props }, ref) {
  return (
    <TabsPrimitive.List
      ref={ref}
      className={cn(
        'flex items-center gap-1 border-b border-[#303030] overflow-x-auto max-w-full no-scrollbar rounded-none',
        className,
      )}
      {...props}
    />
  );
});

export const TabsTrigger = forwardRef(function TabsTrigger({ className, value, children, ...props }, ref) {
  const ctx = useContext(TabsContext);
  const active = ctx.value !== undefined && ctx.value === value;
  const reduced = useReducedMotion();
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      value={value}
      className={cn(
        'relative inline-flex items-center gap-1.5 h-8 max-md:h-11 px-3 max-md:px-3.5 text-[13px] font-medium text-[#929292] whitespace-nowrap shrink-0 cursor-pointer transition-colors hover:text-[#EAEAEA] data-[state=active]:text-[#EAEAEA] focus-visible:outline-1 focus-visible:outline-[#9E6FD0]',
        ctx.value === undefined && 'border-b-2 border-transparent data-[state=active]:border-[#7F49B4]',
        className,
      )}
      {...props}
    >
      <span className="relative z-10 inline-flex items-center gap-1.5">{children}</span>
      {active && (
        <motion.span
          layoutId={`tab-ind-${ctx.groupId}`}
          transition={reduced ? { duration: 0.01 } : SPRING_SNAPPY}
          className="absolute left-0 right-0 bottom-0 h-[2px] bg-[#7F49B4] pointer-events-none"
        />
      )}
    </TabsPrimitive.Trigger>
  );
});

export const TabsContent = forwardRef(function TabsContent({ className, ...props }, ref) {
  return (
    <TabsPrimitive.Content
      ref={ref}
      className={cn('pt-3 focus:outline-none min-w-0', className)}
      {...props}
    />
  );
});

export function SegmentedControl({ value, onChange, options = [], ariaLabel, size = 'default', className }) {
  const groupId = useId();
  const reduced = useReducedMotion();
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        'inline-flex items-center bg-[#111111] border border-[#303030] p-0.5 rounded-none shrink-0',
        size === 'sm' ? 'h-[26px] max-md:h-9' : 'h-7 max-md:h-10',
        className,
      )}
    >
      {options.map((item) => {
        const option = typeof item === 'string' ? { value: item, label: item } : item;
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'relative h-full px-2.5 max-md:px-3 text-[12px] max-md:text-[13px] font-medium whitespace-nowrap cursor-pointer transition-colors rounded-none',
              active ? 'text-[#EAEAEA]' : 'text-[#929292] hover:text-[#EAEAEA]',
            )}
          >
            {active && (
              <motion.span
                layoutId={`seg-${groupId}`}
                transition={reduced ? { duration: 0.01 } : SPRING_SNAPPY}
                className="absolute inset-0 bg-[#252525] border border-[#3A3A3A] pointer-events-none"
              />
            )}
            <span className="relative z-10">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
