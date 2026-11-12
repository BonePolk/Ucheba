import { useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Loader, MultiSelect, Paper, PasswordInput, SimpleGrid, Stack, Switch, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import BotSettings from './BotSettings';
import AgentSettings from './AgentSettings';
import ModelPicker from './ModelPicker';

function ConnectionInfo({ value }) {
  return <>
    {value.last_error && <Alert color="red">{value.last_error}</Alert>}
    <Text size="sm" c="dimmed">Последний успешный запрос: {value.last_success ? new Date(value.last_success).toLocaleString() : 'ещё не было'}</Text>
  </>;
}

export default function Integrations({ features = {} }) {
  const query = useQuery({ queryKey: ['integrations'], queryFn: () => request('/integrations'), refetchInterval: 15000, retry: false });
  if (query.isPending) return <div className="page-loading"><Loader /></div>;
  if (query.error && !query.data) return <Alert color="red">{query.error.message}</Alert>;
  return <>
    {query.error && <Alert color="red">{query.error.message}</Alert>}
    <IntegrationForms data={query.data} features={features} />
  </>;
}

function IntegrationForms({ data, features }) {
  const client = useQueryClient();
  const [telegram, setTelegram] = useState({ enabled: data.telegram.enabled, api_id: '', api_hash: '', phone: '' });
  const [arrodes, setArrodes] = useState({ enabled: data.arrodes.enabled, auto_prepare: data.arrodes.auto_prepare, model: data.arrodes.model ?? '' });
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState('');
  const [chatIds, setChatIds] = useState(data.telegram.chat_ids.map(String));
  const [notice, setNotice] = useState('');
  const chats = useQuery({ queryKey: ['integrations', 'chats'], queryFn: () => request('/integrations/telegram/chats'), enabled: false, retry: false });
  const [arrodesOpened, setArrodesOpened] = useState(data.arrodes.enabled);
  const arrodesState = useQuery({ queryKey: ['integrations', 'arrodes'], enabled: features.arrodes !== false && arrodesOpened, queryFn: () => request('/integrations/arrodes/status'), refetchInterval: 10000, retry: false });
  const mutation = useMutation({
    mutationFn: ({ path, body, method = 'POST' }) => request(`/integrations/${path}`, { method, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) }),
    onSuccess: async (result, variables) => {
      if (variables.method === 'PATCH') {
        const setter = { telegram: setTelegram, arrodes: setArrodes }[variables.path];
        if (setter) setter((current) => Object.fromEntries(Object.entries(current).map(([key, value]) => [
          key, ['api_id', 'api_hash', 'phone', 'api_key', 'api_token'].includes(key) ? '' : variables.body[key] ?? value,
        ])));
      }
      if (result.step) { setStep(result.step); setCode(''); setPassword(''); }
      setNotice(result.messages !== undefined ? `Обработано сообщений: ${result.messages}, создано задач: ${result.created}, на подтверждение: ${result.pending}` : 'Готово');
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      if (variables.path.startsWith('arrodes/')) client.invalidateQueries({ queryKey: ['integrations', 'arrodes'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['inbox'] });
    },
  });
  const run = (path, body, method) => mutation.mutate({ path, body, method });
  const save = (name, values) => mutation.mutate({ path: name, body: { ...values, ...(features[name] === false ? { enabled: false } : {}) }, method: 'PATCH' });
  const saving = (name) => mutation.isPending && mutation.variables?.path === name;
  const field = (setter, name) => (event) => {
    const value = event.currentTarget.value;
    setter((current) => ({ ...current, [name]: value }));
  };
  const toggle = (setter, name) => (event) => {
    const checked = event.currentTarget.checked;
    setter((current) => ({ ...current, [name]: checked }));
  };
  const error = mutation.error || chats.error || arrodesState.error;
  return <Stack>
    {error && <Alert color="red">{error.message}</Alert>}
    {notice && <Alert color="purple" withCloseButton onClose={() => setNotice('')}>{notice}</Alert>}
    <SimpleGrid cols={{ base: 1, md: 2 }} style={{ alignItems: 'start' }}>
      {features.monocle !== false && <AgentSettings name="monocle" title="Monocle" data={data.monocle} />}
      {features.ameba !== false && data.ameba && <AgentSettings name="ameba" title="Ameba" data={data.ameba} />}
      {features.straus !== false && data.straus && <AgentSettings name="straus" title="Straus" data={data.straus} />}

      {features.telegram !== false && <Paper withBorder p="md"><Stack gap="sm">
        <Group justify="space-between"><Title order={4}>Telegram</Title><Badge color={data.telegram_connected ? 'green' : 'gray'}>{data.telegram_connected ? 'Подключён' : 'Не подключён'}</Badge></Group>
        <PasswordInput label="API ID" placeholder={data.telegram.configured.api_id ? 'Сохранён' : ''} value={telegram.api_id} onChange={field(setTelegram, 'api_id')} />
        <PasswordInput label="API Hash" placeholder={data.telegram.configured.api_hash ? 'Сохранён' : ''} value={telegram.api_hash} onChange={field(setTelegram, 'api_hash')} />
        <PasswordInput label="Телефон" placeholder={data.telegram.configured.phone ? 'Сохранён' : '+7…'} value={telegram.phone} onChange={field(setTelegram, 'phone')} />
        <Group><Button loading={saving('telegram')} disabled={mutation.isPending} onClick={() => save('telegram', telegram)}>Сохранить</Button><Button variant="default" disabled={mutation.isPending || data.telegram_connected} onClick={() => run('telegram/code')}>Подключить аккаунт</Button></Group>
        {step === 'code' && <Group align="end"><PasswordInput label="Код Telegram" value={code} onChange={(event) => setCode(event.currentTarget.value)} /><Button disabled={mutation.isPending || !code} onClick={() => run('telegram/login', { code })}>Подтвердить</Button></Group>}
        {step === 'password' && <Group align="end"><PasswordInput label="Пароль двухфакторной авторизации" value={password} onChange={(event) => setPassword(event.currentTarget.value)} /><Button disabled={mutation.isPending || !password} onClick={() => run('telegram/login', { password })}>Войти</Button></Group>}
        <Button variant="default" loading={chats.isFetching} onClick={() => chats.refetch()}>Загрузить чаты</Button>
        <MultiSelect label="Разрешённые чаты" searchable value={chatIds} onChange={setChatIds} data={(chats.data ?? data.telegram.chat_ids.map((id) => ({ id, title: String(id) }))).map((chat) => ({ value: String(chat.id), label: chat.title }))} />
        <Group><Button variant="light" disabled={mutation.isPending || !chats.data} onClick={() => run('telegram/chats', { chat_ids: chatIds.map(Number) })}>Сохранить выбор</Button></Group>
        <Button color="red" variant="subtle" disabled={mutation.isPending} onClick={() => { if (window.confirm('Отключить Telegram?')) run('telegram/disconnect'); }}>Отключить аккаунт</Button>
        <ConnectionInfo value={data.telegram} />
      </Stack></Paper>}
      {features.arrodes !== false && <Paper withBorder p="md"><Stack gap="sm">
        <Group justify="space-between"><Title order={4}>Arrodes</Title><Badge color={arrodesState.data?.authorized ? 'green' : 'gray'}>{arrodesState.data?.authorized ? 'Вход выполнен' : 'Нет авторизации'}</Badge></Group>
        <Text size="sm" c="dimmed">Выполняет поручения и готовит файлы. Вход через ChatGPT.</Text>
        {arrodesState.data && !arrodesState.data.sandbox_available && <Alert color="yellow">Linux sandbox недоступен. {arrodesState.data.sandbox_error || 'Выполнение заблокировано; режим без защиты не используется.'}</Alert>}
        <Group><Button disabled={mutation.isPending} onClick={() => { setArrodesOpened(true); run('arrodes/connect'); }}>Подключить через ChatGPT</Button><Button variant="default" disabled={mutation.isPending} onClick={() => { setArrodesOpened(true); run('arrodes/check'); }}>Проверить доступность</Button></Group>
        {arrodesState.data?.device?.state === 'waiting' && <Alert color="purple"><Stack gap="xs"><Text>Подтвердите вход в браузере:</Text>{arrodesState.data.device.url && <Anchor href={arrodesState.data.device.url} target="_blank" rel="noreferrer">Открыть страницу OpenAI</Anchor>}<Text fw={700}>{arrodesState.data.device.code ?? 'Ожидание кода…'}</Text></Stack></Alert>}
        {['failed', 'expired'].includes(arrodesState.data?.device?.state) && <Alert color="yellow">Вход не завершён или код истёк. Запросите новый код.</Alert>}
        <ModelPicker path="/integrations/arrodes/models" chatgpt value={arrodes.model} onChange={(model) => setArrodes((current) => ({ ...current, model }))} />
        <Switch label="Включить агента" checked={arrodes.enabled} onChange={toggle(setArrodes, 'enabled')} />
        <Switch label="Автоматически готовить материалы для принятых задач из Telegram" checked={arrodes.auto_prepare} onChange={toggle(setArrodes, 'auto_prepare')} />
        <Group><Button loading={saving('arrodes')} disabled={mutation.isPending} onClick={() => save('arrodes', arrodes)}>Сохранить</Button><Button color="red" variant="subtle" disabled={mutation.isPending} onClick={() => run('arrodes/disconnect')}>Отключить</Button></Group>
        <details><summary style={{ cursor: 'pointer', fontSize: 13 }}>Состояние подключения</summary><ConnectionInfo value={data.arrodes} /></details>
      </Stack></Paper>}
      {features.collector !== false && <Paper withBorder p="md"><BotSettings name="collector" data={data.collector} /></Paper>}
      {features.notifications !== false && <Paper withBorder p="md"><BotSettings name="notifications" data={data.notifications} /></Paper>}
    </SimpleGrid>
  </Stack>;
}
