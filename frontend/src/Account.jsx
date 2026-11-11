import { lazy, useState } from 'react';
import { Alert, Button, Paper, PasswordInput, Stack, Tabs, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import Integrations from './Integrations';
import Cabinets from './Cabinets';
import AgentHistory from './AgentHistory';
import AgentMemory from './AgentMemory';
import ProfileTimezone from './ProfileTimezone';
import { request } from './api';

const Admin = lazy(() => import('./Admin'));

export default function Account({ user, initialTab = 'settings' }) {
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });
  const change = useMutation({
    mutationFn: () => request('/auth/password', { method: 'PATCH', body: JSON.stringify(passwords) }),
    onSuccess: () => setPasswords({ current_password: '', new_password: '' }),
  });
  return <Stack>
    <Title order={2}>Профиль · {user.username}</Title>
    <Tabs defaultValue={initialTab} keepMounted={false}>
    <Tabs.List><Tabs.Tab value="settings">Обо мне</Tabs.Tab><Tabs.Tab value="agents">Агенты</Tabs.Tab>{(user.features?.context !== false || user.features?.straus !== false) && <Tabs.Tab value="access">Входы</Tabs.Tab>}<Tabs.Tab value="history">История агентов</Tabs.Tab>{user.is_admin && <Tabs.Tab value="admin">Админка</Tabs.Tab>}</Tabs.List>
    <Tabs.Panel value="settings" pt="md"><Stack>
    <AgentMemory />
    <ProfileTimezone />
    <details className="account-password"><summary>Изменить пароль</summary><Paper withBorder p="md" maw={480} mt="sm"><form onSubmit={(event) => { event.preventDefault(); change.mutate(); }}><Stack>
      <Title order={4}>Изменить пароль</Title>
      <PasswordInput label="Текущий пароль" autoComplete="current-password" required value={passwords.current_password} onChange={(event) => setPasswords({ ...passwords, current_password: event.currentTarget.value })} />
      <PasswordInput label="Новый пароль" autoComplete="new-password" required minLength={12} maxLength={256} value={passwords.new_password} onChange={(event) => setPasswords({ ...passwords, new_password: event.currentTarget.value })} />
      <Button type="submit" loading={change.isPending}>Изменить пароль</Button>
      {change.error && <Alert color="red">{change.error.message}</Alert>}
      {change.isSuccess && <Alert color="green">Пароль изменён</Alert>}
    </Stack></form></Paper></details>
    </Stack></Tabs.Panel>
    <Tabs.Panel value="access" pt="md">{(user.features?.context !== false || user.features?.straus !== false) && <Cabinets />}</Tabs.Panel>
    <Tabs.Panel value="agents" pt="md"><Stack>
    <Integrations features={user.features} />
    </Stack></Tabs.Panel>
    <Tabs.Panel value="history" pt="md"><AgentHistory /></Tabs.Panel>
    {user.is_admin && <Tabs.Panel value="admin" pt="md"><Admin /></Tabs.Panel>}
    </Tabs>
  </Stack>;
}
