import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import AgentSettings from './AgentSettings';
import AiConnections from './AiConnections';
import BotSettings from './BotSettings';
import { Button } from '@/components/ui/button';
import { Field, PasswordField } from '@/components/ui/input';
import { MultiCombobox } from '@/components/ui/select';
import { Badge, Notice, PageLoader, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { normalizeAntigravityState } from '@/lib/antigravity';
import { cn } from '@/lib/utils';
import { motion, SPRING_SNAPPY, StageSwap, useReducedMotion } from '@/lib/motion';

function ConnectionInfo({ value }) {
  if (!value.last_error && !value.last_success) return null;
  return (
    <div className="flex flex-col gap-1.5 text-[11px] text-[#929292]">
      {value.last_error && <Notice tone="error">{value.last_error}</Notice>}
      {value.last_success && (
        <div>Успешно: {new Date(value.last_success).toLocaleString()}</div>
      )}
    </div>
  );
}

export default function Integrations({ features = {} }) {
  const query = useQuery({
    queryKey: ['integrations'],
    queryFn: () => request('/integrations'),
    refetchInterval: 15000,
    retry: false,
  });

  if (query.isPending) return <PageLoader label="Загрузка настроек агентов…" />;
  if (query.error && !query.data) return <Notice tone="error">{query.error.message}</Notice>;

  return (
    <div className="flex flex-col gap-3">
      {query.error && <Notice tone="error">{query.error.message}</Notice>}
      <IntegrationForms data={query.data} features={features} />
    </div>
  );
}

function IntegrationForms({ data, features }) {
  const client = useQueryClient();
  const reduced = useReducedMotion();
  const [telegram, setTelegram] = useState({ enabled: data.telegram.enabled, api_id: '', api_hash: '', phone: '' });
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [step, setStep] = useState('');
  const [chatIds, setChatIds] = useState(data.telegram.chat_ids.map(String));
  const [notice, setNotice] = useState('');

  const chats = useQuery({
    queryKey: ['integrations', 'chats'],
    queryFn: () => request('/integrations/telegram/chats'),
    enabled: false,
    retry: false,
  });

  const anyAgentEnabled = (
    features.ameba !== false
    || features.monocle !== false
    || features.straus !== false
    || features.arrodes !== false
  );
  const oauthState = useQuery({
    queryKey: ['integrations', 'openai-oauth'],
    enabled: anyAgentEnabled,
    queryFn: () => request('/integrations/openai-oauth/status'),
    refetchInterval: (query) => (
      ['waiting', 'starting'].includes(query.state.data?.device?.state) ? 3000 : 10000
    ),
    retry: false,
  });

  const antigravityShouldPoll = Boolean(
    data.antigravity?.system_enabled === true
    && data.antigravity?.system_configured === true
    && !data.antigravity?.oauth_client_mismatch
    && (
      data.antigravity?.configured?.access_token
      || data.antigravity?.configured?.refresh_token
      || data.antigravity?.account_email
    ),
  );
  const antigravityState = useQuery({
    queryKey: [
      'integrations',
      'antigravity',
      Boolean(data.antigravity?.system_enabled === true),
      Boolean(data.antigravity?.system_configured === true),
      data.antigravity?.config_version ?? null,
      data.antigravity?.connection_id || '',
      Boolean(data.antigravity?.configured?.access_token),
      Boolean(data.antigravity?.configured?.refresh_token),
      data.antigravity?.account_email || '',
    ],
    enabled: anyAgentEnabled,
    queryFn: () => request(`/integrations/antigravity/status${antigravityShouldPoll ? '?live=true' : ''}`),
    refetchInterval: antigravityShouldPoll ? 60000 : false,
    retry: false,
  });

  const agState = normalizeAntigravityState({ data: data.antigravity, stateQuery: antigravityState });
  const aiReady = Boolean(
    oauthState.data?.authorized
    || data.ai_provider?.configured?.api_key
    || agState.canRunAgent,
  );
  const aiError = Boolean(
    data.ai_provider?.last_error
    || agState.primaryError,
  );

  const items = [
    ...(anyAgentEnabled ? [{ id: 'ai_provider', label: 'Подключения AI', enabled: aiReady, error: aiError }] : []),
    ...(features.ameba !== false && data.ameba ? [{ id: 'ameba', label: 'Ameba', enabled: data.ameba.enabled, error: Boolean(data.ameba.last_error) }] : []),
    ...(features.monocle !== false ? [{ id: 'monocle', label: 'Monocle', enabled: data.monocle.enabled, error: Boolean(data.monocle.last_error) }] : []),
    ...(features.straus !== false && data.straus ? [{ id: 'straus', label: 'Straus', enabled: data.straus.enabled, error: Boolean(data.straus.last_error) }] : []),
    ...(features.arrodes !== false ? [{ id: 'arrodes', label: 'Arrodes', enabled: data.arrodes.enabled, error: Boolean(data.arrodes.last_error) }] : []),
    ...(features.telegram !== false ? [{ id: 'telegram', label: 'Telegram', enabled: data.telegram_connected, error: Boolean(data.telegram.last_error) }] : []),
    ...(features.collector !== false ? [{ id: 'collector', label: 'Сборщик', enabled: data.collector.enabled, error: Boolean(data.collector.last_error) }] : []),
    ...(features.notifications !== false ? [{ id: 'notifications', label: 'Уведомления', enabled: data.notifications.enabled, error: Boolean(data.notifications.last_error) }] : []),
  ];

  const [selectedId, setSelectedId] = useState(() => items[0]?.id ?? 'ai_provider');
  const activeId = items.some((i) => i.id === selectedId) ? selectedId : items[0]?.id;

  const mutation = useMutation({
    mutationFn: ({ path, body, method = 'POST' }) => request(`/integrations/${path}`, {
      method,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }),
    onSuccess: async (result, variables) => {
      if (variables.method === 'PATCH' && variables.path === 'telegram') {
        setTelegram((current) => Object.fromEntries(Object.entries(current).map(([key, value]) => [
          key, ['api_id', 'api_hash', 'phone', 'api_key', 'api_token'].includes(key) ? '' : variables.body[key] ?? value,
        ])));
      }
      if (result.step) {
        setStep(result.step);
        setCode('');
        setPassword('');
      }
      setNotice(
        result.messages !== undefined
          ? `Обработано сообщений: ${result.messages}, создано задач: ${result.created}, на подтверждение: ${result.pending}`
          : 'Готово',
      );
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['tasks'] });
      await client.invalidateQueries({ queryKey: ['inbox'] });
    },
  });

  const run = (path, body, method) => mutation.mutate({ path, body, method });
  const save = (name, values) => mutation.mutate({
    path: name,
    body: { ...values, ...(features[name] === false ? { enabled: false } : {}) },
    method: 'PATCH',
  });
  const saving = (name) => mutation.isPending && mutation.variables?.path === name;
  const field = (setter, name) => (event) => {
    const value = event.currentTarget.value;
    setter((current) => ({ ...current, [name]: value }));
  };
  const error = mutation.error || chats.error || oauthState.error;

  if (!items.length) {
    return <Notice tone="info">Все интеграции и агенты отключены администратором.</Notice>;
  }

  return (
    <div className="flex flex-col gap-3">
      {error && <Notice tone="error">{error.message}</Notice>}
      {notice && <Notice tone="accent" onClose={() => setNotice('')}>{notice}</Notice>}

      <div className="grid grid-cols-1 md:grid-cols-[190px_minmax(0,1fr)] border border-[#303030] bg-[#111111]">
        {/* Left Agents / Integrations Selector */}
        <aside className="border-b md:border-b-0 md:border-r border-[#242424] bg-[#0D0D0D] p-2 flex md:flex-col gap-1 overflow-x-auto no-scrollbar">
          {items.map((item) => {
            const active = item.id === activeId;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={cn(
                  'relative flex items-center justify-between gap-2 px-2.5 py-2 max-md:py-2.5 max-md:px-3 text-left shrink-0 md:shrink transition-colors cursor-pointer',
                  active ? 'text-[#EAEAEA]' : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#161616]',
                )}
              >
                {active && (
                  <motion.span
                    layoutId="integrations-active"
                    transition={reduced ? { duration: 0.01 } : SPRING_SNAPPY}
                    className="absolute inset-0 bg-[#7F49B4]/16 border-l-2 border-l-[#7F49B4] max-md:border-l-0 max-md:border-b-2 max-md:border-b-[#7F49B4] pointer-events-none"
                  />
                )}
                <span className="relative z-10 text-[13px] font-medium truncate">{item.label}</span>
                <StatusIndicator className="relative z-10" tone={item.error ? 'warning' : item.enabled ? 'success' : 'idle'} />
              </button>
            );
          })}
        </aside>

        {/* Right Detail Configuration Pane */}
        <div className="p-4 max-md:p-3 min-w-0">
          <StageSwap stageKey={activeId}>
            {activeId === 'ai_provider' && (
              <AiConnections
                key="ai_provider"
                data={data.ai_provider}
                oauthState={oauthState}
                antigravityData={data.antigravity}
                antigravityState={antigravityState}
              />
            )}

            {activeId === 'ameba' && data.ameba && (
              <AgentSettings
                key="ameba"
                name="ameba"
                title="Ameba"
                data={data.ameba}
                provider={data.ai_provider}
                oauthState={oauthState}
                antigravityData={data.antigravity}
                antigravityState={antigravityState}
                onOpenProvider={() => setSelectedId('ai_provider')}
              />
            )}

            {activeId === 'monocle' && (
              <AgentSettings
                key="monocle"
                name="monocle"
                title="Monocle"
                data={data.monocle}
                provider={data.ai_provider}
                oauthState={oauthState}
                antigravityData={data.antigravity}
                antigravityState={antigravityState}
                onOpenProvider={() => setSelectedId('ai_provider')}
              />
            )}

            {activeId === 'straus' && data.straus && (
              <AgentSettings
                key="straus"
                name="straus"
                title="Straus"
                data={data.straus}
                provider={data.ai_provider}
                oauthState={oauthState}
                antigravityData={data.antigravity}
                antigravityState={antigravityState}
                onOpenProvider={() => setSelectedId('ai_provider')}
              />
            )}

            {activeId === 'arrodes' && (
              <AgentSettings
                key="arrodes"
                name="arrodes"
                title="Arrodes"
                data={data.arrodes}
                provider={data.ai_provider}
                oauthState={oauthState}
                antigravityData={data.antigravity}
                antigravityState={antigravityState}
                onOpenProvider={() => setSelectedId('ai_provider')}
              />
            )}

            {activeId === 'telegram' && (
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222222] pb-2.5">
                  <SectionHeader title="Telegram · Telethon" />
                  <Badge variant={data.telegram_connected ? 'success' : 'outline'}>
                    {data.telegram_connected ? 'Подключён' : 'Не подключён'}
                  </Badge>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                  <Field label="API ID">
                    <PasswordField placeholder={data.telegram.configured.api_id ? 'Сохранён' : ''} value={telegram.api_id} onChange={field(setTelegram, 'api_id')} />
                  </Field>
                  <Field label="API Hash">
                    <PasswordField placeholder={data.telegram.configured.api_hash ? 'Сохранён' : ''} value={telegram.api_hash} onChange={field(setTelegram, 'api_hash')} />
                  </Field>
                  <Field label="Телефон">
                    <PasswordField placeholder={data.telegram.configured.phone ? 'Сохранён' : '+7…'} value={telegram.phone} onChange={field(setTelegram, 'phone')} />
                  </Field>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" loading={saving('telegram')} disabled={mutation.isPending} onClick={() => save('telegram', telegram)}>
                    Сохранить
                  </Button>
                  <Button variant="default" disabled={mutation.isPending || data.telegram_connected} onClick={() => run('telegram/code')}>
                    Подключить
                  </Button>
                </div>

                {step === 'code' && (
                  <div className="flex flex-wrap items-end gap-2 border border-[#303030] bg-[#0D0D0D] p-2.5">
                    <Field label="Код Telegram" className="w-full min-w-0 sm:flex-1 sm:min-w-[180px]">
                      <PasswordField value={code} onChange={(event) => setCode(event.currentTarget.value)} />
                    </Field>
                    <Button variant="primary" disabled={mutation.isPending || !code} onClick={() => run('telegram/login', { code })}>
                      Подтвердить
                    </Button>
                  </div>
                )}

                {step === 'password' && (
                  <div className="flex flex-wrap items-end gap-2 border border-[#303030] bg-[#0D0D0D] p-2.5">
                    <Field label="Пароль 2FA" className="w-full min-w-0 sm:flex-1 sm:min-w-[180px]">
                      <PasswordField value={password} onChange={(event) => setPassword(event.currentTarget.value)} />
                    </Field>
                    <Button variant="primary" disabled={mutation.isPending || !password} onClick={() => run('telegram/login', { password })}>
                      Войти
                    </Button>
                  </div>
                )}

                <div className="border-t border-[#222222] pt-3 flex flex-col gap-2">
                  <div>
                    <Button size="sm" variant="default" loading={chats.isFetching} onClick={() => chats.refetch()}>
                      Загрузить чаты
                    </Button>
                  </div>
                  <Field label="Разрешённые чаты">
                    <MultiCombobox
                      value={chatIds}
                      onChange={setChatIds}
                      options={(chats.data ?? data.telegram.chat_ids.map((id) => ({ id, title: String(id) }))).map((chat) => ({
                        value: String(chat.id),
                        label: chat.title,
                      }))}
                    />
                  </Field>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="accent-subtle"
                      disabled={mutation.isPending || !chats.data}
                      onClick={() => run('telegram/chats', { chat_ids: chatIds.map(Number) })}
                    >
                      Сохранить выбор
                    </Button>
                    <Button
                      size="sm"
                      variant="danger-ghost"
                      disabled={mutation.isPending}
                      onClick={() => { if (window.confirm('Отключить Telegram?')) run('telegram/disconnect'); }}
                    >
                      Отключить
                    </Button>
                  </div>
                </div>

                <ConnectionInfo value={data.telegram} />
              </div>
            )}

            {activeId === 'collector' && <BotSettings key="collector" name="collector" data={data.collector} />}
            {activeId === 'notifications' && <BotSettings key="notifications" name="notifications" data={data.notifications} />}
          </StageSwap>
        </div>
      </div>
    </div>
  );
}
