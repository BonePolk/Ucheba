import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { MultiCombobox } from '@/components/ui/select';
import { Notice, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { CollapseSection } from '@/lib/motion';

export default function BotSettings({ name, data }) {
  const client = useQueryClient();
  const [token, setToken] = useState('');
  const [enabled, setEnabled] = useState(data.enabled);
  const [owner, setOwner] = useState(data.owner_id ?? '');
  const [minutes, setMinutes] = useState(data.lead_minutes ?? 30);
  const [chats, setChats] = useState((data.chat_ids ?? []).map(String));
  const [notice, setNotice] = useState('');
  const [hintOpen, setHintOpen] = useState(false);
  const collector = name === 'collector';

  const available = useQuery({
    queryKey: ['integrations', 'collector-chats'],
    queryFn: () => request('/integrations/bots/collector/chats'),
    enabled: false,
    retry: false,
  });

  const action = useMutation({
    mutationFn: ({ path, body, method = 'POST' }) => request(`/integrations/${path}`, {
      method,
      body: JSON.stringify(body),
    }),
    onSuccess: async (result, variables) => {
      if (variables.method === 'PATCH') {
        if ('bot_token' in variables.body) setToken('');
        if ('enabled' in variables.body) setEnabled(variables.body.enabled);
      }
      setNotice(
        result.username
          ? `Подключён @${result.username}`
          : result.messages !== undefined
            ? `Сообщений: ${result.messages}`
            : 'Сохранено',
      );
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['inbox'] });
      await client.invalidateQueries({ queryKey: ['tasks'] });
    },
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222222] pb-2.5">
        <SectionHeader title={collector ? 'Бот-сборщик' : 'Бот уведомлений'} />
        <div className="flex items-center gap-2">
          <StatusIndicator tone={enabled ? 'success' : 'idle'} label={enabled ? 'Включён' : 'Отключён'} />
          <Switch label="Включить" checked={enabled} onCheckedChange={setEnabled} />
        </div>
      </div>

      <Field label="Токен BotFather">
        <PasswordField
          value={token}
          placeholder={data.configured.bot_token ? 'Сохранён' : ''}
          onChange={(event) => setToken(event.currentTarget.value)}
        />
      </Field>

      {!collector && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <Field label="Telegram ID">
            <Input
              type="number"
              min={1}
              value={owner}
              onChange={(event) => setOwner(event.currentTarget.value === '' ? '' : Number(event.currentTarget.value))}
            />
          </Field>
          <Field label="Напоминать за (мин)">
            <Input
              type="number"
              min={1}
              max={1440}
              value={minutes}
              onChange={(event) => setMinutes(Number(event.currentTarget.value) || 30)}
            />
          </Field>
        </div>
      )}

      <div>
        <button
          type="button"
          onClick={() => setHintOpen(!hintOpen)}
          aria-expanded={hintOpen}
          className="inline-flex items-center gap-1 text-[11px] text-[#929292] hover:text-[#EAEAEA] cursor-pointer"
        >
          {hintOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <span>Подсказка по настройке</span>
        </button>
        <CollapseSection open={hintOpen}>
          <p className="text-[11px] text-[#929292] m-0 mt-1 border-l border-[#303030] pl-2">
            {collector
              ? 'Добавьте бота в группы и отключите Privacy Mode у BotFather; после сообщения в группе загрузите чаты.'
              : 'Отправьте боту команду /start.'}
          </p>
        </CollapseSection>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          loading={action.isPending}
          onClick={() => action.mutate({
            path: name,
            method: 'PATCH',
            body: {
              enabled,
              ...(token ? { bot_token: token } : {}),
              ...(!collector ? { owner_id: owner || null, lead_minutes: minutes } : {}),
            },
          })}
        >
          Сохранить
        </Button>
        <Button variant="default" disabled={action.isPending} onClick={() => action.mutate({ path: `bots/${name}/check` })}>
          Проверить
        </Button>
        <Button
          variant="danger-ghost"
          disabled={action.isPending}
          onClick={() => action.mutate({ path: name, method: 'PATCH', body: { enabled: false, bot_token: null } })}
        >
          Удалить токен
        </Button>
      </div>

      {collector && (
        <div className="border-t border-[#222222] pt-3 flex flex-col gap-2">
          <div>
            <Button size="sm" variant="default" loading={available.isFetching} onClick={() => available.refetch()}>
              Загрузить группы
            </Button>
          </div>
          <Field label="Разрешённые чаты">
            <MultiCombobox
              value={chats}
              onChange={setChats}
              options={(available.data ?? (data.chat_ids ?? []).map((id) => ({ id, title: String(id) }))).map((chat) => ({
                value: String(chat.id),
                label: chat.title,
              }))}
            />
          </Field>
          <div>
            <Button
              size="sm"
              variant="accent-subtle"
              disabled={!available.data || action.isPending}
              onClick={() => action.mutate({ path: 'bots/collector/chats', body: { chat_ids: chats.map(Number) } })}
            >
              Сохранить чаты
            </Button>
          </div>
        </div>
      )}

      {(action.error || available.error) && <Notice tone="error">{(action.error || available.error).message}</Notice>}
      {notice && <Notice tone="accent" onClose={() => setNotice('')}>{notice}</Notice>}
      {data.last_error && <Notice tone="warning">{data.last_error}</Notice>}
      {data.last_success && (
        <div className="text-[11px] text-[#929292]">
          Успешно: {new Date(data.last_success).toLocaleString()}
        </div>
      )}
    </div>
  );
}
