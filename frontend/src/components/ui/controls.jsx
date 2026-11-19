import { forwardRef, useId, useRef } from 'react';
import * as CheckboxPrimitive from '@radix-ui/react-checkbox';
import * as SwitchPrimitive from '@radix-ui/react-switch';
import { Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { EASE_OUT, motion, useReducedMotion } from '@/lib/motion';

export const Checkbox = forwardRef(function Checkbox(
  { className, label, checked, onCheckedChange, onChange, disabled, id, ...props },
  ref,
) {
  const autoId = useId();
  const controlId = id ?? autoId;
  const control = (
    <CheckboxPrimitive.Root
      ref={ref}
      id={controlId}
      checked={checked}
      disabled={disabled}
      onCheckedChange={(next) => {
        const bool = next === true;
        onCheckedChange?.(bool);
        onChange?.({ currentTarget: { checked: bool }, target: { checked: bool } });
      }}
      className={cn(
        'peer h-4 w-4 max-md:h-5 max-md:w-5 shrink-0 rounded-none border border-[#454545] bg-[#111111] flex items-center justify-center cursor-pointer transition-colors data-[state=checked]:bg-[#7F49B4] data-[state=checked]:border-[#7F49B4] disabled:opacity-45 disabled:cursor-not-allowed',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="text-white flex items-center justify-center u-check-in">
        <Check className="h-3 w-3 stroke-[2.5]" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );

  if (!label) return control;
  return (
    <label htmlFor={controlId} className="inline-flex items-center gap-2 max-md:py-1 cursor-pointer select-none text-[13px] text-[#EAEAEA]">
      {control}
      <span>{label}</span>
    </label>
  );
});

export const Switch = forwardRef(function Switch(
  { className, label, description, checked, onCheckedChange, onChange, disabled, id, ...props },
  ref,
) {
  const autoId = useId();
  const controlId = id ?? autoId;
  const control = (
    <SwitchPrimitive.Root
      ref={ref}
      id={controlId}
      checked={checked}
      disabled={disabled}
      onCheckedChange={(next) => {
        onCheckedChange?.(next);
        onChange?.({ currentTarget: { checked: next }, target: { checked: next } });
      }}
      className={cn(
        'peer inline-flex h-[18px] w-[32px] max-md:h-[24px] max-md:w-[42px] shrink-0 items-center rounded-none border border-[#3A3A3A] bg-[#191919] cursor-pointer transition-colors data-[state=checked]:bg-[#7F49B4] data-[state=checked]:border-[#7F49B4] disabled:opacity-45 disabled:cursor-not-allowed',
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block h-[12px] w-[12px] max-md:h-[18px] max-md:w-[18px] rounded-none bg-[#EAEAEA] transition-transform duration-150 translate-x-[2px] data-[state=checked]:translate-x-[16px] max-md:data-[state=checked]:translate-x-[20px]" />
    </SwitchPrimitive.Root>
  );

  if (!label) return control;
  return (
    <label htmlFor={controlId} className="inline-flex items-start gap-2.5 max-md:py-1 cursor-pointer select-none min-w-0">
      <span className="pt-0.5">{control}</span>
      <span className="flex flex-col gap-0.5 min-w-0">
        <span className="text-[13px] text-[#EAEAEA] leading-snug">{label}</span>
        {description && <span className="text-[11px] text-[#929292] leading-snug">{description}</span>}
      </span>
    </label>
  );
});

const progressTones = {
  accent: 'bg-[#7F49B4]',
  success: 'bg-[#6EBE86]',
  warning: 'bg-[#D4A24C]',
  danger: 'bg-[#D46A6A]',
  info: 'bg-[#6FA3CC]',
};

export function Progress({ value = 0, max = 100, tone = 'accent', className }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const reduced = useReducedMotion();
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={cn('h-1.5 w-full bg-[#191919] border border-[#262626] overflow-hidden rounded-none', className)}
    >
      <motion.div
        initial={false}
        animate={{ width: `${pct}%` }}
        transition={reduced ? { duration: 0.01 } : { duration: 0.22, ease: EASE_OUT }}
        className={cn('h-full', progressTones[tone] ?? progressTones.accent)}
      />
    </div>
  );
}

export function FileTrigger({ onChange, multiple = false, accept, resetRef, disabled = false, children }) {
  const inputRef = useRef(null);
  if (resetRef) {
    resetRef.current = () => {
      if (inputRef.current) inputRef.current.value = '';
    };
  }
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        tabIndex={-1}
        aria-hidden="true"
        className="sr-only"
        multiple={multiple}
        accept={accept}
        onChange={(event) => {
          const list = Array.from(event.currentTarget.files ?? []);
          if (!list.length) return;
          onChange?.(multiple ? list : list[0]);
        }}
      />
      {children({
        onClick: () => {
          if (disabled) return;
          if (inputRef.current) inputRef.current.value = '';
          inputRef.current?.click();
        },
        disabled,
      })}
    </>
  );
}
