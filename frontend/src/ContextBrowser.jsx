import { useState } from 'react';
import { Alert, Button, Collapse, Group, PasswordInput, Select, Stack, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function ContextBrowser({ taskId, disabled }) {
  const client = useQueryClient();
  const [opened, setOpened] = useState(false);
  const [formOpened, setFormOpened] = useState(false);
  const [credentials, setCredentials] = useState({ name: '', url: '', username: '', password: '' });
  const [typing, setTyping] = useState('');
  const [screen, setScreen] = useState(null);
  const path = `/integrations/tasks/${taskId}/context/browser`;
  const sitesPath = '/integrations/context/browser/sites';
  const sites = useQuery({ queryKey: ['context-browser-sites'], queryFn: () => request(sitesPath), enabled: opened });
  const selected = useQuery({ queryKey: ['context-browser', taskId], queryFn: () => request(path), enabled: opened });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['context-browser-sites'] });
    await client.invalidateQueries({ queryKey: ['context-browser', taskId] });
    await client.invalidateQueries({ queryKey: ['context', taskId] });
    await client.invalidateQueries({ queryKey: ['tasks'] });
  };
  const select = useMutation({
    mutationFn: (site_id) => request(path, { method: 'PUT', body: JSON.stringify({ site_id }) }),
    onSuccess: () => { setScreen(null); return refresh(); },
  });
  const save = useMutation({
    mutationFn: () => request(sitesPath, { method: 'POST', body: JSON.stringify(credentials) }),
    onSuccess: async (site) => {
      setCredentials({ name: '', url: '', username: '', password: '' });
      setFormOpened(false);
      select.mutate(site.id);
      await refresh();
    },
  });
  const forget = useMutation({
    mutationFn: () => request(`${sitesPath}/${selected.data.site_id}`, { method: 'DELETE' }),
    onSuccess: () => select.mutate(null),
  });
  const action = useMutation({
    mutationFn: (values) => request(`${path}/action`, { method: 'POST', body: JSON.stringify({ site_id: selected.data.site_id, ...values }) }),
    onSuccess: (data) => { setScreen(data); return refresh(); },
  });
  const busy = disabled || select.isPending || save.isPending || forget.isPending || action.isPending;
  const siteId = selected.data?.site_id;
  const available = sites.data?.some((site) => site.id === siteId);
  const error = sites.error || selected.error || select.error || save.error || forget.error || action.error;
  const field = (name) => (event) => setCredentials({ ...credentials, [name]: event.currentTarget.value });
  return <Stack gap="xs">
    <Button variant="default" onClick={() => setOpened(!opened)}>{opened ? 'Скрыть браузер ЛК' : 'Браузер личного кабинета'}</Button>
    <Collapse in={opened}><Stack gap="sm">
      <Text size="sm">Выбери ЛК для этой задачи. Luna сможет автоматически войти и читать условия работ. Пароль подставляет браузер; модель его не получает.</Text>
      <Select label="Личный кабинет для Luna" placeholder="Доступ к браузеру не выбран" clearable value={available ? siteId : null} data={(sites.data ?? []).map((site) => ({ value: site.id, label: site.name }))} disabled={busy || sites.isPending} onChange={(id) => select.mutate(id)} />
      <Button variant="subtle" disabled={busy} onClick={() => setFormOpened(!formOpened)}>Добавить личный кабинет</Button>
      {formOpened && <Stack gap="xs">
        <TextInput label="Название" placeholder="Например: ЛК университета" value={credentials.name} onChange={field('name')} maxLength={100} />
        <TextInput label="HTTPS-адрес страницы входа" placeholder="https://…" value={credentials.url} onChange={field('url')} autoComplete="off" />
        <TextInput label="Логин ЛК" value={credentials.username} onChange={field('username')} autoComplete="off" />
        <PasswordInput label="Пароль ЛК" value={credentials.password} onChange={field('password')} autoComplete="new-password" />
        <Button disabled={busy || !credentials.name || !credentials.url || !credentials.username || !credentials.password} onClick={() => save.mutate()}>Сохранить и разрешить Luna вход</Button>
      </Stack>}
      {available && <>
        <Group>
          <Button disabled={busy} onClick={() => action.mutate({ action: 'login' })}>Войти автоматически</Button>
          <Button variant="default" disabled={busy} onClick={() => action.mutate({ action: 'open' })}>Открыть страницу входа</Button>
          <Button variant="default" disabled={busy} onClick={() => action.mutate({ action: 'state' })}>Обновить снимок</Button>
          <Button color="red" variant="subtle" disabled={busy} onClick={() => {
            if (window.confirm('Удалить сохранённый вход? Все браузерные сессии твоего аккаунта будут очищены.')) forget.mutate();
          }}>Забыть вход</Button>
        </Group>
        <Text size="xs" c="dimmed">Для 2FA и CAPTCHA используй снимок: нажми на поле, введи значение ниже и нажми «Ввести». Пароль и код не отправляются Luna.</Text>
        {screen?.notice && <Alert>{screen.notice.reason}</Alert>}
        {screen?.image && <>
          <Text size="xs" c="dimmed">{screen.url}</Text>
          <img src={`data:image/png;base64,${screen.image}`} alt="Браузер личного кабинета — нажми на нужный элемент" style={{ width: '100%', maxWidth: 1100, cursor: busy ? 'wait' : 'crosshair', borderRadius: 8 }} onClick={(event) => {
            if (busy) return;
            const rect = event.currentTarget.getBoundingClientRect();
            action.mutate({ action: 'click', x: Math.min(1099, Math.max(0, Math.floor((event.clientX - rect.left) * screen.width / rect.width))), y: Math.min(749, Math.max(0, Math.floor((event.clientY - rect.top) * screen.height / rect.height))) });
          }} />
          <Group align="end">
            <PasswordInput label="Ввести в выбранное поле браузера" value={typing} onChange={(event) => setTyping(event.currentTarget.value)} autoComplete="off" />
            <Button disabled={busy || !typing} onClick={() => { const value = typing; setTyping(''); action.mutate({ action: 'type', text: value }); }}>Ввести</Button>
            {['Enter', 'Tab', 'Control+A', 'Backspace'].map((key) => <Button key={key} variant="default" disabled={busy} onClick={() => action.mutate({ action: 'press', text: key })}>{key}</Button>)}
          </Group>
          <Group>
            <Button variant="default" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: -500 })}>Выше</Button>
            <Button variant="default" disabled={busy} onClick={() => action.mutate({ action: 'scroll', delta: 500 })}>Ниже</Button>
            <Button variant="default" disabled={busy} onClick={() => action.mutate({ action: 'back' })}>Назад</Button>
          </Group>
        </>}
      </>}
      {error && <Alert color="red">{error.message}</Alert>}
    </Stack></Collapse>
  </Stack>;
}
