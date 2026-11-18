import { forwardRef } from 'react';
import * as DropdownMenuPrimitive from '@radix-ui/react-dropdown-menu';
import { cn } from '@/lib/utils';

export const DropdownMenu = DropdownMenuPrimitive.Root;
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;

export const DropdownMenuContent = forwardRef(function DropdownMenuContent(
  { className, align = 'end', sideOffset = 3, ...props },
  ref,
) {
  return (
    <DropdownMenuPrimitive.Portal>
      <DropdownMenuPrimitive.Content
        ref={ref}
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-50 min-w-[168px] max-w-[280px] rounded-none bg-[#191919] border border-[#303030] p-1 text-[#EAEAEA] focus:outline-none',
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
});

export const DropdownMenuItem = forwardRef(function DropdownMenuItem(
  { className, danger = false, ...props },
  ref,
) {
  return (
    <DropdownMenuPrimitive.Item
      ref={ref}
      className={cn(
        'relative flex cursor-pointer select-none items-center gap-2 rounded-none px-2.5 py-1.5 max-md:py-2.5 text-[12px] outline-none transition-colors data-[disabled]:pointer-events-none data-[disabled]:opacity-40',
        danger
          ? 'text-[#D46A6A] data-[highlighted]:bg-[#291515] data-[highlighted]:text-[#E88484]'
          : 'text-[#EAEAEA] data-[highlighted]:bg-[#262626]',
        className,
      )}
      {...props}
    />
  );
});

export const DropdownMenuLabel = forwardRef(function DropdownMenuLabel(
  { className, ...props },
  ref,
) {
  return (
    <DropdownMenuPrimitive.Label
      ref={ref}
      className={cn('px-2.5 py-1 text-[11px] font-medium text-[#929292] truncate', className)}
      {...props}
    />
  );
});

export const DropdownMenuSeparator = forwardRef(function DropdownMenuSeparator(
  { className, ...props },
  ref,
) {
  return (
    <DropdownMenuPrimitive.Separator
      ref={ref}
      className={cn('-mx-1 my-1 h-px bg-[#2A2A2A]', className)}
      {...props}
    />
  );
});
