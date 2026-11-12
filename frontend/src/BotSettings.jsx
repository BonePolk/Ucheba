import { useState } from 'react';
import { Alert, Button, Group, MultiSelect, NumberInput, PasswordInput, Stack, Switch, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function BotSettings({ name, data }) {
  const client = useQueryClient();
  const [token, setToken] = useState('');
  const [enabled, setEnabled] = useState(data.enabled);
  const [owner, setOwner] = useState(data.owner_id ?? '');
  const [minutes, setMinutes] = useState(data.lead_minutes ?? 30);
  const [chats, setChats] = useState((data.chat_ids ?? []).map(String));
  const [notice, setNotice] = useState('');
  const collector = name === 'collector';
  const available = useQuery({ queryKey: ['integrations', 'collector-chats'], queryFn: () => request('/integrations/bots/collector/chats'), enabled: false, retry: false });
  const action = useMutation({
    mutationFn: ({ path, body, method = 'POST' }) => request(`/integrations/${path}`, { method, body: JSON.stringify(body) }),
    onSuccess: async (result, variables) => {
      if (variables.method === 'PATCH') {
        if ('bot_token' in variables.body) setToken('');
        if ('enabled' in variables.body) setEnabled(variables.body.enabled);
      }
      setNotice(result.username ? `Подключён @${result.username}` : result.messages !== undefined ? `Обработано сообщений: ${result.messages}` : 'Сохранено');
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['inbox'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
  return <Stack gap="sm">
    <Title order={4}>{collector ? 'Бот-сборщик задач · aiogram' : 'Бот уведомлений · aiogram'}</Title>
    <PasswordInput label="Токен BotFather" value={token} placeholder={data.configured.bot_token ? 'Сохранён' : ''} onChange={(event) => setToken(event.currentTarget.value)} />
    <Switch label="Включить бота" checked={enabled} onChange={(event) => setEnabled(event.currentTarget.checked)} />
    {!collector && <>
      <NumberInput label="Telegram ID владельца" allowNegative={false} allowDecimal={false} value={owner} onChange={setOwner} />
      <NumberInput label="Напоминать за сколько минут" min={1} max={1440} value={minutes} onChange={setMinutes} />
      <Text size="xs" c="dimmed">Напишите /start именно этому боту. Он отправляет только владельцу напоминания о сроках, новых предложениях и готовых результатах Codex.</Text>
    </>}
    {collector && <Text size="xs" c="dimmed">Добавьте отдельного бота в нужные группы. Отключите Privacy Mode у BotFather для чтения обычных сообщений. Бот не читает вашу историю или личные переписки от имени аккаунта. После включения отправьте сообщение в группу и загрузите чаты. Текст сохраняется только для разрешённых чатов, очередь ограничена 500 сообщениями на чат.</Text>}
    <Group>
      <Button size="sm" loading={action.isPending} onClick={() => action.mutate({ path: name, method: 'PATCH', body: { enabled, ...(token ? { bot_token: token } : {}), ...(!collector ? { owner_id: owner || null, lead_minutes: minutes } : {}) } })}>Сохранить бота</Button>
      <Button size="sm" variant="default" disabled={action.isPending} onClick={() => action.mutate({ path: `bots/${name}/check` })}>Проверить бота</Button>
      <Button size="sm" variant="subtle" color="red" disabled={action.isPending} onClick={() => action.mutate({ path: name, method: 'PATCH', body: { enabled: false, bot_token: null } })}>Удалить токен</Button>
    </Group>
    {collector && <>
      <Button size="sm" variant="subtle" loading={available.isFetching} onClick={() => available.refetch()}>Загрузить группы бота</Button>
      <MultiSelect label="Разрешённые чаты бота" searchable value={chats} onChange={setChats} data={(available.data ?? (data.chat_ids ?? []).map((id) => ({ id, title: String(id) }))).map((chat) => ({ value: String(chat.id), label: chat.title }))} />
      <Group><Button size="sm" variant="light" disabled={!available.data || action.isPending} onClick={() => action.mutate({ path: 'bots/collector/chats', body: { chat_ids: chats.map(Number) } })}>Сохранить чаты</Button></Group>
    </>}
    {(action.error || available.error) && <Alert color="red">{(action.error || available.error).message}</Alert>}
    {notice && <Text size="sm" c="purple">{notice}</Text>}
    {data.last_error && <Alert color="yellow">{data.last_error}</Alert>}
    <Text size="xs" c="dimmed">Последний успешный запрос: {data.last_success ? new Date(data.last_success).toLocaleString() : 'ещё не было'}</Text>
  </Stack>;
}
