import { useState } from 'react';
import { Alert, Badge, Button, ColorInput, Group, Modal, Paper, PasswordInput, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import ContextBrowser from './ContextBrowser';
import LuchebaBrowser from './LuchebaBrowser';

const empty = { name: '', url: '', username: '', password: '', description: '', group: '', color: '#7f49b4' };

export default function Cabinets() {
  const client = useQueryClient();
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);
  const [viewing, setViewing] = useState(null);
  const sites = useQuery({ queryKey: ['context-browser-sites'], queryFn: () => request('/integrations/context/browser/sites') });
  const queue = useQuery({ queryKey: ['agent-queue'], queryFn: () => request('/integrations/queue'), refetchInterval: 2000 });
  const save = useMutation({
    mutationFn: () => request(`/integrations/context/browser/sites${editing.id ? `/${editing.id}` : ''}`, { method: editing.id ? 'PATCH' : 'POST', body: JSON.stringify(form) }),
    onSuccess: async () => { setEditing(null); setForm(empty); await client.invalidateQueries({ queryKey: ['context-browser-sites'] }); },
  });
  const field = (name) => (event) => setForm({ ...form, [name]: event.currentTarget.value });
  const visible = (sites.data ?? []).filter((site) => `${site.name} ${site.group} ${site.url}`.toLowerCase().includes(search.toLowerCase()));
  return <Paper withBorder p="lg" radius="md"><Stack>
    <Group justify="space-between"><Title order={4}>Личные кабинеты</Title><Button size="xs" onClick={() => { setForm(empty); setEditing({}); }}>Добавить</Button></Group>
    <TextInput placeholder="Найти кабинет, группу или адрес" value={search} onChange={(event) => setSearch(event.currentTarget.value)} />
    <SimpleGrid cols={{ base: 1, sm: 2 }}>
      {visible.map((site) => <Paper key={site.id} withBorder p="sm" radius="md" style={{ borderTop: `3px solid ${site.color}` }}><Stack gap={6}>
        <Group justify="space-between"><Text fw={600} size="sm">{site.name}</Text>{site.group && <Badge size="xs" variant="light">{site.group}</Badge>}</Group>
        <Text size="xs" c="dimmed" lineClamp={1}>{site.url}</Text>
        {site.description && <Text size="xs" lineClamp={2}>{site.description}</Text>}
        <Group gap={6}><Button size="compact-xs" variant="light" onClick={() => setViewing(site)}>Открыть</Button><Button size="compact-xs" variant="subtle" onClick={() => { setForm({ ...empty, ...site, username: '', password: '' }); setEditing(site); }}>Настроить</Button></Group>
      </Stack></Paper>)}
    </SimpleGrid>
    {!sites.isPending && !visible.length && <Text size="sm" c="dimmed">Кабинетов пока нет</Text>}
    {sites.error && <Alert color="red">{sites.error.message}</Alert>}
    <Modal opened={Boolean(editing)} onClose={() => setEditing(null)} title={editing?.id ? 'Настройки кабинета' : 'Новый кабинет'}>
      <Stack gap="sm">
        <TextInput label="Название" value={form.name} onChange={field('name')} maxLength={100} />
        <TextInput label="Страница входа" value={form.url} onChange={field('url')} />
        <Group grow><TextInput label="Группа" value={form.group} onChange={field('group')} maxLength={80} /><ColorInput label="Цвет" value={form.color} onChange={(color) => setForm({ ...form, color })} /></Group>
        <TextInput label="Описание" value={form.description} onChange={field('description')} maxLength={300} />
        <TextInput label="Логин" placeholder={editing?.id ? 'Оставить прежний' : ''} value={form.username} onChange={field('username')} autoComplete="off" />
        <PasswordInput label="Пароль" placeholder={editing?.has_credentials ? 'Сохранён; пустое поле сохраняет его' : ''} value={form.password} onChange={field('password')} autoComplete="new-password" />
        <Button loading={save.isPending} disabled={!form.name || !form.url || (!editing?.id && (!form.username || !form.password))} onClick={() => save.mutate()}>Сохранить</Button>
        {save.error && <Alert color="red">{save.error.message}</Alert>}
      </Stack>
    </Modal>
    <Modal opened={Boolean(viewing)} onClose={() => setViewing(null)} title={viewing?.name} size="xl">
      {viewing && <Stack><ContextBrowser key={viewing.id} initialSiteId={viewing.id} disabled={queue.data?.busy} /><LuchebaBrowser active /></Stack>}
      {queue.data?.busy && <Text size="xs" c="dimmed">Агенты используют браузер; ручное управление доступно после их завершения.</Text>}
    </Modal>
  </Stack></Paper>;
}
