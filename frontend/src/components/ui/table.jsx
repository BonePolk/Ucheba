import { forwardRef } from 'react';
import { cn } from '@/lib/utils';

export const tableRowClass = 'h-[38px] hover:bg-[#161616] transition-colors';

export function TableContainer({ className, children }) {
  return (
    <div className={cn('border border-[#303030] bg-[#111111] overflow-x-auto rounded-none', className)}>
      {children}
    </div>
  );
}

export const Table = forwardRef(function Table({ className, ...props }, ref) {
  return (
    <table ref={ref} className={cn('w-full border-separate border-spacing-0 text-[13px] text-[#EAEAEA]', className)} {...props} />
  );
});

export const TableHeader = forwardRef(function TableHeader({ className, ...props }, ref) {
  return (
    <thead ref={ref} className={cn('bg-[#0D0D0D] [&_th]:border-b [&_th]:border-[#303030]', className)} {...props} />
  );
});

export const TableBody = forwardRef(function TableBody({ className, ...props }, ref) {
  return (
    <tbody
      ref={ref}
      className={cn('[&_td]:border-b [&_td]:border-[#1E1E1E] [&_tr:last-child_td]:border-b-0', className)}
      {...props}
    />
  );
});

export const TableRow = forwardRef(function TableRow({ className, ...props }, ref) {
  return (
    <tr ref={ref} className={cn(tableRowClass, className)} {...props} />
  );
});

export const TableHead = forwardRef(function TableHead({ className, ...props }, ref) {
  return (
    <th
      ref={ref}
      className={cn(
        'h-8 px-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-[#929292] whitespace-nowrap',
        className,
      )}
      {...props}
    />
  );
});

export const TableCell = forwardRef(function TableCell({ className, ...props }, ref) {
  return (
    <td ref={ref} className={cn('px-2.5 py-1.5 align-middle min-w-0', className)} {...props} />
  );
});
