import { forwardRef, useId, useState } from 'react';
import { Eye, EyeOff } from 'lucide-react';
import { cn } from '@/lib/utils';

export function Field({ label, hint, error, rightLabel, htmlFor, className, children }) {
  return (
    <div className={cn('flex flex-col gap-1 min-w-0', className)}>
      {(label || rightLabel) && (
        <div className="flex items-center justify-between gap-2">
          {label && (
            <label htmlFor={htmlFor} className="text-[12px] font-medium text-[#929292] leading-tight">
              {label}
            </label>
          )}
          {rightLabel}
        </div>
      )}
      {children}
      {hint && !error && <p className="text-[11px] text-[#7D7D7D] leading-snug m-0">{hint}</p>}
      {error && <p className="text-[11px] text-[#D46A6A] leading-snug m-0">{error}</p>}
    </div>
  );
}

export const Input = forwardRef(function Input(
  { className, leftIcon, rightSlot, type = 'text', error, ...props },
  ref,
) {
  if (leftIcon || rightSlot) {
    return (
      <div className={cn(
        'flex items-center h-[30px] max-md:min-h-[38px] bg-[#111111] border transition-colors focus-within:border-[#7F49B4]',
        error ? 'border-[#D46A6A]' : 'border-[#303030]',
        props.disabled && 'opacity-50',
        className,
      )}>
        {leftIcon && <span className="pl-2.5 text-[#787878] flex items-center shrink-0">{leftIcon}</span>}
        <input
          ref={ref}
          type={type}
          className="h-full w-full min-w-0 bg-transparent px-2.5 text-[13px] max-md:text-[16px] text-[#EAEAEA] placeholder:text-[#666666] focus:outline-none disabled:cursor-not-allowed"
          {...props}
        />
        {rightSlot && <span className="pr-1 flex items-center shrink-0">{rightSlot}</span>}
      </div>
    );
  }

  return (
    <input
      ref={ref}
      type={type}
      className={cn(
        'h-[30px] max-md:min-h-[38px] w-full min-w-0 rounded-none bg-[#111111] border px-2.5 text-[13px] max-md:text-[16px] text-[#EAEAEA] placeholder:text-[#666666] transition-colors focus:border-[#7F49B4] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed',
        error ? 'border-[#D46A6A]' : 'border-[#303030]',
        className,
      )}
      {...props}
    />
  );
});

export const PasswordField = forwardRef(function PasswordField(
  { className, ...props },
  ref,
) {
  const [visible, setVisible] = useState(false);
  const autoId = useId();
  const id = props.id ?? autoId;
  return (
    <Input
      ref={ref}
      id={id}
      type={visible ? 'text' : 'password'}
      className={className}
      rightSlot={(
        <button
          type="button"
          tabIndex={-1}
          aria-label={visible ? 'Скрыть значение' : 'Показать значение'}
          onClick={() => setVisible((prev) => !prev)}
          className="h-6 w-6 grid place-items-center text-[#787878] hover:text-[#EAEAEA] cursor-pointer"
        >
          {visible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
        </button>
      )}
      {...props}
    />
  );
});

export const Textarea = forwardRef(function Textarea(
  { className, rows = 3, error, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(
        'w-full min-w-0 rounded-none bg-[#111111] border px-2.5 py-1.5 text-[13px] max-md:text-[16px] text-[#EAEAEA] placeholder:text-[#666666] transition-colors focus:border-[#7F49B4] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed resize-y',
        error ? 'border-[#D46A6A]' : 'border-[#303030]',
        className,
      )}
      {...props}
    />
  );
});
