import { Component, lazy, Suspense, useEffect, useState } from 'react';
import { Alert, Button, Container, Loader, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { request } from './api';
import AppNavigation from './AppNavigation';

const TaskBoard = lazy(() => import('./TaskBoard'));
const Calendar = lazy(() => import('./Calendar'));
const Account = lazy(() => import('./Account'));
const Files = lazy(() => import('./Files'));
const Inbox = lazy(() => import('./Inbox'));
const TaskAutomation = lazy(() => import('./TaskAutomation'));

class PageError extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return <Container py="lg"><Alert color="red" title="Не удалось открыть страницу">
      <Stack gap="sm">
        <Text size="sm">{this.state.error.message || 'Ошибка интерфейса'}</Text>
        <Button variant="default" onClick={() => window.location.reload()}>Перезагрузить страницу</Button>
      </Stack>
    </Alert></Container>;
  }
}

function Login() {
  const [register, setRegister] = useState(false);
  const registration = useQuery({ queryKey: ['registration'], queryFn: () => request('/auth/registration'), refetchInterval: 30000 });
  const registering = register && registration.data?.enabled === true;
  const [credentials, setCredentials] = useState({ username: '', password: '' });
  const login = useMutation({
    mutationFn: () => request(`/auth/${registering ? 'register' : 'login'}`, { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: () => window.location.assign('/'),
  });
  return <Container size={420} py="xl"><Paper withBorder p="lg"><form onSubmit={(event) => { event.preventDefault(); login.mutate(); }}><Stack>
    <Title order={2}>{registering ? 'Регистрация' : 'Вход'}</Title>
    <TextInput label="Логин" autoComplete="username" required minLength={3} maxLength={64} value={credentials.username} onChange={(event) => setCredentials({ ...credentials, username: event.currentTarget.value })} />
    <PasswordInput label="Пароль" autoComplete={registering ? 'new-password' : 'current-password'} required minLength={12} maxLength={256} value={credentials.password} onChange={(event) => setCredentials({ ...credentials, password: event.currentTarget.value })} />
    {login.error && <Alert color="red">{login.error.message}</Alert>}
    <Button type="submit" loading={login.isPending}>{registering ? 'Создать аккаунт' : 'Войти'}</Button>
    {registration.data?.enabled && <Button variant="subtle" onClick={() => { setRegister(!registering); login.reset(); }}>{registering ? 'Уже есть аккаунт' : 'Регистрация'}</Button>}
  </Stack></form></Paper></Container>;
}

function TaskPanel({ id, features }) {
  const task = useQuery({ queryKey: ['tasks', id], queryFn: () => request(`/tasks/${id}`) });
  if (task.isPending) return <div className="page-loading"><Loader /></div>;
  if (task.error) return <Alert color="red">{task.error.message}</Alert>;
  return <TaskAutomation key={id} task={task.data} features={features} />;
}

export default function App() {
  const [menuCollapsed, setMenuCollapsed] = useState(false);
  const [location, setLocation] = useState(() => window.location.pathname + window.location.search);
  useEffect(() => {
    const update = () => setLocation(window.location.pathname + window.location.search);
    window.addEventListener('popstate', update);
    return () => window.removeEventListener('popstate', update);
  }, []);
  function followLink(event) {
    const link = event.target.closest('a');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
    const url = new URL(link.href);
    if (url.origin !== window.location.origin || !['/', '/profile', '/admin', '/inbox', '/files', '/calendar'].includes(url.pathname)) return;
    event.preventDefault();
    window.history.pushState(null, '', url.pathname + url.search + url.hash);
    setLocation(url.pathname + url.search);
  }
  const me = useQuery({ queryKey: ['me'], queryFn: () => request('/auth/me'), retry: false, refetchInterval: 30000 });
  const logout = useMutation({ mutationFn: () => request('/auth/logout', { method: 'POST' }), onSuccess: () => window.location.assign('/') });
  if (me.isPending) return <Container py="xl"><Loader /></Container>;
  if (me.error?.status === 401) return <Login />;
  if (me.error) return <Container py="xl"><Alert color="red">{me.error.message}</Alert></Container>;
  const user = me.data;
  const [path, search = ''] = location.split('?');
  const task = new URLSearchParams(search).get('task');
  const board = path === '/' && !task;
  return <div className={`app-shell ${menuCollapsed ? 'is-collapsed' : ''}`} key={user.id} onClick={followLink}>
    <AppNavigation user={user} path={path} collapsed={menuCollapsed} onToggle={() => setMenuCollapsed(!menuCollapsed)} onLogout={() => logout.mutate()} leaving={logout.isPending} />
    <main className="app-content">
      {logout.error && <Container py="sm"><Alert color="red">{logout.error.message}</Alert></Container>}
    <PageError key={location}><Suspense fallback={<Container size="xl" w="100%" py="md"><div className="page-loading"><Loader /></div></Container>}>{board ? <TaskBoard features={user.features} /> : <Container size="xl" w="100%" py="md">{task ? <TaskPanel id={task} features={user.features} /> : path === '/calendar' ? <Calendar features={user.features} /> : path === '/files' ? (user.features.monocle !== false || user.features.arrodes !== false || user.features.straus !== false) ? <Files /> : <Alert>Функция отключена</Alert> : path.startsWith('/profile') ? <Account key="profile" user={user} /> : path.startsWith('/admin') ? user.is_admin ? <Account key="profile-admin" user={user} initialTab="admin" /> : <Alert color="red">Требуется доступ администратора</Alert> : path.startsWith('/inbox') ? <Inbox /> : <Alert>Страница не найдена</Alert>}</Container>}</Suspense></PageError>
    </main>
  </div>;
}
