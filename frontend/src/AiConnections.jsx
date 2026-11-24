import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink, RefreshCw } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Badge, Notice, SectionHeader } from '@/components/ui/surface';
import { formatSafeDateTime, normalizeAntigravityState } from '@/lib/antigravity';

function formatLimit(val) {
  return typeof val === 'number' && val > 0 ? val.toLocaleString('ru-RU') : null;
}

export default function AiConnections({ data, oauthState, antigravityData, antigravityState }) {
  const client = useQueryClient();
  const [endpoint, setEndpoint] = useState(data?.base_url || 'https://lenec.tech/v1');
  const [apiKey, setApiKey] = useState('');

  // Пользовательская OAuth-сессия Google Antigravity (без системных OAuth-настроек)
  const [agPending, setAgPending] = useState(null);
  const [agCallbackInput, setAgCallbackInput] = useState('');
  const [agTestModel, setAgTestModel] = useState('');

  const keyConfigured = Boolean(data?.configured?.api_key);
  const oauthAuthorized = Boolean(oauthState?.data?.authorized);
  const device = oauthState?.data?.device;

  const ag = normalizeAntigravityState({ data: antigravityData, stateQuery: antigravityState });
  const agModels = ag.models;
  const agExpiresText = formatSafeDateTime(ag.tokenExpiresAt);
  const agVerifiedText = formatSafeDateTime(ag.verifiedAt);
  const agCheckedTimeText = formatSafeDateTime(ag.checkedAt, { timeOnly: true });
  const agLastSuccessText = formatSafeDateTime(ag.lastSuccess);

  useEffect(() => {
    setAgTestModel('');
  }, [ag.connectionId]);

  useEffect(() => {
    if (!ag.oauthAuthorized) {
      setAgTestModel('');
      return;
    }
    const modelExists = agModels.some((m) => m.id === agTestModel);
    if ((!agTestModel || (agModels.length > 0 && !modelExists)) && agModels.length > 0) {
      const firstAvail = agModels.find((m) => m.available !== false) || agModels[0];
      if (firstAvail?.id) setAgTestModel(firstAvail.id);
    }
  }, [ag.oauthAuthorized, agModels, agTestModel]);

  const invalidateAllAi = () => Promise.all([
    client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
    client.invalidateQueries({ queryKey: ['integrations', 'antigravity'] }),
    client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities'] }),
    client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
  ]);

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

  const agStartLogin = useMutation({
    mutationFn: () => request('/integrations/antigravity/login/start', { method: 'POST' }),
    onSuccess: (res) => {
      setAgPending(res);
      setAgCallbackInput('');
      if (res?.auth_url) {
        window.open(res.auth_url, '_blank', 'noopener,noreferrer');
      }
    },
  });

  const agCompleteLogin = useMutation({
    mutationFn: () => request('/integrations/antigravity/login/complete', {
      method: 'POST',
      body: JSON.stringify({
        callback_url: agCallbackInput.trim(),
        ...(agPending?.state ? { state: agPending.state } : {}),
      }),
    }),
    onSuccess: async (res) => {
      setAgPending(null);
      setAgCallbackInput('');
      setAgTestModel('');
      agTestGeneration.reset();
      client.setQueriesData({ queryKey: ['integrations', 'antigravity'] }, res);
      await invalidateAllAi();
    },
  });

  const agRefresh = useMutation({
    mutationFn: () => request('/integrations/antigravity/refresh', { method: 'POST' }),
    onSuccess: async (res) => {
      agTestGeneration.reset();
      client.setQueriesData({ queryKey: ['integrations', 'antigravity'] }, res);
      await invalidateAllAi();
    },
  });

  const agTestGeneration = useMutation({
    mutationFn: () => request('/integrations/antigravity/test-generation', {
      method: 'POST',
      body: JSON.stringify({ model: agTestModel.trim() }),
    }),
    onSuccess: async (res) => {
      if (res?.status) {
        client.setQueriesData({ queryKey: ['integrations', 'antigravity'] }, res.status);
      }
      await invalidateAllAi();
    },
  });

  const agToggleInternal = useMutation({
    mutationFn: (include_internal) => request('/integrations/antigravity/preferences', {
      method: 'PATCH',
      body: JSON.stringify({ include_internal }),
    }),
    onSuccess: async () => {
      await invalidateAllAi();
    },
  });

  const agLogout = useMutation({
    mutationFn: () => request('/integrations/antigravity/logout', { method: 'POST' }),
    onSuccess: async () => {
      setAgPending(null);
      setAgCallbackInput('');
      setAgTestModel('');
      agTestGeneration.reset();
      await invalidateAllAi();
    },
  });

  const oauthBusy = oauthConnect.isPending || oauthDisconnect.isPending;
  const providerBusy = saveProvider.isPending || checkProvider.isPending;
  const agBusy = (
    agStartLogin.isPending
    || agCompleteLogin.isPending
    || agRefresh.isPending
    || agTestGeneration.isPending
    || agToggleInternal.isPending
    || agLogout.isPending
  );
  const oauthError = oauthConnect.error || oauthDisconnect.error;
  const providerError = saveProvider.error || checkProvider.error;
  const agActionError = (
    agStartLogin.error
    || agCompleteLogin.error
    || agRefresh.error
    || agTestGeneration.error
    || agToggleInternal.error
    || agLogout.error
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-[#222222] pb-2.5">
        <SectionHeader title="Подключения AI" />
      </div>

      {/* Google Antigravity (Пользовательская OAuth-сессия) */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] font-medium text-[#EAEAEA]">Google Antigravity</span>
          <Badge variant={ag.badgeVariant}>{ag.badgeText}</Badge>
        </div>

        <div className="text-[11px] text-[#929292]">
          Подключение вашего Google-аккаунта к Cloud Code API (настраивается администратором Ucheba).
        </div>

        {ag.hasSnapshot && !ag.systemEnabled && (
          <Notice tone="warning">Google Antigravity отключён администратором.</Notice>
        )}
        {ag.hasSnapshot && ag.systemEnabled && !ag.systemConfigured && (
          <Notice tone="warning">
            Google Antigravity пока не настроен администратором (укажите OAuth Client ID и Client Secret в админ-панели).
          </Notice>
        )}
        {ag.oauthClientMismatch && (
          <Notice tone="warning">
            Администратор изменил OAuth-клиент Google Antigravity. Выполните повторный вход через Google для продолжения работы.
          </Notice>
        )}

        {(ag.oauthAuthorized || ag.oauthClientMismatch) && (
          <div className="border border-[#242424] bg-[#111111] px-2.5 py-2 text-[12px] flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-[#929292]">Аккаунт:</span>
              <span className="text-[#EAEAEA] font-medium break-all">
                {ag.accountEmail || (ag.accountName ? ag.accountName : 'Google-аккаунт (email не указан)')}
              </span>
              {ag.accountName && ag.accountEmail && <span className="text-[#929292]">({ag.accountName})</span>}
            </div>
            {ag.projectId && (
              <div className="flex flex-wrap items-center gap-x-2 text-[11px] text-[#929292]">
                <span>Проект:</span>
                <span className="font-mono text-[#DCDCDC] break-all">{ag.projectId}</span>
                {ag.tier && <Badge variant="outline">{ag.tier}</Badge>}
              </div>
            )}
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              <Badge variant={ag.oauthClientMismatch || ag.tokenExpired ? 'warning' : 'success'}>
                {ag.oauthClientMismatch
                  ? 'OAuth: требуется повторный вход'
                  : ag.tokenExpired
                    ? 'OAuth: требуется обновление'
                    : 'OAuth: авторизован'}
              </Badge>
              <Badge variant={ag.catalogAccessible ? 'success' : 'warning'}>
                {ag.catalogAccessible
                  ? 'Каталог Cloud Code: доступен'
                  : ag.configStale
                    ? 'Каталог: требуется перепроверка'
                    : 'Каталог Cloud Code: не подтверждён'}
              </Badge>
              <Badge variant={ag.generationVerified ? 'success' : 'outline'}>
                {ag.generationVerified
                  ? `Генерация: подтверждена (${ag.verifiedModel || 'OK'})`
                  : 'Генерация: не проверялась'}
              </Badge>
            </div>
            {agExpiresText && (
              <div className="text-[11px] text-[#929292]">
                Токен действует до: {agExpiresText}
                {ag.hasRefreshToken ? ' · автообновление активно' : ' · без refresh_token'}
              </div>
            )}
            {agVerifiedText && (
              <div className="text-[11px] text-[#929292]">
                Тест генерации пройден: {agVerifiedText}
              </div>
            )}
            {ag.refreshWarning && (
              <div className="text-[11px] text-[#E5B567]">{ag.refreshWarning}</div>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="primary"
            loading={agStartLogin.isPending}
            disabled={agBusy || ag.isLoading || !ag.systemEnabled || !ag.systemConfigured}
            onClick={() => agStartLogin.mutate()}
          >
            {(ag.oauthAuthorized || ag.oauthClientMismatch) ? 'Переподключить Google' : 'Войти через Google'}
          </Button>
          {ag.oauthAuthorized && (
            <Button
              size="sm"
              variant="default"
              leftIcon={<RefreshCw className={`h-3 w-3 ${agRefresh.isPending ? 'animate-spin' : ''}`} />}
              loading={agRefresh.isPending}
              disabled={agBusy || !ag.systemEnabled || !ag.systemConfigured}
              onClick={() => agRefresh.mutate()}
            >
              Обновить токен и каталог
            </Button>
          )}
          {(ag.oauthAuthorized || ag.oauthClientMismatch) && (
            <Button
              size="sm"
              variant="danger-ghost"
              loading={agLogout.isPending}
              disabled={agBusy}
              onClick={() => {
                if (window.confirm('Выйти из Google Antigravity и удалить сохранённые токены этого аккаунта Ucheba?')) {
                  agLogout.mutate();
                }
              }}
            >
              Выйти
            </Button>
          )}
        </div>

        {ag.oauthAuthorized && ag.systemEnabled && ag.systemConfigured && (
          <div className="border border-[#242424] bg-[#111111] p-2.5 flex flex-col gap-2">
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Модель для проверки реальной генерации" className="w-full sm:flex-1 sm:min-w-[220px]">
                <Input
                  placeholder="Например, gemini-2.5-flash"
                  value={agTestModel}
                  maxLength={200}
                  onChange={(event) => setAgTestModel(event.currentTarget.value)}
                />
              </Field>
              <Button
                size="sm"
                variant="default"
                leftIcon={<CheckCircle2 className="h-3.5 w-3.5" />}
                loading={agTestGeneration.isPending}
                disabled={agBusy || !agTestModel.trim()}
                onClick={() => agTestGeneration.mutate()}
              >
                Проверить генерацию
              </Button>
            </div>
            <Switch
              label="Показывать внутренние / экспериментальные модели без displayName"
              checked={ag.includeInternal}
              disabled={agBusy}
              onCheckedChange={(checked) => agToggleInternal.mutate(checked)}
            />
            {agTestGeneration.data?.ok && !agTestGeneration.isPending && (
              <Notice tone="success">
                Генерация подтверждена ({agTestGeneration.data.model}): {agTestGeneration.data.sample_text}
              </Notice>
            )}
          </div>
        )}

        {agPending && (
          <Notice tone="accent" title="Завершите вход через Google">
            <div className="flex flex-col gap-2 mt-1">
              <div className="text-[11px] text-[#DCDCDC]">
                Откройте страницу авторизации Google, подтвердите доступ и вставьте <strong>полный URL перенаправления</strong> из адресной строки браузера или <strong>код авторизации</strong> (вида <span className="font-mono">4/0...</span>).
              </div>
              <a
                href={agPending.auth_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[#CFA9F5] underline w-fit"
              >
                Открыть страницу Google OAuth <ExternalLink className="h-3 w-3" />
              </a>
              <Field label="Полный URL перенаправления или код авторизации">
                <Input
                  placeholder="https://antigravity.google/oauth-callback?state=...&code=... или 4/0..."
                  value={agCallbackInput}
                  onChange={(event) => setAgCallbackInput(event.currentTarget.value)}
                />
              </Field>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  loading={agCompleteLogin.isPending}
                  disabled={agBusy || !agCallbackInput.trim()}
                  onClick={() => agCompleteLogin.mutate()}
                >
                  Завершить вход
                </Button>
                <Button
                  size="sm"
                  variant="subtle"
                  disabled={agBusy}
                  onClick={() => {
                    setAgPending(null);
                    setAgCallbackInput('');
                  }}
                >
                  Отмена
                </Button>
              </div>
            </div>
          </Notice>
        )}

        {agActionError && <Notice tone="error">{agActionError.message}</Notice>}
        {ag.oauthError && !agActionError && !ag.oauthClientMismatch && (
          <Notice tone="error" title="Ошибка OAuth Google">{ag.oauthError}</Notice>
        )}
        {ag.transportError && !agActionError && (
          <Notice tone="warning" title="Ошибка соединения с сервером">{ag.transportError}</Notice>
        )}
        {ag.quotaError && !agActionError && (
          <Notice tone="warning" title="Проблема доступа или квоты Cloud Code">{ag.quotaError}</Notice>
        )}
        {ag.serverError && !agActionError && (
          <Notice tone="warning" title="Ошибка Google Antigravity">{ag.serverError}</Notice>
        )}

        {ag.oauthAuthorized && agModels.length > 0 && (
          <div className="border border-[#242424] bg-[#111111] p-2.5 flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2 text-[11px] text-[#929292]">
              <span>Доступные модели аккаунта ({agModels.length})</span>
              {agCheckedTimeText && (
                <span>Обновлено: {agCheckedTimeText}</span>
              )}
            </div>
            <div className="max-h-56 overflow-y-auto divide-y divide-[#1E1E1E]">
              {agModels.map((m) => {
                const pct = m.remaining_percent;
                const inLim = formatLimit(m.max_input_tokens || m.context_window);
                const outLim = formatLimit(m.max_output_tokens);
                const resetText = formatSafeDateTime(m.reset_time);
                return (
                  <div key={m.id} className="py-1.5 flex flex-wrap items-center justify-between gap-2 text-[11px]">
                    <div className="min-w-0 flex flex-col">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[#EAEAEA] font-medium truncate">{m.name || m.id}</span>
                        <span className="font-mono text-[#787878] truncate">{m.id}</span>
                        {m.available === false && <Badge variant="warning">недоступна</Badge>}
                      </div>
                      {(inLim || outLim || resetText) && (
                        <div className="text-[#787878] flex flex-wrap gap-x-2">
                          {inLim && <span>вход: {inLim}</span>}
                          {outLim && <span>выход: {outLim}</span>}
                          {resetText && <span>сброс: {resetText}</span>}
                        </div>
                      )}
                    </div>
                    <div className="shrink-0 flex items-center gap-2">
                      {pct != null ? (
                        <Badge variant={pct > 25 ? 'success' : pct > 0 ? 'warning' : 'danger'}>
                          {pct}% квоты
                        </Badge>
                      ) : (
                        <span className="text-[#787878]">Квота неизвестна</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {agLastSuccessText && (
          <div className="text-[11px] text-[#929292]">
            Последняя проверка: {agLastSuccessText}
          </div>
        )}
      </section>

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
          <Button
            variant="danger-ghost"
            loading={saveProvider.isPending}
            disabled={providerBusy || !keyConfigured}
            onClick={() => {
              if (window.confirm('Удалить сохранённый API-ключ?')) saveProvider.mutate(true);
            }}
          >
            Удалить ключ
          </Button>
        </div>

        {providerError && <Notice tone="error">{providerError.message}</Notice>}
        {saveProvider.isSuccess && <Notice tone="success">Сохранено.</Notice>}
        {checkProvider.isSuccess && (
          <Notice tone="success">Соединение работает. Доступных моделей: {checkProvider.data.models_count}.</Notice>
        )}
        {data?.last_success && (
          <div className="text-[11px] text-[#929292]">
            Последняя проверка: {new Date(data.last_success).toLocaleString()}
          </div>
        )}
      </section>
    </div>
  );
}
