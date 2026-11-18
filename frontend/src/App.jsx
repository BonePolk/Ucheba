import { Component, lazy, Suspense, useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { request } from './api';
import AppNavigation from './AppNavigation';
import { Button } from '@/components/ui/button';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { EmptyState, Notice, PageLoader } from '@/components/ui/surface';
import { FAST_TRANSITION, LiveHorizon, motion, PageTransition, useReducedMotion } from '@/lib/motion';

const TaskBoard = lazy(() => import('./TaskBoard'));
const AmebaWorkspace = lazy(() => import('./AmebaWorkspace'));
const AgentsHub = lazy(() => import('./AgentsHub'));
const Calendar = lazy(() => import('./Calendar'));
const Account = lazy(() => import('./Account'));
const Files = lazy(() => import('./Files'));
const Inbox = lazy(() => import('./Inbox'));
const TaskAutomation = lazy(() => import('./TaskAutomation'));

const SPA_PATHS = new Set(['/', '/ameba', '/agents', '/profile', '/admin', '/inbox', '/files', '/calendar']);

class PageError extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="max-w-lg mx-auto py-8">
        <Notice
          tone="error"
          title="Не удалось открыть страницу"
          action={<Button size="sm" onClick={() => window.location.reload()}>Перезагрузить</Button>}
        >
          {this.state.error.message || 'Ошибка интерфейса'}
        </Notice>
      </div>
    );
  }
}

function Login() {
  const reduced = useReducedMotion();
  const [register, setRegister] = useState(false);
  const registration = useQuery({
    queryKey: ['registration'],
    queryFn: () => request('/auth/registration'),
    refetchInterval: 30000,
  });
  const registering = register && registration.data?.enabled === true;
  const [credentials, setCredentials] = useState({ username: '', password: '' });
  const login = useMutation({
    mutationFn: () => request(`/auth/${registering ? 'register' : 'login'}`, {
      method: 'POST',
      body: JSON.stringify(credentials),
    }),
    onSuccess: () => window.location.assign('/'),
  });

  return (
    <div className="min-h-dvh flex items-center justify-center p-4 bg-[#090909]">
      <motion.div
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={reduced ? { duration: 0.01 } : FAST_TRANSITION}
        className="relative overflow-hidden w-full max-w-[360px] bg-[#111111] border border-[#303030] p-5"
      >
        <LiveHorizon active={login.isPending} />
        <div className="flex items-center gap-2.5 pb-4 mb-4 border-b border-[#222222]">
          <img src="/icon.png" width="24" height="24" alt="" />
          <h1 className="text-[16px] font-semibold text-[#EAEAEA] m-0">
            {registering ? 'Регистрация' : 'Вход в Учёбу'}
          </h1>
        </div>
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            login.mutate();
          }}
        >
          <Field label="Логин" htmlFor="login-username">
            <Input
              id="login-username"
              autoComplete="username"
              required
              minLength={3}
              maxLength={64}
              value={credentials.username}
              onChange={(event) => setCredentials({ ...credentials, username: event.currentTarget.value })}
            />
          </Field>
          <Field label="Пароль" htmlFor="login-password">
            <PasswordField
              id="login-password"
              autoComplete={registering ? 'new-password' : 'current-password'}
              required
              minLength={12}
              maxLength={256}
              value={credentials.password}
              onChange={(event) => setCredentials({ ...credentials, password: event.currentTarget.value })}
            />
          </Field>
          {login.error && <Notice tone="error">{login.error.message}</Notice>}
          <div className="flex flex-col gap-2 pt-1">
            <Button type="submit" variant="primary" loading={login.isPending} className="w-full">
              {registering ? 'Создать аккаунт' : 'Войти'}
            </Button>
            {registration.data?.enabled && (
              <Button
                variant="ghost"
                className="w-full"
                onClick={() => {
                  setRegister(!registering);
                  login.reset();
                }}
              >
                {registering ? 'Уже есть аккаунт' : 'Регистрация'}
              </Button>
            )}
          </div>
        </form>
      </motion.div>
    </div>
  );
}

function TaskPanel({ id, features }) {
  const task = useQuery({ queryKey: ['tasks', id], queryFn: () => request(`/tasks/${id}`) });
  if (task.isPending) return <PageLoader label="Загрузка задачи…" />;
  if (task.error) return <Notice tone="error">{task.error.message}</Notice>;
  return <TaskAutomation key={id} task={task.data} features={features} />;
}

export default function App() {
  const [menuCollapsed, setMenuCollapsed] = useState(() => {
    try {
      return localStorage.getItem('ucheba-sidebar-collapsed') === '1';
    } catch {
      return false;
    }
  });
  const [location, setLocation] = useState(() => window.location.pathname + window.location.search);

  useEffect(() => {
    const update = () => setLocation(window.location.pathname + window.location.search);
    window.addEventListener('popstate', update);
    window.addEventListener('ucheba:navigate', update);
    return () => {
      window.removeEventListener('popstate', update);
      window.removeEventListener('ucheba:navigate', update);
    };
  }, []);

  function toggleMenu() {
    setMenuCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('ucheba-sidebar-collapsed', next ? '1' : '0');
      } catch { /* ignore */ }
      return next;
    });
  }

  function followLink(event) {
    const link = event.target.closest('a');
    if (
      !link
      || event.defaultPrevented
      || event.button !== 0
      || event.ctrlKey
      || event.metaKey
      || event.shiftKey
      || event.altKey
      || link.target
      || link.hasAttribute('download')
    ) return;
    const url = new URL(link.href);
    if (url.origin !== window.location.origin || !SPA_PATHS.has(url.pathname)) return;
    event.preventDefault();
    window.history.pushState(null, '', url.pathname + url.search + url.hash);
    setLocation(url.pathname + url.search);
  }

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => request('/auth/me'),
    retry: false,
    refetchInterval: 30000,
  });
  const inbox = useQuery({
    queryKey: ['inbox'],
    queryFn: () => request('/inbox'),
    refetchInterval: 30000,
    retry: false,
    enabled: Boolean(me.data),
  });
  const logout = useMutation({
    mutationFn: () => request('/auth/logout', { method: 'POST' }),
    onSuccess: () => window.location.assign('/'),
  });

  if (me.isPending) return <div className="min-h-dvh flex items-center justify-center"><PageLoader /></div>;
  if (me.error?.status === 401) return <Login />;
  if (me.error) {
    return (
      <div className="max-w-md mx-auto p-6">
        <Notice tone="error">{me.error.message}</Notice>
      </div>
    );
  }

  const user = me.data;
  const [path, search = ''] = location.split('?');
  const params = new URLSearchParams(search);
  const task = params.get('task');
  const filesEnabled = user.features.monocle !== false || user.features.arrodes !== false || user.features.straus !== false;

  let routeKey;
  let page;
  if (task && path === '/') {
    routeKey = `task:${task}`;
    page = <TaskPanel id={task} features={user.features} />;
  } else if (path === '/') {
    routeKey = '/';
    page = <TaskBoard features={user.features} />;
  } else if (path === '/ameba') {
    routeKey = '/ameba';
    page = user.features.ameba !== false
      ? <AmebaWorkspace features={user.features} />
      : <EmptyState title="Ameba отключена" />;
  } else if (path === '/agents') {
    const legacyTab = params.get('tab');
    if (legacyTab === 'ameba') {
      routeKey = '/ameba';
      page = user.features.ameba !== false
        ? <AmebaWorkspace features={user.features} />
        : <EmptyState title="Ameba отключена" />;
    } else if (legacyTab === 'straus') {
      routeKey = '/calendar';
      page = <Calendar features={user.features} />;
    } else if (legacyTab === 'monocle') {
      if (task) {
        routeKey = `task:${task}`;
        page = <TaskPanel id={task} features={user.features} />;
      } else {
        routeKey = '/';
        page = <TaskBoard features={user.features} />;
      }
    } else {
      routeKey = '/agents';
      page = user.features.arrodes !== false
        ? <AgentsHub features={user.features} search={search} />
        : <EmptyState title="Arrodes отключён" />;
    }
  } else if (path === '/calendar') {
    routeKey = '/calendar';
    page = <Calendar features={user.features} />;
  } else if (path === '/files') {
    routeKey = '/files';
    page = filesEnabled
      ? <Files />
      : <EmptyState title="Файлы отключены" />;
  } else if (path.startsWith('/profile')) {
    routeKey = '/profile';
    page = <Account key="profile" user={user} search={search} />;
  } else if (path.startsWith('/admin')) {
    routeKey = '/profile';
    page = user.is_admin
      ? <Account key="profile-admin" user={user} initialTab="admin" search={search} />
      : <Notice tone="error">Требуется доступ администратора</Notice>;
  } else if (path.startsWith('/inbox')) {
    routeKey = '/inbox';
    page = <Inbox />;
  } else {
    routeKey = path;
    page = (
      <EmptyState
        title="Страница не найдена"
        action={<Button asChild><a href="/">К задачам</a></Button>}
      />
    );
  }

  return (
    <div className="min-h-dvh flex flex-col lg:flex-row bg-[#090909]" key={user.id} onClick={followLink}>
      <AppNavigation
        user={user}
        path={path}
        collapsed={menuCollapsed}
        onToggle={toggleMenu}
        onLogout={() => logout.mutate()}
        leaving={logout.isPending}
        inboxCount={inbox.data?.length ?? 0}
      />
      <main className="flex-1 min-w-0 px-3 sm:px-5 py-3 sm:py-4 pb-20 md:pb-6">
        <div className="w-full max-w-[1680px]">
          {logout.error && <Notice tone="error" className="mb-3">{logout.error.message}</Notice>}
          <PageError key={routeKey}>
            <Suspense fallback={<PageLoader />}>
              <PageTransition routeKey={routeKey}>
                {page}
              </PageTransition>
            </Suspense>
          </PageError>
        </div>
      </main>
    </div>
  );
}
