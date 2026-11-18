import { forwardRef, useMemo, useState } from 'react';
import * as SelectPrimitive from '@radix-ui/react-select';
import * as PopoverPrimitive from '@radix-ui/react-popover';
import { Check, ChevronDown, Search, X } from 'lucide-react';
import { cn } from '@/lib/utils';

const CLEAR_SENTINEL = '__ucheba_clear__';

function normalizeOptions(options = []) {
  return options.map((item) => (typeof item === 'string' ? { value: item, label: item } : item));
}

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;

export const SelectTrigger = forwardRef(function SelectTrigger(
  { className, size = 'default', variant = 'default', children, ...props },
  ref,
) {
  return (
    <SelectPrimitive.Trigger
      ref={ref}
      className={cn(
        'inline-flex items-center justify-between gap-2 w-full min-w-0 rounded-none border px-2.5 text-[#EAEAEA] placeholder:text-[#666666] focus:border-[#7F49B4] focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors',
        variant === 'ghost'
          ? 'bg-transparent border-transparent hover:bg-[#1C1C1C] hover:border-[#303030]'
          : 'bg-[#111111] border-[#303030] hover:border-[#454545]',
        size === 'sm' ? 'h-[25px] text-[12px] px-2 max-md:min-h-[34px]' : 'h-[30px] text-[13px] max-md:min-h-[38px]',
        className,
      )}
      {...props}
    >
      <span className="truncate text-left">{children}</span>
      <SelectPrimitive.Icon asChild>
        <ChevronDown className="h-3.5 w-3.5 text-[#888888] shrink-0" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
});

export const SelectContent = forwardRef(function SelectContent(
  { className, children, position = 'popper', ...props },
  ref,
) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        ref={ref}
        position={position}
        sideOffset={2}
        className={cn(
          'z-50 max-h-64 min-w-[var(--radix-select-trigger-width)] overflow-y-auto rounded-none bg-[#191919] border border-[#303030] text-[#EAEAEA] p-1',
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport>{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
});

export const SelectItem = forwardRef(function SelectItem(
  { className, children, ...props },
  ref,
) {
  return (
    <SelectPrimitive.Item
      ref={ref}
      className={cn(
        'relative flex w-full cursor-pointer select-none items-center rounded-none py-1.5 pl-6 pr-2.5 text-[12px] text-[#EAEAEA] outline-none data-[highlighted]:bg-[#262626] data-[disabled]:pointer-events-none data-[disabled]:opacity-40',
        className,
      )}
      {...props}
    >
      <span className="absolute left-1.5 flex h-3.5 w-3.5 items-center justify-center text-[#B98CE8]">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-3 w-3" />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
});

export function SelectField({
  value,
  onChange,
  options = [],
  placeholder = 'Выбрать…',
  clearable = false,
  clearLabel = 'Все',
  disabled = false,
  ariaLabel,
  className,
  size = 'default',
  variant = 'default',
  id,
}) {
  const normalized = useMemo(() => normalizeOptions(options), [options]);
  const stringValue = value == null || value === '' ? undefined : String(value);

  return (
    <Select
      value={stringValue}
      onValueChange={(next) => {
        if (next === CLEAR_SENTINEL) onChange(null);
        else onChange(next);
      }}
      disabled={disabled}
    >
      <SelectTrigger id={id} aria-label={ariaLabel} size={size} variant={variant} className={className}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {clearable && (
          <SelectItem value={CLEAR_SENTINEL} className="text-[#929292]">
            {clearLabel}
          </SelectItem>
        )}
        {normalized.map((option) => (
          <SelectItem key={option.value} value={String(option.value)} disabled={option.disabled}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function Combobox({
  value,
  onChange,
  options = [],
  placeholder = 'Выбрать…',
  searchPlaceholder = 'Поиск…',
  emptyText = 'Ничего не найдено',
  clearable = false,
  disabled = false,
  allowFreeText = false,
  onOpen,
  filterFn,
  ariaLabel,
  className,
  id,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const normalized = useMemo(() => normalizeOptions(options), [options]);
  const selected = normalized.find((item) => String(item.value) === String(value ?? ''));

  const filtered = useMemo(() => {
    if (filterFn) return filterFn(normalized, query);
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) return normalized;
    return normalized.filter((item) => item.label.toLowerCase().includes(trimmed) || String(item.value).toLowerCase().includes(trimmed));
  }, [normalized, query, filterFn]);

  function handleOpenChange(next) {
    setOpen(next);
    if (next) {
      setQuery('');
      onOpen?.();
    }
  }

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={handleOpenChange}>
      <div className={cn('relative flex items-center min-w-0', className)}>
        <PopoverPrimitive.Trigger asChild>
          <button
            id={id}
            type="button"
            disabled={disabled}
            aria-label={ariaLabel}
            className={cn(
              'flex h-[30px] max-md:min-h-[38px] w-full min-w-0 items-center justify-between gap-2 rounded-none bg-[#111111] border border-[#303030] px-2.5 text-[13px] text-left hover:border-[#454545] focus-visible:border-[#7F49B4] disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer',
              clearable && value ? 'pr-12' : '',
            )}
          >
            <span className={cn('truncate', !(selected || value) && 'text-[#6E6E6E]')}>
              {selected ? selected.label : value || placeholder}
            </span>
            <ChevronDown className="h-3.5 w-3.5 text-[#888888] shrink-0" />
          </button>
        </PopoverPrimitive.Trigger>
        {clearable && value && !disabled && (
          <button
            type="button"
            aria-label="Очистить"
            onClick={(event) => {
              event.stopPropagation();
              onChange(null);
            }}
            className="absolute right-7 h-5 w-5 grid place-items-center text-[#888888] hover:text-[#EAEAEA] cursor-pointer"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={2}
          className="z-50 w-[max(var(--radix-popover-trigger-width),220px)] max-w-[92vw] rounded-none bg-[#191919] border border-[#303030] text-[#EAEAEA] p-1"
        >
          <div className="flex items-center gap-1.5 border-b border-[#2A2A2A] px-2 pb-1.5 pt-1 mb-1">
            <Search className="h-3.5 w-3.5 text-[#888888] shrink-0" />
            <input
              type="text"
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && allowFreeText && query.trim()) {
                  event.preventDefault();
                  onChange(query.trim());
                  setOpen(false);
                }
              }}
              placeholder={searchPlaceholder}
              className="w-full bg-transparent text-[12px] text-[#EAEAEA] placeholder:text-[#666666] focus:outline-none"
            />
          </div>
          <div className="max-h-56 overflow-y-auto">
            {filtered.map((item) => {
              const active = String(item.value) === String(value ?? '');
              return (
                <button
                  key={item.value}
                  type="button"
                  onClick={() => {
                    onChange(item.value);
                    setOpen(false);
                  }}
                  className={cn(
                    'flex w-full items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-[#262626] cursor-pointer',
                    active && 'bg-[#7F49B4]/20 text-[#E6D1FA]',
                  )}
                >
                  <Check className={cn('h-3 w-3 shrink-0 text-[#B98CE8]', active ? 'opacity-100' : 'opacity-0')} />
                  <span className="truncate">{item.label}</span>
                </button>
              );
            })}
            {!filtered.length && !allowFreeText && (
              <div className="px-2 py-3 text-center text-[12px] text-[#888888]">{emptyText}</div>
            )}
            {allowFreeText && query.trim() && !filtered.some((item) => String(item.value) === query.trim()) && (
              <button
                type="button"
                onClick={() => {
                  onChange(query.trim());
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-[12px] text-[#CFA9F5] hover:bg-[#262626] cursor-pointer"
              >
                Выбрать «{query.trim()}»
              </button>
            )}
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

export function MultiCombobox({
  value = [],
  onChange,
  options = [],
  placeholder = 'Выбрать…',
  searchPlaceholder = 'Поиск…',
  disabled = false,
  className,
  id,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const normalized = useMemo(() => normalizeOptions(options), [options]);
  const selectedSet = useMemo(() => new Set(value.map(String)), [value]);

  const filtered = useMemo(() => {
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) return normalized;
    return normalized.filter((item) => item.label.toLowerCase().includes(trimmed));
  }, [normalized, query]);

  function toggleItem(itemValue) {
    const str = String(itemValue);
    if (selectedSet.has(str)) onChange(value.filter((v) => String(v) !== str));
    else onChange([...value, str]);
  }

  const selectedLabels = normalized.filter((item) => selectedSet.has(String(item.value))).map((item) => item.label);

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={(next) => { setOpen(next); if (next) setQuery(''); }}>
      <PopoverPrimitive.Trigger asChild>
        <button
          id={id}
          type="button"
          disabled={disabled}
          className={cn(
            'flex min-h-[30px] w-full items-center justify-between gap-2 rounded-none bg-[#111111] border border-[#303030] px-2.5 py-1 text-[12px] text-left hover:border-[#454545] disabled:opacity-50 cursor-pointer',
            className,
          )}
        >
          <span className={cn('truncate', !selectedLabels.length && 'text-[#6E6E6E]')}>
            {selectedLabels.length ? `${selectedLabels.length} выбр.: ${selectedLabels.join(', ')}` : placeholder}
          </span>
          <ChevronDown className="h-3.5 w-3.5 text-[#888888] shrink-0" />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={2}
          className="z-50 w-[max(var(--radix-popover-trigger-width),240px)] max-w-[92vw] rounded-none bg-[#191919] border border-[#303030] text-[#EAEAEA] p-1"
        >
          <div className="flex items-center gap-1.5 border-b border-[#2A2A2A] px-2 pb-1.5 pt-1 mb-1">
            <Search className="h-3.5 w-3.5 text-[#888888] shrink-0" />
            <input
              type="text"
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.currentTarget.value)}
              placeholder={searchPlaceholder}
              className="w-full bg-transparent text-[12px] text-[#EAEAEA] placeholder:text-[#666666] focus:outline-none"
            />
          </div>
          <div className="max-h-56 overflow-y-auto">
            {filtered.map((item) => {
              const checked = selectedSet.has(String(item.value));
              return (
                <button
                  key={item.value}
                  type="button"
                  onClick={() => toggleItem(item.value)}
                  className={cn(
                    'flex w-full items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-[#262626] cursor-pointer',
                    checked && 'bg-[#7F49B4]/15',
                  )}
                >
                  <span className={cn(
                    'flex h-3.5 w-3.5 items-center justify-center border shrink-0',
                    checked ? 'border-[#7F49B4] bg-[#7F49B4] text-white' : 'border-[#454545] bg-[#111111]',
                  )}>
                    {checked && <Check className="h-2.5 w-2.5" />}
                  </span>
                  <span className="truncate">{item.label}</span>
                </button>
              );
            })}
            {!filtered.length && (
              <div className="px-2 py-3 text-center text-[12px] text-[#888888]">Ничего не найдено</div>
            )}
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}
