import { useState } from 'react';
import { Alert, Anchor, Button, Container, Group, Loader, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { request } from './api';
import TaskBoard from './TaskBoard';
import Account from './Account';
import Admin from './Admin';
import Inbox from './Inbox';
import TaskAutomation from './TaskAutomation';

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
    <TextInput label="Логин" description="Латинские буквы, цифры, точка, дефис или подчёркивание" autoComplete="username" required minLength={3} maxLength={64} value={credentials.username} onChange={(event) => setCredentials({ ...credentials, username: event.currentTarget.value })} />
    <PasswordInput label="Пароль" autoComplete={registering ? 'new-password' : 'current-password'} required minLength={12} maxLength={256} value={credentials.password} onChange={(event) => setCredentials({ ...credentials, password: event.currentTarget.value })} />
    {login.error && <Alert color="red">{login.error.message}</Alert>}
    <Button type="submit" loading={login.isPending}>{registering ? 'Создать аккаунт' : 'Войти'}</Button>
    {registration.data?.enabled && <Button variant="subtle" onClick={() => { setRegister(!registering); login.reset(); }}>{registering ? 'Уже есть аккаунт' : 'Регистрация'}</Button>}
  </Stack></form></Paper></Container>;
}

function TaskPanel({ id, features }) {
  const task = useQuery({ queryKey: ['tasks', id], queryFn: () => request(`/tasks/${id}`) });
  if (task.isPending) return <Loader />;
  if (task.error) return <Alert color="red">{task.error.message}</Alert>;
  return <TaskAutomation key={id} task={task.data} features={features} />;
}

export default function App() {
  const me = useQuery({ queryKey: ['me'], queryFn: () => request('/auth/me'), retry: false, refetchInterval: 30000 });
  const logout = useMutation({ mutationFn: () => request('/auth/logout', { method: 'POST' }), onSuccess: () => window.location.assign('/') });
  if (me.isPending) return <Container py="xl"><Loader /></Container>;
  if (me.error?.status === 401) return <Login />;
  if (me.error) return <Container py="xl"><Alert color="red">{me.error.message}</Alert></Container>;
  const user = me.data;
  const path = window.location.pathname;
  const task = new URLSearchParams(window.location.search).get('task');
  const board = path === '/' && !task;
  const inboxEnabled = user.features.gemini !== false;
  return <Stack gap="md" key={user.id}>
    <Container size="xl" w="100%" pt="md"><Group justify="space-between">
      <Group gap="md"><Anchor href="/">Задачи и архив</Anchor><Anchor href="/profile">Профиль</Anchor>{inboxEnabled && <Anchor href="/inbox">На подтверждение</Anchor>}{user.is_admin && <Anchor href="/admin">Админка</Anchor>}</Group>
      <Group><Text size="sm">{user.username}</Text><Button size="xs" variant="default" loading={logout.isPending} onClick={() => logout.mutate()}>Выйти</Button></Group>
    </Group>{logout.error && <Alert color="red">{logout.error.message}</Alert>}</Container>
    {board ? <TaskBoard features={user.features} /> : <Container size="xl" w="100%" py="lg">{task ? <TaskPanel id={task} features={user.features} /> : path.startsWith('/profile') ? <Account user={user} /> : path.startsWith('/admin') ? user.is_admin ? <Admin /> : <Alert color="red">Требуется доступ администратора</Alert> : path.startsWith('/inbox') ? inboxEnabled ? <Inbox /> : <Alert>Функция отключена</Alert> : <Alert>Страница не найдена</Alert>}</Container>}
  </Stack>;
}
