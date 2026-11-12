import { useState } from 'react';
import { Alert, Button, Collapse, Group, Modal, PasswordInput, Select, Stack, Text, TextInput } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import useBrowserScreen from './useBrowserScreen';

export default function ContextBrowser({ taskId, disabled, initialSiteId = null }) {
  const client = useQueryClient();
  const [opened, setOpened] = useState(Boolean(initialSiteId));
  const [profileId, setProfileId] = useState(initialSiteId);
  const [formOpened, setFormOpened] = useState(false);
  const [credentials, setCredentials] = useState({ name: '', url: '', username: '', password: '' });
  const [typing, setTyping] = useState('');
  const [screen, setScreen] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const live = useBrowserScreen(opened && Boolean(screen));
  const path = `/integrations/tasks/${taskId}/monocle/browser`;
  const sitesPath = '/integrations/monocle/browser/sites';
  const sites = useQuery({ queryKey: ['context-browser-sites'], queryFn: () => request(sitesPath), enabled: opened });
  const selected = useQuery({ queryKey: ['context-browser', taskId], queryFn: () => request(path), enabled: opened && Boolean(taskId) });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['context-browser-sites'] });
    if (taskId) {
      await client.invalidateQueries({ queryKey: ['context-browser', taskId] });
      await client.invalidateQueries({ queryKey: ['context', taskId] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    }
  };
  const select = useMutation({
    mutationFn: (site_id) => taskId ? request(path, { method: 'PUT', body: JSON.stringify({ site_id }) }) : Promise.resolve({ site_id }),
    onSuccess: (data) => { setProfileId(data.site_id); setScreen(null); return refresh(); },
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
    mutationFn: () => request(`${sitesPath}/${siteId}`, { method: 'DELETE' }),
    onSuccess: () => { client.setQueryData(['luna-browser-screen'], null); select.mutate(null); },
  });
  const action = useMutation({
    mutationFn: (values) => request(taskId ? `${path}/action` : '/integrations/monocle/browser/manual', { method: 'POST', body: JSON.stringify({ site_id: siteId, ...values }) }),
    onSuccess: (data) => { setScreen(data); return refresh(); },
  });
  const busy = disabled || select.isPending || save.isPending || forget.isPending || action.isPending;
  const siteId = taskId ? selected.data?.site_id : profileId;
  const available = sites.data?.some((site) => site.id === siteId);
  const error = sites.error || selected.error || select.error || save.error || forget.error || action.error;
  const field = (name) => (event) => setCredentials({ ...credentials, [name]: event.currentTarget.value });
  function screenImage(large = false) {
    return <img width={live.frame?.width ?? screen?.width ?? 1100} height={live.frame?.height ?? screen?.height ?? 750} src={live.frame ? `data:${live.frame.mime};base64,${live.frame.image}` : `data:${screen?.mime ?? 'image/png'};base64,${screen?.image}`} alt="Браузер личного кабинета — нажми на нужный элемент" style={{ width: '100%', height: 'auto', maxWidth: large ? 1100 : 360, display: 'block', cursor: busy ? 'wait' : 'crosshair', borderRadius: 0 }} onClick={(event) => {
      if (busy) return;
      const rect = event.currentTarget.getBoundingClientRect();
      action.mutate({ action: 'click', x: Math.min(1099, Math.max(0, Math.floor((event.clientX - rect.left) * (screen?.width ?? live.frame?.width ?? 1100) / rect.width))), y: Math.min(749, Math.max(0, Math.floor((event.clientY - rect.top) * (screen?.height ?? live.frame?.height ?? 750) / rect.height))) });
    }} />;
  }
  return <Stack gap="xs">
    <Button variant="default" onClick={() => setOpened(!opened)}>{opened ? 'Скрыть браузер ЛК' : 'Браузер личного кабинета'}</Button>
    <Collapse in={opened}><Stack gap="sm">
      <Text size="sm">Monocle сам выбирает подходящий кабинет из сохранённых и может искать информацию в Google. Здесь можно выбрать ЛК для ручного просмотра и как подсказку агенту. Пароль подставляет браузер; модель его не получает.</Text>
      <Select label="Личный кабинет для просмотра / подсказки Monocle" placeholder="Monocle выберет кабинет сам" clearable value={available ? siteId : null} data={(sites.data ?? []).map((site) => ({ value: site.id, label: site.name }))} disabled={busy || sites.isPending} onChange={(id) => select.mutate(id)} />
      <Button variant="subtle" disabled={busy} onClick={() => setFormOpened(!formOpened)}>Добавить личный кабинет</Button>
      {formOpened && <Stack gap="xs">
        <TextInput label="Название" placeholder="Например: ЛК университета" value={credentials.name} onChange={field('name')} maxLength={100} />
        <TextInput label="Адрес страницы входа" placeholder="http://ibks.spbstu.ru:3508" value={credentials.url} onChange={field('url')} autoComplete="off" />
        <TextInput label="Логин ЛК" value={credentials.username} onChange={field('username')} autoComplete="off" />
        <PasswordInput label="Пароль ЛК" value={credentials.password} onChange={field('password')} autoComplete="new-password" />
        <Button disabled={busy || !credentials.name || !credentials.url || !credentials.username || !credentials.password} onClick={() => save.mutate()}>Сохранить и разрешить Monocle вход</Button>
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
        <Text size="xs" c="dimmed">Для 2FA и CAPTCHA используй снимок: нажми на поле, введи значение ниже и нажми «Ввести». Пароль и код не отправляются Monocle.</Text>
        {live.error && <Text size="xs" c="yellow">Переподключаем экран браузера…</Text>}
        {screen?.notice && <Alert>{screen.notice.reason}</Alert>}
        {(screen?.image || live.frame?.image) && <>
          <Text size="xs" c="dimmed">{screen?.url ?? live.frame?.url}</Text>
          {screenImage()}
          <Button size="compact-xs" variant="subtle" onClick={() => setExpanded(true)}>Увеличить браузер ↗</Button>
          <Modal opened={expanded} onClose={() => setExpanded(false)} title="Личный кабинет" size="xl" centered>{screenImage(true)}</Modal>
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
