import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { request } from './api';
import ModelPicker from './ModelPicker';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, Textarea } from '@/components/ui/input';
import { SelectField } from '@/components/ui/select';
import { Badge, Notice, SectionHeader, StatusIndicator } from '@/components/ui/surface';
import { cn } from '@/lib/utils';

const CAP_SOURCE_LABELS = {
  provider: 'метаданные провайдера',
  codex: 'метаданные Codex',
  thread: 'метаданные сессии',
  runtime: 'фактический лимит рантайма',
  config: 'настройка конфигурации',
  manual: 'задано вручную',
};

function formatTokens(val) {
  return Number(val).toLocaleString('ru-RU');
}

function sourceText(source) {
  if (!source || source === 'unknown') return 'неизвестно';
  return source.split('+').map((part) => CAP_SOURCE_LABELS[part] || part).join(' + ');
}

const EFFORT_OPTIONS = [
  { value: 'low', label: 'Быстро (low)' },
  { value: 'medium', label: 'Сбалансировано (medium)' },
  { value: 'high', label: 'Глубоко (high)' },
];

const PLANNING_STYLE_OPTIONS = [
  { value: 'gentle', label: 'Щадящий' },
  { value: 'balanced', label: 'Сбалансированный' },
  { value: 'intense', label: 'Интенсивный' },
];

const WEB_SEARCH_OPTIONS = [
  { value: 'cached', label: 'Кэшированный' },
  { value: 'live', label: 'Живой поиск' },
  { value: 'disabled', label: 'Отключён' },
];

const INSTRUCTION_PLACEHOLDERS = {
  monocle: 'Например: кратко выделять критерии сдачи и формат проверки…',
  ameba: 'Например: не планировать тяжёлую работу по вечерам пятницы…',
  straus: 'Например: при добавлении пар указывать корпус и аудиторию…',
  arrodes: 'Например: оформлять отчёты в Markdown и прикладывать проверку…',
};

export default function AgentSettings({ name, title, data, provider, oauthState, onOpenProvider }) {
  const client = useQueryClient();
  const defaultMode = name === 'arrodes' ? 'oauth' : 'api_key';
  const savedAuthMode = data.auth_mode || defaultMode;
  const savedModel = data.model ?? (savedAuthMode === 'oauth' ? '' : 'gpt-6-luna');
  const savedContextWindow = data.context_window != null ? String(data.context_window) : '';

  const [enabled, setEnabled] = useState(data.enabled);
  const [authMode, setAuthMode] = useState(savedAuthMode);
  const [model, setModel] = useState(savedModel);
  const [contextWindow, setContextWindow] = useState(savedContextWindow);
  const [effort, setEffort] = useState(data.reasoning_effort || 'medium');
  const [customInstructions, setCustomInstructions] = useState(data.custom_instructions ?? '');

  // Per-agent settings
  const [rewrite, setRewrite] = useState(data.rewrite_title ?? false);
  const [planningStyle, setPlanningStyle] = useState(data.planning_style || 'balanced');
  const [autoReplan, setAutoReplan] = useState(data.auto_replan ?? true);
  const [allowContextRecheck, setAllowContextRecheck] = useState(data.allow_context_recheck ?? true);
  const [webSearchMode, setWebSearchMode] = useState(data.web_search_mode || 'cached');
  const [autoPrepare, setAutoPrepare] = useState(data.auto_prepare ?? false);
  const [allowShell, setAllowShell] = useState(data.allow_shell ?? true);
  const [allowBrowser, setAllowBrowser] = useState(data.allow_browser ?? true);
  const [askClarifications, setAskClarifications] = useState(data.ask_clarifications ?? true);

  useEffect(() => {
    setEnabled(data.enabled);
  }, [data.enabled]);

  useEffect(() => {
    setContextWindow(savedContextWindow);
  }, [savedContextWindow]);

  const trimmedCtx = contextWindow.replace(/[\s_]+/g, '');
  let parsedContextWindow = null;
  let contextWindowError = '';
  if (trimmedCtx) {
    if (!/^\d+$/.test(trimmedCtx)) {
      contextWindowError = 'Введите целое число токенов или оставьте пустым для Авто';
    } else {
      const n = Number(trimmedCtx);
      if (n < 4096 || n > 10_000_000) {
        contextWindowError = 'Допустимый диапазон: от 4 096 до 10 000 000';
      } else {
        parsedContextWindow = n;
      }
    }
  }

  function switchAuthMode(nextMode) {
    if (nextMode === authMode) return;
    setAuthMode(nextMode);
    if (nextMode === savedAuthMode) {
      setModel(savedModel);
    } else if (model === savedModel) {
      setModel('');
    }
  }

  const toggle = useMutation({
    mutationFn: (value) => request(`/integrations/${name}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: value }),
    }),
    onSuccess: async (_, value) => {
      setEnabled(value);
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
  });

  const save = useMutation({
    mutationFn: () => request(`/integrations/${name}`, {
      method: 'PATCH',
      body: JSON.stringify({
        enabled,
        auth_mode: authMode,
        model: model.trim(),
        context_window: parsedContextWindow,
        reasoning_effort: effort,
        custom_instructions: customInstructions,
        ...(name === 'monocle' ? {
          rewrite_title: rewrite,
          allow_browser: allowBrowser,
          ask_clarifications: askClarifications,
        } : {}),
        ...(name === 'ameba' ? {
          planning_style: planningStyle,
          auto_replan: autoReplan,
          allow_context_recheck: allowContextRecheck,
        } : {}),
        ...(name === 'straus' ? {
          allow_browser: allowBrowser,
          ask_clarifications: askClarifications,
        } : {}),
        ...(name === 'arrodes' ? {
          web_search_mode: webSearchMode,
          auto_prepare: autoPrepare,
          allow_shell: allowShell,
          allow_browser: allowBrowser,
          ask_clarifications: askClarifications,
        } : {}),
      }),
    }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities', name] }),
        client.invalidateQueries({ queryKey: ['ameba-ranking'] }),
      ]);
    },
  });

  const check = useMutation({
    mutationFn: () => request(`/integrations/agents/${name}/check`, { method: 'POST' }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
        client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities', name] }),
      ]);
    },
  });

  const oauthAuthorized = Boolean(oauthState?.data?.authorized);
  const apiKeyReady = Boolean(provider?.configured?.api_key);
  const selectedReady = authMode === 'oauth' ? oauthAuthorized : apiKeyReady;
  const capQueryKey = [
    'integrations',
    'agent-capabilities',
    name,
    authMode,
    provider?.base_url || '',
    model.trim(),
    parsedContextWindow ?? 'auto',
    selectedReady,
  ];
  const capabilities = useQuery({
    queryKey: capQueryKey,
    enabled: !contextWindowError,
    queryFn: () => request(`/integrations/agents/${name}/capabilities`, {
      method: 'POST',
      body: JSON.stringify({
        auth_mode: authMode,
        model: model.trim(),
        context_window: parsedContextWindow,
      }),
    }),
    staleTime: 30_000,
    retry: false,
  });

  const capData = capabilities.data;
  const detectionUnavailable = Boolean(capData?.detection_unavailable || capabilities.isError);
  const manualCtx = capData?.manual_context_window ?? parsedContextWindow;
  const verifiedCtx = capData?.verified_context_window ?? null;
  const effectiveCtx = capData?.effective_limit ?? (capabilities.isError ? parsedContextWindow : null);
  const capSource = capData?.source || (effectiveCtx != null ? 'manual' : 'unknown');
  const hasUnsavedAuthOrModel = authMode !== savedAuthMode || model.trim() !== savedModel.trim();
  const savedAuthReady = savedAuthMode === 'oauth' ? oauthAuthorized : Boolean(apiKeyReady && savedModel.trim());
  const actionError = save.error || toggle.error || check.error;

  return (
    <div className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222222] pb-2.5">
        <SectionHeader title={title} />
        <div className="flex items-center gap-2">
          <StatusIndicator tone={enabled ? 'success' : 'idle'} label={enabled ? 'Включён' : 'Отключён'} />
          <Switch
            aria-label={`Включить ${title}`}
            checked={enabled}
            disabled={toggle.isPending || save.isPending}
            onCheckedChange={(checked) => toggle.mutate(checked)}
          />
        </div>
      </div>

      {name === 'arrodes' && oauthState?.data && !oauthState.data.sandbox_available && (
        <Notice tone="warning">
          Sandbox недоступен: {oauthState.data.sandbox_error || 'выполнение заблокировано'}.
        </Notice>
      )}

      {/* Connection Mode Selection */}
      <div className="flex flex-col gap-2">
        <Field label="Режим подключения">
          <div className="grid grid-cols-2 border border-[#303030] bg-[#0D0D0D]">
            <button
              type="button"
              onClick={() => switchAuthMode('api_key')}
              className={cn(
                'px-3 py-1.5 text-[12px] font-medium transition-colors cursor-pointer border-r border-[#303030]',
                authMode === 'api_key'
                  ? 'bg-[#7F49B4]/20 text-[#EAEAEA] border-b-2 border-b-[#7F49B4]'
                  : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#161616]',
              )}
            >
              API-ключ
            </button>
            <button
              type="button"
              onClick={() => switchAuthMode('oauth')}
              className={cn(
                'px-3 py-1.5 text-[12px] font-medium transition-colors cursor-pointer',
                authMode === 'oauth'
                  ? 'bg-[#7F49B4]/20 text-[#EAEAEA] border-b-2 border-b-[#7F49B4]'
                  : 'text-[#929292] hover:text-[#EAEAEA] hover:bg-[#161616]',
              )}
            >
              OpenAI OAuth
            </button>
          </div>
        </Field>

        <div className="flex flex-wrap items-center justify-between gap-2 border border-[#262626] bg-[#0D0D0D] px-2.5 py-2 text-[12px]">
          <div className="flex flex-wrap items-center gap-2 min-w-0">
            <Badge variant={selectedReady ? 'success' : 'warning'}>
              {authMode === 'oauth'
                ? (selectedReady ? 'Вход выполнен' : 'Нет входа')
                : (selectedReady ? 'Ключ настроен' : 'Нет ключа')}
            </Badge>
            {authMode !== 'oauth' && (
              <span className="text-[11px] text-[#929292] truncate">
                {provider?.base_url || 'https://lenec.tech/v1'}
              </span>
            )}
          </div>
          {onOpenProvider && (
            <Button size="xs" variant="subtle" onClick={onOpenProvider}>
              Настроить →
            </Button>
          )}
        </div>
      </div>

      {/* Model Selection */}
      <ModelPicker
        path={`/integrations/agents/${name}/models`}
        authMode={authMode}
        providerConfigured={apiKeyReady}
        oauthAuthorized={oauthAuthorized}
        value={model}
        onChange={setModel}
        onModelsLoaded={() => client.invalidateQueries({ queryKey: ['integrations', 'agent-capabilities', name] })}
      />

      {/* Agent-specific settings */}
      <div className="border-t border-[#222222] pt-3 flex flex-col gap-3">
        <div className="text-[12px] font-medium text-[#929292]">Настройки агента</div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <Field
            label="Контекстное окно (токены)"
            hint="Используется только для расчёта бюджета контекста в агенте и не увеличивает фактический лимит модели или провайдера."
            error={contextWindowError}
          >
            <Input
              type="text"
              inputMode="numeric"
              placeholder="Авто"
              value={contextWindow}
              error={Boolean(contextWindowError)}
              onChange={(event) => setContextWindow(event.currentTarget.value)}
              rightSlot={contextWindow ? (
                <button
                  type="button"
                  aria-label="Сбросить на Авто"
                  onClick={() => setContextWindow('')}
                  className="h-6 w-6 max-md:h-9 max-md:w-9 grid place-items-center text-[#787878] hover:text-[#EAEAEA] cursor-pointer"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
            />
          </Field>

          <Field label="Глубина рассуждений">
            <SelectField
              value={effort}
              onChange={(val) => { if (val) setEffort(val); }}
              options={EFFORT_OPTIONS}
            />
          </Field>

          {name === 'ameba' && (
            <Field label="Режим нагрузки">
              <SelectField
                value={planningStyle}
                onChange={(val) => { if (val) setPlanningStyle(val); }}
                options={PLANNING_STYLE_OPTIONS}
              />
            </Field>
          )}

          {name === 'arrodes' && (
            <Field label="Веб-поиск Codex">
              <SelectField
                value={webSearchMode}
                onChange={(val) => { if (val) setWebSearchMode(val); }}
                options={WEB_SEARCH_OPTIONS}
              />
            </Field>
          )}
        </div>

        <div className="border border-[#262626] bg-[#0D0D0D] px-2.5 py-2 text-[11px] flex flex-col gap-0.5">
          {capabilities.isFetching && !capData ? (
            <span className="text-[#929292]">Определение контекстного окна…</span>
          ) : effectiveCtx != null && verifiedCtx != null ? (
            <>
              <div className="text-[#EAEAEA]">
                <span className="text-[#929292]">
                  {manualCtx != null && manualCtx < verifiedCtx
                    ? 'Эффективный контекст: '
                    : 'Подтверждённый контекст: '}
                </span>
                <span className="font-mono font-medium">{formatTokens(effectiveCtx)} токенов</span>
                <span className="text-[#929292]">
                  {manualCtx != null && manualCtx < verifiedCtx
                    ? ` — ограничено вручную (провайдер подтверждает ${formatTokens(verifiedCtx)})`
                    : ` — ${sourceText(capSource)}`}
                </span>
              </div>
              {manualCtx != null && verifiedCtx < manualCtx && (
                <div className="text-[#929292]">
                  Подтверждённое ограничение ниже ручного значения ({formatTokens(manualCtx)} токенов); ручная настройка не увеличивает лимит провайдера.
                </div>
              )}
              {capData?.theoretical_context_window && capData.theoretical_context_window > effectiveCtx && (
                <div className="text-[#929292]">
                  Теоретическое окно модели: {formatTokens(capData.theoretical_context_window)} токенов.
                </div>
              )}
            </>
          ) : effectiveCtx != null ? (
            <>
              <div className="text-[#EAEAEA]">
                <span className="text-[#929292]">Ручное ограничение: </span>
                <span className="font-mono font-medium">{formatTokens(effectiveCtx)} токенов</span>
                <span className="text-[#929292]">
                  {detectionUnavailable
                    ? ' — автоопределение недоступно (не подтверждено провайдером)'
                    : ' — задано вручную (не подтверждено провайдером)'}
                </span>
              </div>
              <div className="text-[#929292]">
                Применяется только для расчёта бюджета агента и не повышает реальный предел провайдера.
              </div>
            </>
          ) : (
            <div className="text-[#929292]">
              Контекстное окно:{' '}
              <span className="text-[#EAEAEA]">
                {detectionUnavailable ? 'Автоопределение недоступно' : 'Неизвестно'}
              </span>
              {detectionUnavailable
                ? ' — сервис агента или метаданные провайдера не ответили; при необходимости укажите ограничение вручную'
                : ' — провайдер не передаёт метаданные контекста; при необходимости укажите вручную'}
            </div>
          )}
        </div>

        <div className="flex flex-col gap-2">
          {name === 'monocle' && (
            <>
              <Switch
                label="Разрешить переписывать названия задач"
                checked={rewrite}
                onCheckedChange={setRewrite}
              />
              <Switch
                label="Доступ к браузеру и веб-поиску"
                checked={allowBrowser}
                onCheckedChange={setAllowBrowser}
              />
              <Switch
                label="Интерактивные вопросы владельцу"
                checked={askClarifications}
                onCheckedChange={setAskClarifications}
              />
            </>
          )}

          {name === 'ameba' && (
            <>
              <Switch
                label="Автопересчёт плана при изменениях задач и календаря"
                checked={autoReplan}
                onCheckedChange={setAutoReplan}
              />
              <Switch
                label="Запрашивать перепроверку контекста у Monocle"
                checked={allowContextRecheck}
                onCheckedChange={setAllowContextRecheck}
              />
            </>
          )}

          {name === 'straus' && (
            <>
              <Switch
                label="Доступ к браузеру и веб-поиску"
                checked={allowBrowser}
                onCheckedChange={setAllowBrowser}
              />
              <Switch
                label="Интерактивные вопросы владельцу"
                checked={askClarifications}
                onCheckedChange={setAskClarifications}
              />
            </>
          )}

          {name === 'arrodes' && (
            <>
              <Switch
                label="Автоподготовка задач из Telegram"
                checked={autoPrepare}
                onCheckedChange={setAutoPrepare}
              />
              <Switch
                label="Разрешить выполнение shell-команд"
                checked={allowShell}
                onCheckedChange={setAllowShell}
              />
              <Switch
                label="Доступ к браузеру"
                checked={allowBrowser}
                onCheckedChange={setAllowBrowser}
              />
              <Switch
                label="Интерактивные вопросы и подтверждения"
                checked={askClarifications}
                onCheckedChange={setAskClarifications}
              />
            </>
          )}
        </div>

        <Field label="Дополнительные указания агенту" hint="До 2000 символов">
          <Textarea
            rows={2}
            maxLength={2000}
            placeholder={INSTRUCTION_PLACEHOLDERS[name] || ''}
            value={customInstructions}
            onChange={(event) => setCustomInstructions(event.currentTarget.value)}
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          variant="primary"
          loading={save.isPending}
          disabled={toggle.isPending || Boolean(contextWindowError) || (authMode === 'api_key' && !model.trim())}
          onClick={() => save.mutate()}
        >
          Сохранить
        </Button>
        <Button
          variant="default"
          loading={check.isPending}
          disabled={save.isPending || toggle.isPending || hasUnsavedAuthOrModel || !savedAuthReady}
          onClick={() => check.mutate()}
        >
          Проверить
        </Button>
      </div>

      {actionError && <Notice tone="error">{actionError.message}</Notice>}
      {save.isSuccess && <Notice tone="success">Сохранено.</Notice>}
      {check.isSuccess && <Notice tone="success">Подключено.</Notice>}
      {data.last_error && <Notice tone="warning" title="Ошибка">{data.last_error}</Notice>}
      {data.last_success && (
        <div className="text-[11px] text-[#929292]">
          Успешно: {new Date(data.last_success).toLocaleString()}
        </div>
      )}
    </div>
  );
}
