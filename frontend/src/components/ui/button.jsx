import { cloneElement, forwardRef, isValidElement } from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva } from 'class-variance-authority';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

export const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-none border font-medium transition-[color,background-color,border-color,transform] duration-100 active:enabled:scale-[0.98] select-none cursor-pointer disabled:opacity-45 disabled:cursor-not-allowed max-w-full shrink-0 focus-visible:outline-1 focus-visible:outline-[#9E6FD0] focus-visible:outline-offset-1',
  {
    variants: {
      variant: {
        primary: 'bg-[#7F49B4] border-[#7F49B4] text-[#F5F0FA] hover:bg-[#8E58C3]',
        default: 'bg-[#191919] border-[#303030] text-[#EAEAEA] hover:bg-[#222222] hover:border-[#424242]',
        secondary: 'bg-[#191919] border-[#303030] text-[#EAEAEA] hover:bg-[#222222] hover:border-[#424242]',
        outline: 'bg-transparent border-[#303030] text-[#EAEAEA] hover:bg-[#191919]',
        ghost: 'bg-transparent border-transparent text-[#929292] hover:text-[#EAEAEA] hover:bg-[#191919]',
        subtle: 'bg-transparent border-transparent text-[#929292] hover:text-[#EAEAEA] hover:bg-[#191919]',
        'accent-subtle': 'bg-[#7F49B4]/18 border-[#7F49B4]/40 text-[#CFA9F5] hover:bg-[#7F49B4]/28',
        danger: 'bg-[#291515] border-[#4F2424] text-[#E07A7A] hover:bg-[#361B1B]',
        'danger-ghost': 'bg-transparent border-transparent text-[#D46A6A] hover:bg-[#291515]',
      },
      size: {
        default: 'h-7 px-2.5 text-[13px] gap-1.5 max-md:min-h-[36px]',
        sm: 'h-[25px] px-2 text-[12px] gap-1 max-md:min-h-[32px]',
        xs: 'h-[22px] px-1.5 text-[11px] gap-1',
        icon: 'h-7 w-7 p-0 max-md:h-9 max-md:w-9',
        'icon-sm': 'h-6 w-6 p-0 max-md:h-8 max-md:w-8',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export const Button = forwardRef(function Button(
  { className, variant, size, asChild = false, loading = false, leftIcon, rightIcon, disabled, children, type, ...props },
  ref,
) {
  const Comp = asChild ? Slot : 'button';
  if (asChild) {
    const content = isValidElement(children) && (leftIcon || rightIcon)
      ? cloneElement(children, undefined, leftIcon, children.props.children, rightIcon)
      : children;
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      >
        {content}
      </Comp>
    );
  }
  return (
    <Comp
      ref={ref}
      type={type ?? 'button'}
      disabled={disabled || loading}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    >
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" aria-hidden="true" /> : leftIcon}
      {children}
      {rightIcon}
    </Comp>
  );
});

export const IconButton = forwardRef(function IconButton(
  { size = 'icon', variant = 'ghost', title, children, ...props },
  ref,
) {
  return (
    <Button
      ref={ref}
      size={size}
      variant={variant}
      title={title ?? props['aria-label']}
      {...props}
    >
      {children}
    </Button>
  );
});
