import { useState } from 'react';
import { Alert, Anchor, Badge, Button, Group, Loader, MultiSelect, NumberInput, Paper, PasswordInput, Select, SimpleGrid, Stack, Switch, Text, Textarea, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import BotSettings from './BotSettings';

function ConnectionInfo({ value }) {
  return <>
    {value.last_error && <Alert color="red">{value.last_error}</Alert>}
    <Text size="sm" c="dimmed">Последний успешный запрос: {value.last_success ? new Date(value.last_success).toLocaleString() : 'ещё не было'}</Text>
  </>;
}

export default function Integrations({ features = {} }) {
  const query = useQuery({ queryKey: ['integrations'], queryFn: () => request('/integrations'), refetchInterval: 15000, retry: false });
  if (query.isPending) return <Loader />;
  if (query.error) return <Alert color="red">{query.error.message}</Alert>;
  return <IntegrationForms data={query.data} features={features} />;
}

function IntegrationForms({ data, features }) {
  const client = useQueryClient();
  const [telegram, setTelegram] = useState({ enabled: data.telegram.enabled, api_id: '', api_hash: '', phone: '' });
  const [gemini, setGemini] = useState({ enabled: data.gemini.enabled, model: data.gemini.model, interval_seconds: data.gemini.interval_seconds, confidence_threshold: data.gemini.confidence_threshold, timezone: data.gemini.timezone, owner_context: data.gemini.owner_context, api_key: '' });
  const [codex, setCodex] = useState({ enabled: data.codex.enabled, auto_prepare: data.codex.auto_prepare });
  const [context, setContext] = useState({ enabled: data.context.enabled, api_key: '' });
  const [jev, setJev] = useState({ enabled: data.jev.enabled });
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState('');
  const [chatIds, setChatIds] = useState(data.telegram.chat_ids.map(String));
  const [notice, setNotice] = useState('');
  const chats = useQuery({ queryKey: ['integrations', 'chats'], queryFn: () => request('/integrations/telegram/chats'), enabled: false, retry: false });
  const models = useQuery({ queryKey: ['integrations', 'models'], queryFn: () => request('/integrations/gemini/models'), enabled: false, retry: false });
  const [codexOpened, setCodexOpened] = useState(data.codex.enabled);
  const codexState = useQuery({ queryKey: ['integrations', 'codex'], enabled: features.codex !== false && codexOpened, queryFn: () => request('/integrations/codex/status'), refetchInterval: 10000, retry: false });
  const mutation = useMutation({
    mutationFn: ({ path, body, method = 'POST' }) => request(`/integrations/${path}`, { method, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) }),
    onSuccess: async (result) => {
      if (result.step) { setStep(result.step); setCode(''); setPassword(''); }
      setNotice(result.messages !== undefined ? `Обработано сообщений: ${result.messages}, создано задач: ${result.created}, на подтверждение: ${result.pending}` : 'Готово');
      await client.invalidateQueries({ queryKey: ['integrations'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['inbox'] });
    },
  });
  const run = (path, body, method) => mutation.mutate({ path, body, method });
  const save = (name, values, setter) => mutation.mutate({ path: name, body: { ...values, ...(features[name] === false ? { enabled: false } : {}) }, method: 'PATCH' }, { onSuccess: () => setter((current) => Object.fromEntries(Object.entries(current).map(([key, value]) => [key, ['api_id', 'api_hash', 'phone', 'api_key', 'api_token'].includes(key) ? '' : value]))) });
  const field = (setter, name) => (event) => setter((current) => ({ ...current, [name]: event.currentTarget.value }));
  const toggle = (setter, name) => (event) => setter((current) => ({ ...current, [name]: event.currentTarget.checked }));
  const error = mutation.error || chats.error || models.error || codexState.error;
  return <Stack>
    <Text size="sm" c="dimmed">Пустые поля ключей сохраняют прежнее значение. Сначала сохраните настройки, затем проверяйте подключение. Проверка использует квоту провайдера.</Text>
    {error && <Alert color="red">{error.message}</Alert>}
    {notice && <Alert color="purple" withCloseButton onClose={() => setNotice('')}>{notice}</Alert>}
    <SimpleGrid cols={{ base: 1, md: 2 }}>
      {features.context !== false && <Paper withBorder p="lg"><Stack>
        <Title order={3}>Контекст · GPT-6 Luna</Title>
        <Text size="sm">Личный ключ шлюза lenec.tech. Память контекста и ответы доступны только в твоём аккаунте.</Text>
        <PasswordInput label="API-ключ" placeholder={data.context.configured.api_key ? 'Сохранён' : ''} value={context.api_key} onChange={field(setContext, 'api_key')} />
        <Switch label="Проверять контекст перед Jev" checked={context.enabled} onChange={toggle(setContext, 'enabled')} />
        <Group><Button loading={mutation.isPending} onClick={() => save('context', context, setContext)}>Сохранить</Button><Button variant="subtle" color="red" disabled={mutation.isPending} onClick={() => run('context', { api_key: null, enabled: false }, 'PATCH')}>Удалить ключ</Button></Group>
        <ConnectionInfo value={data.context} />
      </Stack></Paper>}

      {features.telegram !== false && <Paper withBorder p="lg"><Stack>
        <Group justify="space-between"><Title order={3}>Telegram</Title><Badge color={data.telegram_connected ? 'green' : 'gray'}>{data.telegram_connected ? 'Подключён' : 'Не подключён'}</Badge></Group>
        <PasswordInput label="API ID" placeholder={data.telegram.configured.api_id ? 'Сохранён' : ''} value={telegram.api_id} onChange={field(setTelegram, 'api_id')} />
        <PasswordInput label="API Hash" placeholder={data.telegram.configured.api_hash ? 'Сохранён' : ''} value={telegram.api_hash} onChange={field(setTelegram, 'api_hash')} />
        <PasswordInput label="Телефон" placeholder={data.telegram.configured.phone ? 'Сохранён' : '+7…'} value={telegram.phone} onChange={field(setTelegram, 'phone')} />
        <Switch label="Автоматический анализ выбранных чатов" checked={telegram.enabled} onChange={toggle(setTelegram, 'enabled')} />
        <Group><Button loading={mutation.isPending} onClick={() => save('telegram', telegram, setTelegram)}>Сохранить</Button><Button variant="default" disabled={mutation.isPending || data.telegram_connected} onClick={() => run('telegram/code')}>Подключить аккаунт</Button></Group>
        {step === 'code' && <Group align="end"><PasswordInput label="Код Telegram" value={code} onChange={(event) => setCode(event.currentTarget.value)} /><Button disabled={mutation.isPending || !code} onClick={() => run('telegram/login', { code })}>Подтвердить</Button></Group>}
        {step === 'password' && <Group align="end"><PasswordInput label="Пароль двухфакторной авторизации" value={password} onChange={(event) => setPassword(event.currentTarget.value)} /><Button disabled={mutation.isPending || !password} onClick={() => run('telegram/login', { password })}>Войти</Button></Group>}
        <Button variant="default" loading={chats.isFetching} onClick={() => chats.refetch()}>Загрузить чаты</Button>
        <MultiSelect label="Разрешённые чаты" description="До 30 чатов. Новые сообщения отслеживаются с момента выбора." searchable value={chatIds} onChange={setChatIds} data={(chats.data ?? data.telegram.chat_ids.map((id) => ({ id, title: String(id) }))).map((chat) => ({ value: String(chat.id), label: chat.title }))} />
        <Group><Button variant="light" disabled={mutation.isPending || !chats.data} onClick={() => run('telegram/chats', { chat_ids: chatIds.map(Number) })}>Сохранить выбор</Button><Button variant="light" loading={mutation.isPending || data.analyzing} onClick={() => run('telegram/analyze')}>Проанализировать последние сообщения</Button></Group>
        <Text size="xs" c="dimmed">Ручной анализ: до 50 последних сообщений за 7 дней в каждом выбранном чате. Повторно обработанные сообщения пропускаются.</Text>
        <Button color="red" variant="subtle" disabled={mutation.isPending} onClick={() => { if (window.confirm('Отключить Telegram?')) run('telegram/disconnect'); }}>Отключить аккаунт</Button>
        <ConnectionInfo value={data.telegram} />
      </Stack></Paper>}
      {features.gemini !== false && <Paper withBorder p="lg"><Stack>
        <Title order={3}>Gemini</Title>
        <PasswordInput label="API-ключ" placeholder={data.gemini.configured.api_key ? 'Сохранён' : ''} value={gemini.api_key} onChange={field(setGemini, 'api_key')} />
        <Select label="Модель" searchable data={[...new Set([gemini.model, ...(models.data ?? [])])]} value={gemini.model} allowDeselect={false} onChange={(model) => setGemini({ ...gemini, model })} />
        <Button variant="subtle" loading={models.isFetching} onClick={() => models.refetch()}>Загрузить доступные модели</Button>
        <TextInput label="Часовой пояс" description="Например, Europe/Moscow" value={gemini.timezone} onChange={field(setGemini, 'timezone')} />
        <Textarea label="Контекст владельца" description="Имя, группа, какие поручения относятся к вам" value={gemini.owner_context} onChange={field(setGemini, 'owner_context')} />
        <NumberInput label="Интервал обработки, секунд" min={30} max={86400} value={gemini.interval_seconds} onChange={(value) => setGemini({ ...gemini, interval_seconds: value })} />
        <NumberInput label="Порог уверенности автосоздания" min={0.5} max={1} step={0.05} decimalScale={2} value={gemini.confidence_threshold} onChange={(value) => setGemini({ ...gemini, confidence_threshold: value })} />
        <Switch label="Разрешить анализ сообщений" checked={gemini.enabled} onChange={toggle(setGemini, 'enabled')} />
        <Text size="xs" c="dimmed">Изменения, отмена, неясный адресат и неоднозначные задачи всегда требуют подтверждения.</Text>
        <Group><Button loading={mutation.isPending} onClick={() => save('gemini', gemini, setGemini)}>Сохранить</Button><Button variant="default" disabled={mutation.isPending} onClick={() => run('gemini/check')}>Проверить подключение</Button><Button variant="subtle" color="red" disabled={mutation.isPending} onClick={() => run('gemini', { api_key: null, enabled: false }, 'PATCH')}>Удалить ключ</Button></Group>
        <ConnectionInfo value={data.gemini} />
      </Stack></Paper>}
      {features.codex !== false && <Paper withBorder p="lg"><Stack>
        <Group justify="space-between"><Title order={3}>Codex</Title><Badge color={codexState.data?.authorized ? 'green' : 'gray'}>{codexState.data?.authorized ? 'Вход выполнен' : 'Нет авторизации'}</Badge></Group>
        <Text size="sm">Официальный Python SDK openai-codex с входом ChatGPT по device code. Включите device code login в настройках безопасности ChatGPT. Доступ зависит от подписки и лимитов аккаунта.</Text>
        {codexState.data && !codexState.data.sandbox_available && <Alert color="yellow">Linux sandbox недоступен в этом окружении. Выполнение заблокировано; режим без защиты не используется.</Alert>}
        <Group><Button disabled={mutation.isPending} onClick={() => { setCodexOpened(true); run('codex/connect'); }}>Подключить через ChatGPT</Button><Button variant="default" disabled={mutation.isPending} onClick={() => { setCodexOpened(true); run('codex/check'); }}>Проверить доступность</Button></Group>
        {codexState.data?.device?.state === 'waiting' && <Alert color="purple"><Stack gap="xs"><Text>Подтвердите вход в браузере:</Text>{codexState.data.device.url && <Anchor href={codexState.data.device.url} target="_blank" rel="noreferrer">Открыть страницу OpenAI</Anchor>}<Text fw={700}>{codexState.data.device.code ?? 'Ожидание кода…'}</Text></Stack></Alert>}
        {['failed', 'expired'].includes(codexState.data?.device?.state) && <Alert color="yellow">Вход не завершён или код истёк. Запросите новый код.</Alert>}
        <Switch label="Включить агента" checked={codex.enabled} onChange={toggle(setCodex, 'enabled')} />
        <Switch label="Автоматически готовить материалы для принятых задач из Telegram" checked={codex.auto_prepare} onChange={toggle(setCodex, 'auto_prepare')} />
        <Text size="xs" c="dimmed">Одна задача за раз, до 5 минут. Подготовка файлов и встроенный поиск; сетевой доступ команд отключён. Результат переводит задачу «На проверку». Публикация и отправка сообщений не выполняются.</Text>
        <Group><Button loading={mutation.isPending} onClick={() => save('codex', codex, setCodex)}>Сохранить</Button><Button color="red" variant="subtle" disabled={mutation.isPending} onClick={() => run('codex/disconnect')}>Отключить</Button></Group>
        <ConnectionInfo value={data.codex} />
      </Stack></Paper>}
      {features.jev !== false && <Paper withBorder p="lg"><Stack>
        <Title order={3}>Приоритеты · Laya Multilingual</Title>
        <Text size="sm">Локальная альтернатива Jev: 322M параметров, CPU, поддержка русского. Ключи и оплата Cloudflare не нужны. При первой проверке скачивается модель; первый запрос может занять несколько минут. Ориентир памяти сервиса — 2–3 ГБ, фактический расход зависит от текста.</Text>
        <Switch label="Автоматически обновлять порядок задач" checked={jev.enabled} onChange={toggle(setJev, 'enabled')} />
        <Text size="xs" c="dimmed">Сначала Luna проверяет контекст. Затем Jev сравнивает задачи попарно и расставляет по важности, без баллов из 100. При неуверенном сравнении сохраняются ручной приоритет и дедлайн. Просроченные задачи остаются выше остальных. Задачи без готового контекста ожидают уточнений.</Text>
        <Group><Button loading={mutation.isPending} onClick={() => save('jev', jev, setJev)}>Сохранить</Button><Button variant="default" disabled={mutation.isPending} onClick={() => run('jev/check')}>Проверить модель</Button></Group>
        <ConnectionInfo value={data.jev} />
      </Stack></Paper>}
      {features.collector !== false && <Paper withBorder p="lg"><BotSettings name="collector" data={data.collector} /></Paper>}
      {features.notifications !== false && <Paper withBorder p="lg"><BotSettings name="notifications" data={data.notifications} /></Paper>}
    </SimpleGrid>
  </Stack>;
}
