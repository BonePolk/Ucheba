import { lazy, Suspense, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { request } from './api';
import AgentHistory from './AgentHistory';
import AgentMemory from './AgentMemory';
import Cabinets from './Cabinets';
import Integrations from './Integrations';
import ProfileTimezone from './ProfileTimezone';
import { Button } from '@/components/ui/button';
import { Field, PasswordField } from '@/components/ui/input';
import { Badge, Notice, PageHeader, PageLoader, SectionHeader } from '@/components/ui/surface';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { TabTransition } from '@/lib/motion';

const Admin = lazy(() => import('./Admin'));

const TAB_ALIASES = {
  settings: 'profile',
  profile: 'profile',
  agents: 'agents',
  memory: 'memory',
  access: 'access',
  history: 'history',
  admin: 'admin',
};

export default function Account({ user, initialTab = 'profile', search = '' }) {
  const paramTab = new URLSearchParams(search).get('tab');
  const resolvedInitial = TAB_ALIASES[paramTab] ?? TAB_ALIASES[initialTab] ?? 'profile';
  const [tab, setTab] = useState(resolvedInitial);
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });

  const change = useMutation({
    mutationFn: () => request('/auth/password', { method: 'PATCH', body: JSON.stringify(passwords) }),
    onSuccess: () => setPasswords({ current_password: '', new_password: '' }),
  });

  const accessEnabled = user.features?.monocle !== false || user.features?.straus !== false || user.features?.arrodes !== false;
  const activeTab = (tab === 'access' && !accessEnabled) || (tab === 'admin' && !user.is_admin) ? 'profile' : tab;

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Настройки"
        badge={(
          <div className="flex items-center gap-1.5">
            <Badge variant="default">{user.username}</Badge>
            {user.is_admin && <Badge variant="accent">Админ</Badge>}
          </div>
        )}
      />

      <Tabs
        value={activeTab}
        onValueChange={(next) => {
          setTab(next);
          window.history.replaceState(null, '', `/profile?tab=${next}`);
        }}
      >
        <TabsList>
          <TabsTrigger value="profile">Профиль</TabsTrigger>
          <TabsTrigger value="agents">Агенты</TabsTrigger>
          <TabsTrigger value="memory">Память</TabsTrigger>
          {accessEnabled && <TabsTrigger value="access">Сохранённые входы</TabsTrigger>}
          <TabsTrigger value="history">История</TabsTrigger>
          {user.is_admin && <TabsTrigger value="admin">Администрирование</TabsTrigger>}
        </TabsList>

        <TabsContent value="profile">
          <TabTransition transitionKey="profile">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
              <div className="flex flex-col gap-3">
                <section className="border border-[#303030] bg-[#111111] p-3.5 flex flex-col gap-2">
                  <SectionHeader title="Аккаунт" />
                  <div className="grid grid-cols-[120px_1fr] gap-y-1.5 text-[12px]">
                    <span className="text-[#929292]">Пользователь</span>
                    <span className="font-medium text-[#EAEAEA]">{user.username}</span>
                    <span className="text-[#929292]">Роль</span>
                    <span className="text-[#EAEAEA]">{user.is_admin ? 'Администратор' : 'Пользователь'}</span>
                  </div>
                </section>
                <ProfileTimezone />
              </div>

              <section className="border border-[#303030] bg-[#111111] p-3.5 flex flex-col gap-2.5">
                <SectionHeader title="Изменить пароль" />
                <form
                  className="flex flex-col gap-2.5"
                  onSubmit={(event) => {
                    event.preventDefault();
                    change.mutate();
                  }}
                >
                  <Field label="Текущий пароль">
                    <PasswordField
                      autoComplete="current-password"
                      required
                      value={passwords.current_password}
                      onChange={(event) => setPasswords({ ...passwords, current_password: event.currentTarget.value })}
                    />
                  </Field>
                  <Field label="Новый пароль" hint="Мин. 12 символов">
                    <PasswordField
                      autoComplete="new-password"
                      required
                      minLength={12}
                      maxLength={256}
                      value={passwords.new_password}
                      onChange={(event) => setPasswords({ ...passwords, new_password: event.currentTarget.value })}
                    />
                  </Field>
                  <div>
                    <Button type="submit" variant="primary" loading={change.isPending}>
                      Сохранить пароль
                    </Button>
                  </div>
                  {change.error && <Notice tone="error">{change.error.message}</Notice>}
                  {change.isSuccess && <Notice tone="success">Пароль изменён.</Notice>}
                </form>
              </section>
            </div>
          </TabTransition>
        </TabsContent>

        <TabsContent value="agents">
          <TabTransition transitionKey="agents">
            <Integrations features={user.features} />
          </TabTransition>
        </TabsContent>

        <TabsContent value="memory">
          <TabTransition transitionKey="memory">
            <AgentMemory />
          </TabTransition>
        </TabsContent>

        {accessEnabled && (
          <TabsContent value="access">
            <TabTransition transitionKey="access">
              <Cabinets />
            </TabTransition>
          </TabsContent>
        )}

        <TabsContent value="history">
          <TabTransition transitionKey="history">
            <AgentHistory />
          </TabTransition>
        </TabsContent>

        {user.is_admin && (
          <TabsContent value="admin">
            <TabTransition transitionKey="admin">
              <Suspense fallback={<PageLoader />}>
                <Admin />
              </Suspense>
            </TabTransition>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
