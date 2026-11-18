import { useState } from 'react';
import {
  Bot,
  CalendarDays,
  CheckSquare,
  ChevronDown,
  Folder,
  Inbox,
  Layers,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Shield,
  Terminal,
  User,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { LAYOUT_TRANSITION, motion, PANEL_TRANSITION } from '@/lib/motion';
import { IconButton } from '@/components/ui/button';
import { Drawer } from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Badge } from '@/components/ui/surface';
import { HintTooltip } from '@/components/ui/tooltip';

export default function AppNavigation({
  user,
  path,
  onLogout,
  leaving,
  collapsed,
  onToggle,
  inboxCount = 0,
}) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const features = user?.features ?? {};
  const filesEnabled = features.monocle !== false || features.arrodes !== false || features.straus !== false;
  const inboxEnabled = features.telegram !== false || features.collector !== false || inboxCount > 0;

  const links = [
    { href: '/', label: 'Задачи', icon: CheckSquare, active: path === '/' },
    ...(features.ameba !== false ? [{ href: '/ameba', label: 'План', icon: Layers, active: path === '/ameba' }] : []),
    { href: '/calendar', label: 'Календарь', icon: CalendarDays, active: path === '/calendar' },
    ...(filesEnabled ? [{ href: '/files', label: 'Файлы', icon: Folder, active: path === '/files' }] : []),
    ...(features.arrodes !== false ? [{ href: '/agents', label: 'Arrodes', icon: Terminal, active: path === '/agents' }] : []),
    ...(inboxEnabled ? [{ href: '/inbox', label: 'Входящие', icon: Inbox, active: path.startsWith('/inbox'), badge: inboxCount > 0 ? inboxCount : null }] : []),
    { href: '/profile', label: 'Настройки', icon: Settings, active: path.startsWith('/profile') || path.startsWith('/admin') },
  ];

  function renderNavItems(inDrawer = false) {
    return links.map((link) => {
      const Icon = link.icon;
      const item = (
        <a
          key={link.href}
          href={link.href}
          onClick={() => { if (inDrawer) setMobileOpen(false); }}
          aria-label={link.label}
          aria-current={link.active ? 'page' : undefined}
          className={cn(
            'relative flex items-center gap-2.5 h-8 px-2.5 text-[13px] transition-colors duration-100 select-none',
            link.active
              ? 'text-[#EAEAEA] font-medium'
              : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#161616]',
            inDrawer && link.active && 'bg-[#7F49B4]/16 border-l-2 border-l-[#7F49B4]',
            inDrawer && !link.active && 'border-l-2 border-l-transparent',
            collapsed && !inDrawer && 'justify-center px-0',
          )}
        >
          {!inDrawer && link.active && (
            <motion.span
              layoutId="ucheba-sidebar-active"
              transition={LAYOUT_TRANSITION}
              className={cn(
                'absolute inset-0 bg-[#7F49B4]/16 pointer-events-none',
                !collapsed && 'border-l-2 border-l-[#7F49B4]',
              )}
            />
          )}
          <Icon className="relative z-10 h-4 w-4 shrink-0" strokeWidth={1.75} aria-hidden="true" />
          {(!collapsed || inDrawer) && (
            <>
              <span className="relative z-10 truncate flex-1">{link.label}</span>
              {link.badge != null && (
                <Badge variant="accent" className="relative z-10 tabular-nums h-4 px-1 text-[10px]">{link.badge}</Badge>
              )}
            </>
          )}
        </a>
      );

      if (collapsed && !inDrawer) {
        return (
          <HintTooltip key={link.href} label={link.badge != null ? `${link.label} (${link.badge})` : link.label} side="right">
            {item}
          </HintTooltip>
        );
      }
      return item;
    });
  }

  return (
    <>
      {/* Desktop sidebar */}
      <motion.aside
        initial={false}
        animate={{ width: collapsed ? 52 : 180 }}
        transition={PANEL_TRANSITION}
        className="hidden lg:flex flex-col sticky top-0 h-dvh bg-[#0C0C0C] border-r border-[#242424] select-none shrink-0 z-30 overflow-hidden"
      >
        <div className={cn('flex items-center h-11 border-b border-[#222222] shrink-0', collapsed ? 'justify-center px-1' : 'justify-between px-2.5')}>
          {!collapsed && (
            <a href="/" className="flex items-center gap-2 min-w-0 text-[#EAEAEA] font-semibold text-[14px] tracking-tight">
              <img src="/icon.png" width="20" height="20" alt="" className="shrink-0" />
              <span className="truncate">Учёба</span>
            </a>
          )}
          <IconButton
            size="icon-sm"
            variant="ghost"
            onClick={onToggle}
            aria-label={collapsed ? 'Развернуть боковую панель' : 'Свернуть боковую панель'}
            title={collapsed ? 'Развернуть меню' : 'Свернуть меню'}
            aria-expanded={!collapsed}
          >
            {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </IconButton>
        </div>

        <nav aria-label="Основная навигация" className="flex flex-col gap-0.5 py-2 px-1.5 flex-1 overflow-y-auto">
          {renderNavItems(false)}
        </nav>

        <div className="border-t border-[#222222] p-1.5 shrink-0">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                aria-label={`Аккаунт: ${user.username}`}
                className={cn(
                  'flex items-center gap-2 w-full h-9 px-2 text-left text-[12px] text-[#EAEAEA] hover:bg-[#191919] transition-colors cursor-pointer',
                  collapsed && 'justify-center px-0',
                )}
              >
                <span className="h-6 w-6 shrink-0 grid place-items-center bg-[#191919] border border-[#303030] text-[#CFA9F5]">
                  <User className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                {!collapsed && (
                  <>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium leading-tight">{user.username}</span>
                      {user.is_admin && (
                        <span className="block text-[10px] text-[#B98CE8] leading-tight">Админ</span>
                      )}
                    </span>
                    <ChevronDown className="h-3.5 w-3.5 text-[#787878] shrink-0" />
                  </>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="top" className="w-[200px]">
              <DropdownMenuLabel>{user.username}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <a href="/profile?tab=profile" className="w-full"><User className="h-3.5 w-3.5 text-[#929292]" />Профиль</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/profile?tab=agents" className="w-full"><Bot className="h-3.5 w-3.5 text-[#929292]" />Настройки агентов</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/profile?tab=memory" className="w-full"><Settings className="h-3.5 w-3.5 text-[#929292]" />Память агентов</a>
              </DropdownMenuItem>
              {user.is_admin && (
                <DropdownMenuItem asChild>
                  <a href="/profile?tab=admin" className="w-full"><Shield className="h-3.5 w-3.5 text-[#929292]" />Администрирование</a>
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem danger disabled={leaving} onClick={onLogout}>
                <LogOut className="h-3.5 w-3.5" />
                {leaving ? 'Выходим…' : 'Выйти'}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </motion.aside>

      {/* Mobile & Tablet top header */}
      <header className="lg:hidden sticky top-0 z-30 flex items-center justify-between h-11 px-3 bg-[#0C0C0C] border-b border-[#242424]">
        <div className="flex items-center gap-2 min-w-0">
          <IconButton
            size="icon-sm"
            variant="default"
            onClick={() => setMobileOpen(true)}
            aria-label="Открыть меню навигации"
          >
            <Menu className="h-4 w-4" />
          </IconButton>
          <a href="/" className="flex items-center gap-2 min-w-0 text-[#EAEAEA] font-semibold text-[14px]">
            <img src="/icon.png" width="20" height="20" alt="" className="shrink-0" />
            <span className="truncate">Учёба</span>
          </a>
        </div>
        <div className="flex items-center gap-1.5">
          {inboxEnabled && inboxCount > 0 && (
            <a href="/inbox" className="inline-flex items-center gap-1 px-2 h-7 bg-[#7F49B4]/18 border border-[#7F49B4]/40 text-[11px] text-[#CFA9F5]">
              <Inbox className="h-3 w-3" />
              <span className="tabular-nums">{inboxCount}</span>
            </a>
          )}
          <a href="/profile" className="inline-flex items-center gap-1.5 px-2 h-7 bg-[#191919] border border-[#303030] text-[12px] text-[#EAEAEA]">
            <User className="h-3.5 w-3.5 text-[#929292]" />
            <span className="max-w-[96px] truncate">{user.username}</span>
          </a>
        </div>
      </header>

      {/* Mobile & Tablet bottom quick bar */}
      <nav
        aria-label="Быстрая навигация"
        className="md:hidden fixed bottom-0 left-0 right-0 z-30 flex items-center bg-[#0C0C0C] border-t border-[#242424] pb-[env(safe-area-inset-bottom)]"
      >
        {links.slice(0, 5).map((link) => {
          const Icon = link.icon;
          return (
            <a
              key={link.href}
              href={link.href}
              aria-current={link.active ? 'page' : undefined}
              className={cn(
                'relative flex-1 min-w-0 flex flex-col items-center justify-center gap-0.5 py-1.5 text-[10px]',
                link.active ? 'text-[#DFC4F8] bg-[#7F49B4]/16' : 'text-[#929292]',
              )}
            >
              {link.active && (
                <motion.span
                  layoutId="ucheba-bottom-active"
                  transition={LAYOUT_TRANSITION}
                  className="absolute inset-x-0 top-0 h-[2px] bg-[#7F49B4] pointer-events-none"
                />
              )}
              <Icon className="relative z-10 h-4 w-4" strokeWidth={1.75} />
              <span className="relative z-10 truncate max-w-full px-1">{link.label}</span>
            </a>
          );
        })}
      </nav>

      {/* Mobile & Tablet drawer */}
      <Drawer opened={mobileOpen} onClose={() => setMobileOpen(false)} side="left" title="Навигация" widthClass="w-[260px] max-w-[85vw]">
        <div className="flex flex-col h-full gap-4">
          <nav className="flex flex-col gap-0.5" aria-label="Меню">
            {renderNavItems(true)}
          </nav>
          <div className="mt-auto border-t border-[#242424] pt-3 flex flex-col gap-2">
            <div className="flex items-center gap-2 px-1">
              <span className="h-7 w-7 grid place-items-center bg-[#191919] border border-[#303030] text-[#CFA9F5]">
                <User className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <div className="text-[13px] font-medium truncate">{user.username}</div>
                {user.is_admin && <div className="text-[11px] text-[#B98CE8]">Админ</div>}
              </div>
            </div>
            <button
              type="button"
              disabled={leaving}
              onClick={onLogout}
              className="flex items-center gap-2 h-9 px-2.5 text-[13px] text-[#D46A6A] bg-[#291515] border border-[#4F2424] cursor-pointer"
            >
              <LogOut className="h-4 w-4" />
              <span>{leaving ? 'Выходим…' : 'Выйти из аккаунта'}</span>
            </button>
          </div>
        </div>
      </Drawer>
    </>
  );
}
