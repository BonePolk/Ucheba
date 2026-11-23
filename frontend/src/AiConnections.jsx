import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, ExternalLink, RefreshCw } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Badge, Notice, SectionHeader } from '@/components/ui/surface';

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

  const agStatus = antigravityState?.data;
  const agSystemEnabled = (agStatus?.system_enabled ?? antigravityData?.system_enabled) !== false;
  const agSystemConfigured = (agStatus?.system_configured ?? antigravityData?.system_configured) !== false;
  const agAuthorized = Boolean(
    agStatus?.oauth_authorized
    ?? agStatus?.authorized
    ?? (antigravityData?.configured?.access_token || antigravityData?.configured?.refresh_token),
  );
  const agEmail = agStatus?.account_email || antigravityData?.account_email || '';
  const agName = agStatus?.account_name || antigravityData?.account_name || '';
  const agEffectiveProject = agStatus?.project_id || antigravityData?.discovered_project_id || '';
  const agTier = agStatus?.subscription_tier || antigravityData?.subscription_tier || '';
  const agModels = Array.isArray(agStatus?.models)
    ? agStatus.models
    : (Array.isArray(antigravityData?.cached_models) ? antigravityData.cached_models : []);
  const agTokenExpired = Boolean(agStatus?.token_expired);
  const agCatalogAccessible = Boolean(agStatus?.catalog_accessible ?? antigravityData?.catalog_accessible);
  const agGenerationVerified = Boolean(agStatus?.generation_verified ?? antigravityData?.generation_verified);
  const agVerifiedModel = agStatus?.generation_verified_model || antigravityData?.generation_verified_model || '';
  const agVerifiedAt = agStatus?.generation_verified_at || antigravityData?.generation_verified_at || null;
  const agIncludeInternal = Boolean(agStatus?.include_internal ?? antigravityData?.include_internal);

  useEffect(() => {
    if (!agTestModel && agModels.length > 0) {
      const firstAvail = agModels.find((m) => m.available !== false) || agModels[0];
      if (firstAvail?.id) setAgTestModel(firstAvail.id);
    }
  }, [agModels, agTestModel]);

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
      client.setQueriesData({ queryKey: ['integrations', 'antigravity'] }, res);
      await invalidateAllAi();
    },
  });

  const agRefresh = useMutation({
    mutationFn: () => request('/integrations/antigravity/refresh', { method: 'POST' }),
    onSuccess: async (res) => {
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
  const agActionError = agStartLogin.error || agCompleteLogin.error || agRefresh.error || agTestGeneration.error || agLogout.error;
  const agOAuthError = agStatus?.oauth_error || null;
  const agQuotaError = agStatus?.quota_error || null;
  const agServerError = !agOAuthError && !agQuotaError ? (agStatus?.error || antigravityData?.last_error) : null;

  const agBadgeVariant = !agSystemEnabled || !agSystemConfigured
    ? 'warning'
    : !agAuthorized
      ? 'outline'
      : agOAuthError || agTokenExpired
        ? 'warning'
        : agQuotaError || !agCatalogAccessible
          ? 'warning'
          : agGenerationVerified
            ? 'success'
            : 'outline';
  const agBadgeText = !agSystemEnabled
    ? 'Отключён админом'
    : !agSystemConfigured
      ? 'Не настроен админом'
      : !agAuthorized
        ? 'Не подключён'
        : agTokenExpired
          ? 'Токен истёк'
          : agOAuthError
            ? 'Ошибка OAuth'
            : !agCatalogAccessible
              ? 'Нет доступа к каталогу'
              : agGenerationVerified
                ? 'Подтверждён'
                : 'Каталог доступен';

  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-[#222222] pb-2.5">
        <SectionHeader title="Подключения AI" />
      </div>

      {/* Google Antigravity (Пользовательская OAuth-сессия) */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] font-medium text-[#EAEAEA]">Google Antigravity</span>
          <Badge variant={agBadgeVariant}>{agBadgeText}</Badge>
        </div>

        <div className="text-[11px] text-[#929292]">
          Подключение вашего Google-аккаунта к Cloud Code API (настраивается администратором Ucheba).
        </div>

        {!agSystemEnabled && (
          <Notice tone="warning">Google Antigravity отключён администратором.</Notice>
        )}
        {agSystemEnabled && !agSystemConfigured && (
          <Notice tone="warning">
            Google Antigravity пока не настроен администратором (укажите OAuth Client ID и Client Secret в админ-панели).
          </Notice>
        )}

        {agAuthorized && (
          <div className="border border-[#242424] bg-[#111111] px-2.5 py-2 text-[12px] flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-[#929292]">Аккаунт:</span>
              <span className="text-[#EAEAEA] font-medium break-all">
                {agEmail || (agName ? agName : 'Google-аккаунт (email не указан)')}
              </span>
              {agName && agEmail && <span className="text-[#929292]">({agName})</span>}
            </div>
            {agEffectiveProject && (
              <div className="flex flex-wrap items-center gap-x-2 text-[11px] text-[#929292]">
                <span>Проект:</span>
                <span className="font-mono text-[#DCDCDC] break-all">{agEffectiveProject}</span>
                {agTier && <Badge variant="outline">{agTier}</Badge>}
              </div>
            )}
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              <Badge variant={agTokenExpired ? 'warning' : 'success'}>
                {agTokenExpired ? 'OAuth: требуется обновление' : 'OAuth: авторизован'}
              </Badge>
              <Badge variant={agCatalogAccessible ? 'success' : 'warning'}>
                {agCatalogAccessible ? 'Каталог Cloud Code: доступен' : 'Каталог Cloud Code: не подтверждён'}
              </Badge>
              <Badge variant={agGenerationVerified ? 'success' : 'outline'}>
                {agGenerationVerified
                  ? `Генерация: подтверждена (${agVerifiedModel || 'OK'})`
                  : 'Генерация: не проверялась'}
              </Badge>
            </div>
            {agStatus?.token_expires_at && (
              <div className="text-[11px] text-[#929292]">
                Токен действует до: {new Date(agStatus.token_expires_at).toLocaleString()}
                {agStatus.has_refresh_token ? ' · автообновление активно' : ' · без refresh_token'}
              </div>
            )}
            {agVerifiedAt && (
              <div className="text-[11px] text-[#929292]">
                Тест генерации пройден: {new Date(agVerifiedAt).toLocaleString()}
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="primary"
            loading={agStartLogin.isPending}
            disabled={agBusy || !agSystemEnabled || !agSystemConfigured}
            onClick={() => agStartLogin.mutate()}
          >
            {agAuthorized ? 'Переподключить Google' : 'Войти через Google'}
          </Button>
          {agAuthorized && (
            <Button
              size="sm"
              variant="default"
              leftIcon={<RefreshCw className={`h-3 w-3 ${agRefresh.isPending ? 'animate-spin' : ''}`} />}
              loading={agRefresh.isPending}
              disabled={agBusy || !agSystemEnabled || !agSystemConfigured}
              onClick={() => agRefresh.mutate()}
            >
              Обновить токен и каталог
            </Button>
          )}
          {agAuthorized && (
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

        {agAuthorized && agSystemEnabled && agSystemConfigured && (
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
              checked={agIncludeInternal}
              disabled={agBusy}
              onCheckedChange={(checked) => agToggleInternal.mutate(checked)}
            />
            {agTestGeneration.data?.ok && (
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
                Откройте страницу авторизации Google, подтвердите доступ и вставьте <strong>полный URL перенаправления</strong> из адресной строки браузера, содержащий параметры <span className="font-mono">state</span> и <span className="font-mono">code</span>.
              </div>
              <a
                href={agPending.auth_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[#CFA9F5] underline w-fit"
              >
                Открыть страницу Google OAuth <ExternalLink className="h-3 w-3" />
              </a>
              <Field label="Полный URL перенаправления">
                <Input
                  placeholder="https://antigravity.google/oauth-callback?state=...&code=..."
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
        {agOAuthError && !agActionError && (
          <Notice tone="error" title="Ошибка OAuth Google">{agOAuthError}</Notice>
        )}
        {agQuotaError && !agActionError && (
          <Notice tone="warning" title="Проблема доступа или квоты Cloud Code">{agQuotaError}</Notice>
        )}
        {agServerError && !agActionError && (
          <Notice tone="warning" title="Ошибка Google Antigravity">{agServerError}</Notice>
        )}

        {agAuthorized && agModels.length > 0 && (
          <div className="border border-[#242424] bg-[#111111] p-2.5 flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2 text-[11px] text-[#929292]">
              <span>Доступные модели аккаунта ({agModels.length})</span>
              {agStatus?.checked_at && (
                <span>Обновлено: {new Date(agStatus.checked_at).toLocaleTimeString()}</span>
              )}
            </div>
            <div className="max-h-56 overflow-y-auto divide-y divide-[#1E1E1E]">
              {agModels.map((m) => {
                const pct = m.remaining_percent;
                const inLim = formatLimit(m.max_input_tokens || m.context_window);
                const outLim = formatLimit(m.max_output_tokens);
                return (
                  <div key={m.id} className="py-1.5 flex flex-wrap items-center justify-between gap-2 text-[11px]">
                    <div className="min-w-0 flex flex-col">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[#EAEAEA] font-medium truncate">{m.name || m.id}</span>
                        <span className="font-mono text-[#787878] truncate">{m.id}</span>
                        {m.available === false && <Badge variant="warning">недоступна</Badge>}
                      </div>
                      {(inLim || outLim || m.reset_time) && (
                        <div className="text-[#787878] flex flex-wrap gap-x-2">
                          {inLim && <span>вход: {inLim}</span>}
                          {outLim && <span>выход: {outLim}</span>}
                          {m.reset_time && <span>сброс: {new Date(m.reset_time).toLocaleString()}</span>}
                        </div>
                      )}
                    </div>
                    <div className="shrink-0 flex items-center gap-2">
                      {pct != null ? (
                        <Badge variant={pct > 25 ? 'success' : pct > 0 ? 'warning' : 'danger'}>
                          {pct}% квоты
                        </Badge>
                      ) : (
                        <span className="text-[#787878]">квота не передана</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {antigravityData?.last_success && (
          <div className="text-[11px] text-[#929292]">
            Последняя проверка: {new Date(antigravityData.last_success).toLocaleString()}
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
