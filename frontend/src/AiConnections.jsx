import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Badge, Notice, SectionHeader } from '@/components/ui/surface';

export default function AiConnections({ data, oauthState }) {
  const client = useQueryClient();
  const [endpoint, setEndpoint] = useState(data?.base_url || 'https://lenec.tech/v1');
  const [apiKey, setApiKey] = useState('');

  const keyConfigured = Boolean(data?.configured?.api_key);
  const oauthAuthorized = Boolean(oauthState?.data?.authorized);
  const device = oauthState?.data?.device;

  const oauthConnect = useMutation({
    mutationFn: () => request('/integrations/openai-oauth/connect', { method: 'POST' }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations', 'openai-oauth'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'arrodes'] }),
      ]);
    },
  });

  const oauthDisconnect = useMutation({
    mutationFn: () => request('/integrations/openai-oauth/disconnect', { method: 'POST' }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations', 'openai-oauth'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'arrodes'] }),
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
      ]);
    },
  });

  const saveProvider = useMutation({
    mutationFn: (remove = false) => request('/integrations/ai_provider', {
      method: 'PATCH',
      body: JSON.stringify(
        remove
          ? { api_key: null }
          : {
              base_url: endpoint.trim() || 'https://lenec.tech/v1',
              ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
            },
      ),
    }),
    onSuccess: async () => {
      setApiKey('');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
        client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
      ]);
    },
  });

  const checkProvider = useMutation({
    mutationFn: () => request('/integrations/ai_provider/check', {
      method: 'POST',
      body: JSON.stringify({
        base_url: endpoint.trim() || data?.base_url || 'https://lenec.tech/v1',
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      }),
    }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
  });

  const oauthBusy = oauthConnect.isPending || oauthDisconnect.isPending;
  const providerBusy = saveProvider.isPending || checkProvider.isPending;
  const oauthError = oauthConnect.error || oauthDisconnect.error;
  const providerError = saveProvider.error || checkProvider.error;

  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-[#222222] pb-2.5">
        <SectionHeader title="Подключения AI" />
      </div>

      {/* OpenAI OAuth */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] font-medium text-[#EAEAEA]">OpenAI OAuth</span>
            <span className="text-[11px] text-[#929292]">
              Общая авторизация ChatGPT / OpenAI для агентов в режиме OAuth
            </span>
          </div>
          <Badge variant={oauthAuthorized ? 'success' : 'outline'}>
            {oauthAuthorized ? 'Вход выполнен' : 'Нет авторизации'}
          </Badge>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="default"
            loading={oauthConnect.isPending}
            disabled={oauthBusy}
            onClick={() => oauthConnect.mutate()}
          >
            Подключить OpenAI OAuth
          </Button>
          <Button
            size="sm"
            variant="danger-ghost"
            loading={oauthDisconnect.isPending}
            disabled={oauthBusy || !oauthAuthorized}
            onClick={() => {
              if (window.confirm('Отключить OpenAI OAuth для всех агентов?')) oauthDisconnect.mutate();
            }}
          >
            Отключить OAuth
          </Button>
        </div>

        {device?.state === 'waiting' && (
          <Notice tone="accent" title="Подтвердите вход в браузере">
            <div className="flex flex-col gap-1.5 mt-1">
              {device.url && (
                <a href={device.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[#CFA9F5] underline w-fit">
                  OpenAI <ExternalLink className="h-3 w-3" />
                </a>
              )}
              <div className="font-mono text-[15px] font-bold tracking-wider text-[#EAEAEA]">
                {device.code ?? 'Ожидание кода…'}
              </div>
            </div>
          </Notice>
        )}

        {['failed', 'expired'].includes(device?.state) && (
          <Notice tone="warning">Код истёк или вход не завершён. Запросите новый.</Notice>
        )}
        {oauthError && <Notice tone="error">{oauthError.message}</Notice>}
      </section>

      {/* Shared API Key */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] font-medium text-[#EAEAEA]">API-ключ (OpenAI-совместимый шлюз)</span>
            <span className="text-[11px] text-[#929292]">
              Общий endpoint и ключ для всех агентов в режиме API-ключа
            </span>
          </div>
          <Badge variant={keyConfigured ? 'success' : 'outline'}>
            {keyConfigured ? 'Сохранён' : 'Не задан'}
          </Badge>
        </div>

        <Field label="Адрес API">
          <Input
            placeholder="https://provider.example/v1"
            value={endpoint}
            maxLength={2000}
            onChange={(event) => setEndpoint(event.currentTarget.value)}
          />
        </Field>

        <Field label="API-ключ">
          <PasswordField
            placeholder={keyConfigured ? 'Сохранён' : 'API-ключ'}
            value={apiKey}
            autoComplete="new-password"
            onChange={(event) => setApiKey(event.currentTarget.value)}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <Button
            variant="primary"
            loading={saveProvider.isPending}
            disabled={providerBusy || !endpoint.trim()}
            onClick={() => saveProvider.mutate(false)}
          >
            Сохранить
          </Button>
          <Button
            variant="default"
            loading={checkProvider.isPending}
            disabled={providerBusy || !endpoint.trim() || (!keyConfigured && !apiKey.trim())}
            onClick={() => checkProvider.mutate()}
          >
            Проверить шлюз
          </Button>
          {keyConfigured && (
            <Button
              variant="danger-ghost"
              disabled={providerBusy}
              onClick={() => saveProvider.mutate(true)}
            >
              Удалить ключ
            </Button>
          )}
        </div>

        {providerError && <Notice tone="error">{providerError.message}</Notice>}
        {saveProvider.isSuccess && <Notice tone="success">Настройки шлюза сохранены.</Notice>}
        {checkProvider.isSuccess && (
          <Notice tone="success">
            Шлюз доступен, моделей: {checkProvider.data?.models_count ?? 0}.
          </Notice>
        )}
        {data?.last_error && <Notice tone="warning" title="Ошибка">{data.last_error}</Notice>}
        {data?.last_success && (
          <div className="text-[11px] text-[#929292]">
            Успешно: {new Date(data.last_success).toLocaleString()}
          </div>
        )}
      </section>
    </div>
  );
}
