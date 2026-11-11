import { useState } from 'react';
import { Alert, Button, Paper, PasswordInput, Stack, Tabs, Title } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import Integrations from './Integrations';
import Cabinets from './Cabinets';
import AgentHistory from './AgentHistory';
import AgentMemory from './AgentMemory';
import { request } from './api';

export default function Account({ user }) {
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });
  const change = useMutation({
    mutationFn: () => request('/auth/password', { method: 'PATCH', body: JSON.stringify(passwords) }),
    onSuccess: () => setPasswords({ current_password: '', new_password: '' }),
  });
  return <Stack>
    <Title order={2}>Профиль · {user.username}</Title>
    <Tabs defaultValue="settings" keepMounted={false}>
    <Tabs.List><Tabs.Tab value="settings">Настройки</Tabs.Tab><Tabs.Tab value="history">История агентов</Tabs.Tab></Tabs.List>
    <Tabs.Panel value="settings" pt="md"><Stack>
    <AgentMemory />
    <Paper withBorder p="lg" maw={480}><form onSubmit={(event) => { event.preventDefault(); change.mutate(); }}><Stack>
      <Title order={4}>Изменить пароль</Title>
      <PasswordInput label="Текущий пароль" autoComplete="current-password" required value={passwords.current_password} onChange={(event) => setPasswords({ ...passwords, current_password: event.currentTarget.value })} />
      <PasswordInput label="Новый пароль" autoComplete="new-password" required minLength={12} maxLength={256} value={passwords.new_password} onChange={(event) => setPasswords({ ...passwords, new_password: event.currentTarget.value })} />
      <Button type="submit" loading={change.isPending}>Изменить пароль</Button>
      {change.error && <Alert color="red">{change.error.message}</Alert>}
      {change.isSuccess && <Alert color="green">Пароль изменён</Alert>}
    </Stack></form></Paper>
    {(user.features?.context !== false || user.features?.straus !== false) && <Cabinets />}
    <Title order={3}>Мои интеграции и ключи</Title>
    <Integrations features={user.features} />
    </Stack></Tabs.Panel>
    <Tabs.Panel value="history" pt="md"><AgentHistory /></Tabs.Panel>
    </Tabs>
  </Stack>;
}
