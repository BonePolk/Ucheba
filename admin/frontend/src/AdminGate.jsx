import { useState } from 'react';
import { Alert, Button, Group, Loader, Paper, PasswordInput, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function AdminGate({ children }) {
  const client = useQueryClient();
  const [password, setPassword] = useState('');
  const status = useQuery({ queryKey: ['owner'], queryFn: () => request('/auth/status'), retry: false });
  const login = useMutation({
    mutationFn: () => request('/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),
    onSuccess: () => { setPassword(''); return client.invalidateQueries({ queryKey: ['owner'] }); },
  });
  const logout = useMutation({
    mutationFn: () => request('/auth/logout', { method: 'POST' }),
    onSuccess: () => { client.removeQueries({ queryKey: ['integrations'] }); client.removeQueries({ queryKey: ['inbox'] }); return client.invalidateQueries({ queryKey: ['owner'] }); },
  });
  if (status.isPending) return <Loader />;
  if (status.error) return <Alert color="red">{status.error.message}</Alert>;
  if (!status.data.configured) return <Alert color="yellow">{status.data.detail}. Инструкции настройки — в README. Обычные задачи работают без интеграций.</Alert>;
  if (!status.data.authenticated) return (
    <Paper withBorder p="lg" maw={440}>
      <form onSubmit={(event) => { event.preventDefault(); login.mutate(); }}>
        <Stack>
          <Text fw={500}>Доступ владельца</Text>
          <PasswordInput label="Пароль администратора" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.currentTarget.value)} required />
          {login.error && <Alert color="red">{login.error.message}</Alert>}
          <Button type="submit" loading={login.isPending}>Войти</Button>
        </Stack>
      </form>
    </Paper>
  );
  return <Stack><Group justify="flex-end"><Button size="xs" variant="subtle" onClick={() => logout.mutate()}>Выйти из управления</Button></Group>{children}</Stack>;
}
