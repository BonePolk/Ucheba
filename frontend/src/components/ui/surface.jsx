import { forwardRef } from 'react';
import { AlertCircle, CheckCircle2, Info, Loader2, TriangleAlert, X } from 'lucide-react';
import { cn } from '@/lib/utils';
import { FAST_TRANSITION, motion } from '@/lib/motion';

const badgeVariants = {
  default: 'bg-[#191919] border-[#303030] text-[#B8B8B8]',
  accent: 'bg-[#7F49B4]/18 border-[#7F49B4]/45 text-[#CFA9F5]',
  success: 'bg-[#16261B] border-[#284732] text-[#6EBE86]',
  warning: 'bg-[#282012] border-[#4A391D] text-[#D4A24C]',
  danger: 'bg-[#291515] border-[#4F2424] text-[#D46A6A]',
  info: 'bg-[#14202B] border-[#253E54] text-[#6FA3CC]',
  outline: 'bg-transparent border-[#303030] text-[#929292]',
};

export const Badge = forwardRef(function Badge({ variant = 'default', className, children, ...props }, ref) {
  return (
    <span
      ref={ref}
      className={cn(
        'inline-flex items-center gap-1 h-5 px-1.5 text-[11px] font-medium leading-none border rounded-none whitespace-nowrap shrink-0',
        badgeVariants[variant] ?? badgeVariants.default,
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
});

const indicatorTones = {
  idle: 'bg-[#666666]',
  active: 'bg-[#9E6FD0]',
  success: 'bg-[#6EBE86]',
  warning: 'bg-[#D4A24C]',
  danger: 'bg-[#D46A6A]',
  info: 'bg-[#6FA3CC]',
};

export function StatusIndicator({ tone = 'idle', pulse = false, label, className }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 min-w-0', className)}>
      <span
        aria-hidden="true"
        className={cn(
          'inline-block h-1.5 w-1.5 shrink-0 rounded-none',
          indicatorTones[tone] ?? indicatorTones.idle,
          pulse && 'animate-pulse',
        )}
      />
      {label && <span className="text-[12px] text-[#929292] truncate">{label}</span>}
    </span>
  );
}

const noticeStyles = {
  error: { border: 'border-[#4F2424] border-l-[#D46A6A]', bg: 'bg-[#1D1111]', icon: AlertCircle, iconColor: 'text-[#D46A6A]' },
  warning: { border: 'border-[#4A391D] border-l-[#D4A24C]', bg: 'bg-[#1B160D]', icon: TriangleAlert, iconColor: 'text-[#D4A24C]' },
  success: { border: 'border-[#284732] border-l-[#6EBE86]', bg: 'bg-[#101B13]', icon: CheckCircle2, iconColor: 'text-[#6EBE86]' },
  info: { border: 'border-[#253E54] border-l-[#6FA3CC]', bg: 'bg-[#10171E]', icon: Info, iconColor: 'text-[#6FA3CC]' },
  accent: { border: 'border-[#543476] border-l-[#9E6FD0]', bg: 'bg-[#16101D]', icon: Info, iconColor: 'text-[#B98CE8]' },
};

export function Notice({ tone = 'info', title, onClose, action, className, children }) {
  const style = noticeStyles[tone] ?? noticeStyles.info;
  const IconComp = style.icon;
  return (
    <motion.div
      role={tone === 'error' ? 'alert' : 'status'}
      initial={{ opacity: 0, y: -3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={FAST_TRANSITION}
      className={cn(
        'flex items-start gap-2.5 border border-l-2 px-3 py-2 text-[12px] text-[#EAEAEA] rounded-none',
        style.border,
        style.bg,
        className,
      )}
    >
      <IconComp className={cn('h-3.5 w-3.5 mt-0.5 shrink-0', style.iconColor)} aria-hidden="true" />
      <div className="flex-1 min-w-0 space-y-1">
        {title && <div className="font-semibold text-[#EAEAEA]">{title}</div>}
        <div className="text-[#DCDCDC] break-words">{children}</div>
      </div>
      {action && <div className="shrink-0">{action}</div>}
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label="Закрыть сообщение"
          className="h-5 w-5 grid place-items-center text-[#929292] hover:text-[#EAEAEA] cursor-pointer shrink-0"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </motion.div>
  );
}

export function EmptyState({ icon, title, description, action, className }) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 border border-dashed border-[#2A2A2A] bg-[#0D0D0D] px-4 py-8 text-center rounded-none', className)}>
      {icon && <div className="text-[#6E6E6E]">{icon}</div>}
      <div className="text-[13px] font-medium text-[#EAEAEA]">{title}</div>
      {description && <div className="text-[12px] text-[#929292] max-w-md">{description}</div>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

export function PageHeader({ title, count, subtitle, badge, actions, className }) {
  return (
    <header className={cn('flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-[#222222]', className)}>
      <div className="min-w-0 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2 min-w-0">
          <h1 className="text-[21px] max-md:text-[19px] font-semibold tracking-tight text-[#EAEAEA] m-0 leading-tight break-words">
            {title}
          </h1>
          {count != null && (
            <span className="text-[14px] font-normal text-[#929292] tabular-nums">{count}</span>
          )}
          {badge}
        </div>
        {subtitle && <p className="text-[12px] text-[#929292] m-0">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-1.5 shrink-0">{actions}</div>}
    </header>
  );
}

export function SectionHeader({ title, meta, actions, className }) {
  return (
    <div className={cn('flex flex-wrap items-center justify-between gap-2', className)}>
      <div className="flex items-center gap-2 min-w-0">
        <h2 className="text-[14px] font-semibold text-[#EAEAEA] m-0">{title}</h2>
        {meta && <span className="text-[12px] text-[#929292]">{meta}</span>}
      </div>
      {actions && <div className="flex items-center gap-1.5 shrink-0">{actions}</div>}
    </div>
  );
}

export function Spinner({ label, size = 14, className }) {
  return (
    <span className={cn('inline-flex items-center gap-2 text-[12px] text-[#929292]', className)} role="status">
      <Loader2 className="animate-spin shrink-0 text-[#9E6FD0]" style={{ width: size, height: size }} aria-hidden="true" />
      {label && <span>{label}</span>}
    </span>
  );
}

export function PageLoader({ label = 'Загрузка…' }) {
  return (
    <div className="min-h-[120px] flex items-center justify-center">
      <Spinner label={label} size={16} />
    </div>
  );
}
