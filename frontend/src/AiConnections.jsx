import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Badge, Notice, SectionHeader } from '@/components/ui/surface';

export default function AiConnections({ data, oauthState, antigravityData, antigravityState }) {
  const client = useQueryClient();
  const [endpoint, setEndpoint] = useState(data?.base_url || 'https://lenec.tech/v1');
  const [apiKey, setApiKey] = useState('');
  const [gwEndpoint, setGwEndpoint] = useState(antigravityData?.base_url || '');
  const [gwToken, setGwToken] = useState('');

  useEffect(() => {
    setGwEndpoint(antigravityData?.base_url || '');
  }, [antigravityData?.base_url]);

  const keyConfigured = Boolean(data?.configured?.api_key);
  const oauthAuthorized = Boolean(oauthState?.data?.authorized);
  const device = oauthState?.data?.device;
  const gwConfigured = Boolean(antigravityData?.base_url?.trim());
  const gwTokenConfigured = Boolean(antigravityData?.configured?.gateway_token);
  const gwStatusData = antigravityState?.data;
  const gwReachable = Boolean(gwConfigured && gwStatusData?.reachable);
  const gwChecking = Boolean(gwConfigured && antigravityState?.isFetching && !gwStatusData);

  const oauthConnect = useMutation({
    mutationFn: () => request('/integrations/openai-oauth/connect', { method: 'POST' }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations', 'openai-oauth'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'arrodes'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
      ]);
    },
  });

  const oauthDisconnect = useMutation({
    mutationFn: () => request('/integrations/openai-oauth/disconnect', { method: 'POST' }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations', 'openai-oauth'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'arrodes'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
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
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
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
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
      ]);
    },
  });

  const saveGateway = useMutation({
    mutationFn: (mode = 'save') => {
      if (mode === 'disconnect') {
        return request('/integrations/antigravity_gateway', {
          method: 'PATCH',
          body: JSON.stringify({ base_url: '', gateway_token: null }),
        });
      }
      if (mode === 'remove_token') {
        return request('/integrations/antigravity_gateway', {
          method: 'PATCH',
          body: JSON.stringify({ gateway_token: null }),
        });
      }
      return request('/integrations/antigravity_gateway', {
        method: 'PATCH',
        body: JSON.stringify({
          base_url: gwEndpoint.trim(),
          ...(gwToken.trim() ? { gateway_token: gwToken.trim() } : {}),
        }),
      });
    },
    onSuccess: async (_, mode) => {
      setGwToken('');
      if (mode === 'disconnect') setGwEndpoint('');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
        client.invalidateQueries({ queryKey: ['integrations', 'antigravity-gateway'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
        client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
      ]);
    },
  });

  const checkGateway = useMutation({
    mutationFn: () => request('/integrations/antigravity_gateway/check', {
      method: 'POST',
      body: JSON.stringify({
        base_url: gwEndpoint.trim() || antigravityData?.base_url || '',
        ...(gwToken.trim() ? { gateway_token: gwToken.trim() } : {}),
      }),
    }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
        client.invalidateQueries({ queryKey: ['integrations', 'antigravity-gateway'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
      ]);
    },
  });

  const oauthBusy = oauthConnect.isPending || oauthDisconnect.isPending;
  const providerBusy = saveProvider.isPending || checkProvider.isPending;
  const gwBusy = saveGateway.isPending || checkGateway.isPending;
  const oauthError = oauthConnect.error || oauthDisconnect.error;
  const providerError = saveProvider.error || checkProvider.error;
  const gwError = saveGateway.error || checkGateway.error;
  const gwLiveError = gwConfigured && !gwReachable ? (gwStatusData?.error || antigravityState?.error?.message) : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-[#222222] pb-2.5">
        <SectionHeader title="Подключения AI" />
      </div>

      {/* OpenAI OAuth */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] font-medium text-[#EAEAEA]">OpenAI OAuth</span>
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
          <span className="text-[13px] font-medium text-[#EAEAEA]">API-ключ</span>
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

      {/* Antigravity Responses API Gateway */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] font-medium text-[#EAEAEA]">Antigravity Gateway</span>
          <Badge variant={!gwConfigured || gwChecking ? 'outline' : gwReachable ? 'success' : 'warning'}>
            {!gwConfigured
              ? 'Не настроен'
              : gwChecking
                ? 'Проверка…'
                : gwReachable
                  ? 'Доступен'
                  : 'Недоступен'}
          </Badge>
        </div>

        <div className="text-[11px] text-[#929292]">
          Подключение к внешнему Responses API шлюзу (например, codex-antigravity-auth). Авторизация Google-аккаунта выполняется на стороне самого шлюза.
        </div>

        <Field label="Адрес шлюза">
          <Input
            placeholder="http://host.docker.internal:51122/v1"
            value={gwEndpoint}
            maxLength={2000}
            onChange={(event) => setGwEndpoint(event.currentTarget.value)}
          />
        </Field>

        <Field label="Токен доступа (опционально)">
          <PasswordField
            placeholder={gwTokenConfigured ? 'Сохранён' : 'ANTIGRAVITY_GATEWAY_TOKEN (если требуется)'}
            value={gwToken}
            autoComplete="new-password"
            onChange={(event) => setGwToken(event.currentTarget.value)}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <Button
            variant="primary"
            loading={saveGateway.isPending && saveGateway.variables === 'save'}
            disabled={gwBusy || !gwEndpoint.trim()}
            onClick={() => saveGateway.mutate('save')}
          >
            Сохранить
          </Button>
          <Button
            variant="default"
            loading={checkGateway.isPending}
            disabled={gwBusy || (!gwEndpoint.trim() && !gwConfigured)}
            onClick={() => checkGateway.mutate()}
          >
            Проверить шлюз
          </Button>
          {gwTokenConfigured && (
            <Button
              variant="danger-ghost"
              loading={saveGateway.isPending && saveGateway.variables === 'remove_token'}
              disabled={gwBusy}
              onClick={() => saveGateway.mutate('remove_token')}
            >
              Удалить токен
            </Button>
          )}
          {gwConfigured && (
            <Button
              variant="danger-ghost"
              loading={saveGateway.isPending && saveGateway.variables === 'disconnect'}
              disabled={gwBusy}
              onClick={() => {
                if (window.confirm('Отключить Antigravity Gateway?')) saveGateway.mutate('disconnect');
              }}
            >
              Отключить
            </Button>
          )}
        </div>

        {gwError && <Notice tone="error">{gwError.message}</Notice>}
        {saveGateway.isSuccess && (
          <Notice tone="success">
            {saveGateway.variables === 'disconnect'
              ? 'Настройки Antigravity Gateway сброшены.'
              : 'Настройки Antigravity Gateway сохранены.'}
          </Notice>
        )}
        {checkGateway.isSuccess && (
          <Notice tone="success">
            Шлюз доступен, моделей: {checkGateway.data?.models_count ?? 0}
            {checkGateway.data?.health_status ? ` · health: ${checkGateway.data.health_status}` : ''}.
          </Notice>
        )}
        {!checkGateway.isSuccess && gwReachable && (
          <div className="text-[11px] text-[#929292]">
            Моделей: {gwStatusData?.models_count ?? 0}
            {gwStatusData?.health_status ? ` · health: ${gwStatusData.health_status}` : ''}
          </div>
        )}
        {gwLiveError && !gwError && (
          <Notice tone="warning" title="Статус шлюза">{gwLiveError}</Notice>
        )}
        {antigravityData?.last_error && antigravityData.last_error !== gwLiveError && (
          <Notice tone="warning" title="Ошибка">{antigravityData.last_error}</Notice>
        )}
        {antigravityData?.last_success && (
          <div className="text-[11px] text-[#929292]">
            Успешно: {new Date(antigravityData.last_success).toLocaleString()}
          </div>
        )}
      </section>
    </div>
  );
}
