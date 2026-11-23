import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronUp, ExternalLink, RefreshCw } from 'lucide-react';
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

  // Google Antigravity OAuth & client config state
  const [agPending, setAgPending] = useState(null);
  const [agCallbackInput, setAgCallbackInput] = useState('');
  const [agConfigOpen, setAgConfigOpen] = useState(false);
  const [agClientId, setAgClientId] = useState(antigravityData?.client_id || '');
  const [agClientSecret, setAgClientSecret] = useState('');
  const [agRedirectUri, setAgRedirectUri] = useState(antigravityData?.redirect_uri || 'https://antigravity.google/oauth-callback');
  const [agCloudcodeUrl, setAgCloudcodeUrl] = useState(antigravityData?.cloudcode_url || 'https://daily-cloudcode-pa.googleapis.com');
  const [agProjectId, setAgProjectId] = useState(antigravityData?.project_id || '');
  const [agUserAgent, setAgUserAgent] = useState(antigravityData?.user_agent || '');
  const [agIncludeInternal, setAgIncludeInternal] = useState(Boolean(antigravityData?.include_internal));

  useEffect(() => {
    setAgClientId(antigravityData?.client_id || '');
    setAgRedirectUri(antigravityData?.redirect_uri || 'https://antigravity.google/oauth-callback');
    setAgCloudcodeUrl(antigravityData?.cloudcode_url || 'https://daily-cloudcode-pa.googleapis.com');
    setAgProjectId(antigravityData?.project_id || '');
    setAgUserAgent(antigravityData?.user_agent || '');
    setAgIncludeInternal(Boolean(antigravityData?.include_internal));
  }, [
    antigravityData?.client_id,
    antigravityData?.redirect_uri,
    antigravityData?.cloudcode_url,
    antigravityData?.project_id,
    antigravityData?.user_agent,
    antigravityData?.include_internal,
  ]);

  const keyConfigured = Boolean(data?.configured?.api_key);
  const oauthAuthorized = Boolean(oauthState?.data?.authorized);
  const device = oauthState?.data?.device;

  const agStatus = antigravityState?.data;
  const agAuthorized = Boolean(
    agStatus?.authorized
    ?? (antigravityData?.configured?.access_token || antigravityData?.configured?.refresh_token),
  );
  const agEmail = agStatus?.account_email || antigravityData?.account_email || '';
  const agName = agStatus?.account_name || antigravityData?.account_name || '';
  const agEffectiveProject = agStatus?.project_id || antigravityData?.project_id || antigravityData?.discovered_project_id || '';
  const agTier = agStatus?.subscription_tier || antigravityData?.subscription_tier || '';
  const agSecretConfigured = Boolean(agStatus?.client_secret_configured ?? antigravityData?.configured?.client_secret);
  const agModels = Array.isArray(agStatus?.models) ? agStatus.models : [];
  const agTokenExpired = Boolean(agStatus?.token_expired);
  const agSubscribed = agStatus?.subscription_verified;

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
        callback_url_or_code: agCallbackInput.trim(),
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

  const agLogout = useMutation({
    mutationFn: () => request('/integrations/antigravity/logout', { method: 'POST' }),
    onSuccess: async () => {
      setAgPending(null);
      setAgCallbackInput('');
      await invalidateAllAi();
    },
  });

  const agSaveConfig = useMutation({
    mutationFn: (mode = 'save') => request('/integrations/antigravity', {
      method: 'PATCH',
      body: JSON.stringify(
        mode === 'remove_secret'
          ? { client_secret: null }
          : {
              client_id: agClientId.trim(),
              redirect_uri: agRedirectUri.trim(),
              cloudcode_url: agCloudcodeUrl.trim(),
              project_id: agProjectId.trim(),
              user_agent: agUserAgent.trim(),
              include_internal: agIncludeInternal,
              ...(agClientSecret.trim() ? { client_secret: agClientSecret.trim() } : {}),
            },
      ),
    }),
    onSuccess: async () => {
      setAgClientSecret('');
      await invalidateAllAi();
    },
  });

  const oauthBusy = oauthConnect.isPending || oauthDisconnect.isPending;
  const providerBusy = saveProvider.isPending || checkProvider.isPending;
  const agBusy = agStartLogin.isPending || agCompleteLogin.isPending || agRefresh.isPending || agLogout.isPending || agSaveConfig.isPending;
  const oauthError = oauthConnect.error || oauthDisconnect.error;
  const providerError = saveProvider.error || checkProvider.error;
  const agActionError = agStartLogin.error || agCompleteLogin.error || agRefresh.error || agLogout.error || agSaveConfig.error;
  const agServerError = agStatus?.error || antigravityData?.last_error;

  const agBadgeVariant = !agAuthorized
    ? 'outline'
    : agServerError || agSubscribed === false || agTokenExpired
      ? 'warning'
      : 'success';
  const agBadgeText = !agAuthorized
    ? 'Не подключён'
    : agTokenExpired
      ? 'Токен истёк'
      : agServerError || agSubscribed === false
        ? 'Ошибка доступа'
        : 'Подключён';

  return (
    <div className="flex flex-col gap-4">
      <div className="border-b border-[#222222] pb-2.5">
        <SectionHeader title="Подключения AI" />
      </div>

      {/* Google Antigravity (Native OAuth + Cloud Code) */}
      <section className="border border-[#262626] bg-[#0D0D0D] p-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-[13px] font-medium text-[#EAEAEA]">Google Antigravity</span>
          <Badge variant={agBadgeVariant}>{agBadgeText}</Badge>
        </div>

        <div className="text-[11px] text-[#929292]">
          Прямое подключение к Cloud Code API через Google OAuth 2.0 (PKCE) для моделей Gemini и Claude с учётом квот аккаунта.
        </div>

        {agAuthorized && (
          <div className="border border-[#242424] bg-[#111111] px-2.5 py-2 text-[12px] flex flex-col gap-1">
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
            {agStatus?.token_expires_at && (
              <div className="text-[11px] text-[#929292]">
                Токен действует до: {new Date(agStatus.token_expires_at).toLocaleString()}
                {agStatus.has_refresh_token ? ' · автообновление активно' : ' · без refresh_token'}
              </div>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="primary"
            loading={agStartLogin.isPending}
            disabled={agBusy}
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
              disabled={agBusy}
              onClick={() => agRefresh.mutate()}
            >
              Обновить токен и модели
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
          <Button
            size="sm"
            variant="subtle"
            rightIcon={agConfigOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
            onClick={() => setAgConfigOpen((prev) => !prev)}
          >
            Параметры OAuth
          </Button>
        </div>

        {agPending && (
          <Notice tone="accent" title="Завершите вход через Google">
            <div className="flex flex-col gap-2 mt-1">
              <div className="text-[11px] text-[#DCDCDC]">
                Откройте страницу авторизации Google, подтвердите доступ и вставьте итоговый URL перенаправления (или код авторизации).
              </div>
              <a
                href={agPending.auth_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-[#CFA9F5] underline w-fit"
              >
                Открыть страницу Google OAuth <ExternalLink className="h-3 w-3" />
              </a>
              {!agPending.client_secret_configured && (
                <div className="text-[11px] text-[#D4A24C]">
                  Client Secret не задан: если выбранный OAuth-клиент конфиденциальный, укажите Client Secret в «Параметрах OAuth» перед входом.
                </div>
              )}
              <Field label="URL перенаправления или код авторизации">
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

        {agConfigOpen && (
          <div className="border border-[#242424] bg-[#111111] p-2.5 flex flex-col gap-2.5">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <Field label="OAuth Client ID">
                <Input
                  value={agClientId}
                  maxLength={300}
                  onChange={(event) => setAgClientId(event.currentTarget.value)}
                />
              </Field>
              <Field label="OAuth Client Secret">
                <PasswordField
                  placeholder={agSecretConfigured ? 'Сохранён' : 'Не задан'}
                  value={agClientSecret}
                  autoComplete="new-password"
                  onChange={(event) => setAgClientSecret(event.currentTarget.value)}
                />
              </Field>
              <Field label="Redirect URI">
                <Input
                  value={agRedirectUri}
                  maxLength={500}
                  onChange={(event) => setAgRedirectUri(event.currentTarget.value)}
                />
              </Field>
              <Field label="Cloud Code Endpoint">
                <Input
                  value={agCloudcodeUrl}
                  maxLength={2000}
                  onChange={(event) => setAgCloudcodeUrl(event.currentTarget.value)}
                />
              </Field>
              <Field label="Cloud Code Project ID (опционально)" hint="Пусто — автоопределение через loadCodeAssist">
                <Input
                  placeholder="Автоопределение"
                  value={agProjectId}
                  maxLength={200}
                  onChange={(event) => setAgProjectId(event.currentTarget.value)}
                />
              </Field>
              <Field label="User-Agent (опционально)" hint="Пусто — стандартный CLI User-Agent">
                <Input
                  placeholder="antigravity/cli/1.5.15 ..."
                  value={agUserAgent}
                  maxLength={300}
                  onChange={(event) => setAgUserAgent(event.currentTarget.value)}
                />
              </Field>
            </div>
            <Switch
              label="Показывать внутренние / экспериментальные модели без displayName"
              checked={agIncludeInternal}
              onCheckedChange={setAgIncludeInternal}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                loading={agSaveConfig.isPending && agSaveConfig.variables === 'save'}
                disabled={agBusy || !agClientId.trim()}
                onClick={() => agSaveConfig.mutate('save')}
              >
                Сохранить параметры
              </Button>
              {agSecretConfigured && (
                <Button
                  size="sm"
                  variant="danger-ghost"
                  loading={agSaveConfig.isPending && agSaveConfig.variables === 'remove_secret'}
                  disabled={agBusy}
                  onClick={() => agSaveConfig.mutate('remove_secret')}
                >
                  Удалить Client Secret
                </Button>
              )}
            </div>
            {agSaveConfig.isSuccess && <Notice tone="success">Параметры Google Antigravity сохранены.</Notice>}
          </div>
        )}

        {agActionError && <Notice tone="error">{agActionError.message}</Notice>}
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
