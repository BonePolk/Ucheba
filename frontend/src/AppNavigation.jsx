import { useState } from 'react';
import { Button, Drawer, Stack, Text } from '@mantine/core';

const icons = {
  tasks: 'M8 6h12M8 12h12M8 18h12M3 6h1M3 12h1M3 18h1',
  calendar: 'M4 5h16v16H4zM4 10h16M8 3v4M16 3v4',
  files: 'M3 7V5h6l2 2h10v13H3z',
  profile: 'M4 21v-3a8 8 0 0 1 16 0v3M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  more: 'M5 12h.01M12 12h.01M19 12h.01',
};

function Icon({ name }) {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={icons[name]} /></svg>;
}

export default function AppNavigation({ user, path, onLogout, leaving, collapsed, onToggle }) {
  const [more, setMore] = useState(false);
  const links = [{ href: '/', label: 'Задачи', icon: 'tasks' },
    { href: '/calendar', label: 'Календарь', icon: 'calendar' },
    ...(user.features.context !== false ? [{ href: '/files', label: 'Файлы', icon: 'files' }] : []),
    { href: '/profile', label: 'Профиль', icon: 'profile' }];
  const extra = <>
    <a href="/inbox" onClick={() => setMore(false)}>Входящие</a>
    <Button variant="subtle" color="gray" loading={leaving} onClick={onLogout}>Выйти из аккаунта</Button>
  </>;
  return <>
    <aside className={`app-navigation ${collapsed ? 'is-collapsed' : ''}`}>
      <div className="app-navigation-head"><a className="app-brand" href="/"><img src="/icon.png" width="34" height="34" alt="" /><span>Учёба</span></a>
        <button type="button" className="menu-toggle" onClick={onToggle} aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'} title={collapsed ? 'Развернуть меню' : 'Свернуть меню'} aria-expanded={!collapsed}>
          <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d={collapsed ? 'm9 6 6 6-6 6' : 'm15 6-6 6 6 6'} /></svg>
        </button>
      </div>
      <nav aria-label="Основная навигация" className="app-links">
        {links.map((link) => <a key={link.href} href={link.href} aria-label={link.label} title={collapsed ? link.label : undefined} aria-current={path === link.href ? 'page' : undefined}><Icon name={link.icon} /><span>{link.label}</span></a>)}
        <button type="button" className="app-more" title="Ещё" aria-expanded={more} onClick={() => setMore(true)}><Icon name="more" /><span>Ещё</span></button>
      </nav>
      <div className="app-account"><Text size="sm" fw={600} truncate>{user.username}</Text>{extra}</div>
    </aside>
    <Drawer opened={more} onClose={() => setMore(false)} position="bottom" title={user.username} size="auto" radius="lg">
      <Stack className="app-more-links" gap="sm">{extra}</Stack>
    </Drawer>
  </>;
}
