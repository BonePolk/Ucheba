import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { request } from './api';
import AgentSettings from './AgentSettings';
import BotSettings from './BotSettings';
import ModelPicker from './ModelPicker';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, PasswordField } from '@/components/ui/input';
import { MultiCombobox } from '@/components/ui/select';
import { Badge, Notice, PageLoader, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { cn } from '@/lib/utils';
import { LAYOUT_TRANSITION, motion, StageSwap } from '@/lib/motion';

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
  const [telegram, setTelegram] = useState({ enabled: data.telegram.enabled, api_id: '', api_hash: '', phone: '' });
  const [arrodes, setArrodes] = useState({ enabled: data.arrodes.enabled, auto_prepare: data.arrodes.auto_prepare, model: data.arrodes.model ?? '' });
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

  const arrodesState = useQuery({
    queryKey: ['integrations', 'arrodes'],
    enabled: features.arrodes !== false,
    queryFn: () => request('/integrations/arrodes/status'),
    refetchInterval: 10000,
    retry: false,
  });

  const items = [
    ...(features.ameba !== false && data.ameba ? [{ id: 'ameba', label: 'Ameba', enabled: data.ameba.enabled, error: Boolean(data.ameba.last_error) }] : []),
    ...(features.monocle !== false ? [{ id: 'monocle', label: 'Monocle', enabled: data.monocle.enabled, error: Boolean(data.monocle.last_error) }] : []),
    ...(features.straus !== false && data.straus ? [{ id: 'straus', label: 'Straus', enabled: data.straus.enabled, error: Boolean(data.straus.last_error) }] : []),
    ...(features.arrodes !== false ? [{ id: 'arrodes', label: 'Arrodes', enabled: data.arrodes.enabled, error: Boolean(data.arrodes.last_error) }] : []),
    ...(features.telegram !== false ? [{ id: 'telegram', label: 'Telegram', enabled: data.telegram_connected, error: Boolean(data.telegram.last_error) }] : []),
    ...(features.collector !== false ? [{ id: 'collector', label: 'Сборщик', enabled: data.collector.enabled, error: Boolean(data.collector.last_error) }] : []),
    ...(features.notifications !== false ? [{ id: 'notifications', label: 'Уведомления', enabled: data.notifications.enabled, error: Boolean(data.notifications.last_error) }] : []),
  ];

  const [selectedId, setSelectedId] = useState(() => items[0]?.id ?? 'ameba');
  const activeId = items.some((i) => i.id === selectedId) ? selectedId : items[0]?.id;

  const mutation = useMutation({
    mutationFn: ({ path, body, method = 'POST' }) => request(`/integrations/${path}`, {
      method,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    }),
    onSuccess: async (result, variables) => {
      if (variables.method === 'PATCH') {
        const setter = { telegram: setTelegram, arrodes: setArrodes }[variables.path];
        if (setter) {
          setter((current) => Object.fromEntries(Object.entries(current).map(([key, value]) => [
            key, ['api_id', 'api_hash', 'phone', 'api_key', 'api_token'].includes(key) ? '' : variables.body[key] ?? value,
          ])));
        }
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
      if (variables.path.startsWith('arrodes/')) client.invalidateQueries({ queryKey: ['integrations', 'arrodes'] });
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
  const error = mutation.error || chats.error || arrodesState.error;

  if (!items.length) {
    return <Notice tone="info">Все интеграции и агенты отключены администратором.</Notice>;
  }

  return (
    <div className="flex flex-col gap-3">
      {error && <Notice tone="error">{error.message}</Notice>}
      {notice && <Notice tone="accent" onClose={() => setNotice('')}>{notice}</Notice>}

      <div className="grid grid-cols-1 md:grid-cols-[190px_minmax(0,1fr)] border border-[#303030] bg-[#111111]">
        {/* Left Agents / Integrations Selector */}
        <aside className="border-b md:border-b-0 md:border-r border-[#242424] bg-[#0D0D0D] p-2 flex md:flex-col gap-1 overflow-x-auto">
          {items.map((item) => {
            const active = item.id === activeId;
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={cn(
                  'relative flex items-center justify-between gap-2 px-2.5 py-2 text-left shrink-0 md:shrink transition-colors cursor-pointer',
                  active ? 'text-[#EAEAEA]' : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#161616]',
                )}
              >
                {active && (
                  <motion.span
                    layoutId="integrations-active"
                    transition={LAYOUT_TRANSITION}
                    className="absolute inset-0 bg-[#7F49B4]/16 border-l-2 border-l-[#7F49B4] pointer-events-none"
                  />
                )}
                <span className="relative z-10 text-[13px] font-medium truncate">{item.label}</span>
                <StatusIndicator className="relative z-10" tone={item.error ? 'warning' : item.enabled ? 'success' : 'idle'} />
              </button>
            );
          })}
        </aside>

        {/* Right Detail Configuration Pane */}
        <div className="p-4 min-w-0">
          <StageSwap stageKey={activeId}>
            {activeId === 'ameba' && data.ameba && (
              <AgentSettings key="ameba" name="ameba" title="Ameba" data={data.ameba} />
            )}

            {activeId === 'monocle' && (
              <AgentSettings key="monocle" name="monocle" title="Monocle" data={data.monocle} />
            )}

            {activeId === 'straus' && data.straus && (
              <AgentSettings key="straus" name="straus" title="Straus" data={data.straus} />
            )}

            {activeId === 'arrodes' && (
              <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222222] pb-2.5">
                  <SectionHeader title="Arrodes" />
                  <Badge variant={arrodesState.data?.authorized ? 'success' : 'outline'}>
                    {arrodesState.data?.authorized ? 'Вход выполнен' : 'Нет авторизации'}
                  </Badge>
                </div>

                {arrodesState.data && !arrodesState.data.sandbox_available && (
                  <Notice tone="warning">
                    Sandbox недоступен: {arrodesState.data.sandbox_error || 'выполнение заблокировано'}.
                  </Notice>
                )}

                <div className="flex flex-wrap gap-2">
                  <Button variant="primary" disabled={mutation.isPending} onClick={() => run('arrodes/connect')}>
                    Подключить ChatGPT
                  </Button>
                  <Button variant="default" disabled={mutation.isPending} onClick={() => run('arrodes/check')}>
                    Проверить
                  </Button>
                </div>

                {arrodesState.data?.device?.state === 'waiting' && (
                  <Notice tone="accent" title="Подтвердите вход в браузере">
                    <div className="flex flex-col gap-1.5 mt-1">
                      {arrodesState.data.device.url && (
                        <a href={arrodesState.data.device.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[#CFA9F5] underline w-fit">
                          OpenAI <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                      <div className="font-mono text-[15px] font-bold tracking-wider text-[#EAEAEA]">
                        {arrodesState.data.device.code ?? 'Ожидание кода…'}
                      </div>
                    </div>
                  </Notice>
                )}

                {['failed', 'expired'].includes(arrodesState.data?.device?.state) && (
                  <Notice tone="warning">Код истёк. Запросите новый.</Notice>
                )}

                <ModelPicker
                  path="/integrations/arrodes/models"
                  chatgpt
                  value={arrodes.model}
                  onChange={(model) => setArrodes((current) => ({ ...current, model }))}
                />

                <Switch
                  label="Включить"
                  checked={arrodes.enabled}
                  onCheckedChange={(checked) => setArrodes((current) => ({ ...current, enabled: checked }))}
                />
                <Switch
                  label="Автоподготовка задач из Telegram"
                  checked={arrodes.auto_prepare}
                  onCheckedChange={(checked) => setArrodes((current) => ({ ...current, auto_prepare: checked }))}
                />

                <div className="flex flex-wrap gap-2 pt-1">
                  <Button variant="primary" loading={saving('arrodes')} disabled={mutation.isPending} onClick={() => save('arrodes', arrodes)}>
                    Сохранить
                  </Button>
                  <Button variant="danger-ghost" disabled={mutation.isPending} onClick={() => run('arrodes/disconnect')}>
                    Отключить
                  </Button>
                </div>

                <ConnectionInfo value={data.arrodes} />
              </div>
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
                    <Field label="Код Telegram" className="flex-1 min-w-[180px]">
                      <PasswordField value={code} onChange={(event) => setCode(event.currentTarget.value)} />
                    </Field>
                    <Button variant="primary" disabled={mutation.isPending || !code} onClick={() => run('telegram/login', { code })}>
                      Подтвердить
                    </Button>
                  </div>
                )}

                {step === 'password' && (
                  <div className="flex flex-wrap items-end gap-2 border border-[#303030] bg-[#0D0D0D] p-2.5">
                    <Field label="Пароль 2FA" className="flex-1 min-w-[180px]">
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
