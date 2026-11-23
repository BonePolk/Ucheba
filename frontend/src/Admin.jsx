import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request, agentName } from './api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Badge, EmptyState, Notice, PageLoader, SectionHeader } from '@/components/ui/surface';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TableContainer } from '@/components/ui/table';

const labels = {
  telegram: 'Telegram-аккаунты',
  arrodes: 'Выполнение Arrodes',
  monocle: 'Monocle',
  ameba: 'Ameba',
  straus: 'Straus',
  collector: 'Боты сбора задач',
  notifications: 'Боты уведомлений',
  registration: 'Регистрация новых пользователей',
};

const categoryLabels = {
  timeout: 'Таймаут',
  element_not_found: 'Элемент не найден',
  login_failed: 'Ошибка входа',
  unsupported_file: 'Формат не поддерживается',
  invalid_format: 'Неверный формат',
  service_unavailable: 'Сервис недоступен',
  missing_capability: 'Нужна новая возможность',
  other: 'Другая ошибка',
};

export default function Admin() {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ['admin'], queryFn: () => request('/admin'), refetchInterval: 15000 });
  const agAdmin = useQuery({ queryKey: ['admin', 'antigravity'], queryFn: () => request('/admin/antigravity') });
  const issues = useQuery({ queryKey: ['agent-issues'], queryFn: () => request('/admin/agent-issues'), refetchInterval: 15000 });

  const [agEnabled, setAgEnabled] = useState(true);
  const [agClientId, setAgClientId] = useState('');
  const [agClientSecret, setAgClientSecret] = useState('');
  const [agRedirectUri, setAgRedirectUri] = useState('https://antigravity.google/oauth-callback');
  const [agCloudcodeUrl, setAgCloudcodeUrl] = useState('https://daily-cloudcode-pa.googleapis.com');
  const [agUserAgent, setAgUserAgent] = useState('');
  const [agScopes, setAgScopes] = useState('');
  const [agDiagnostic, setAgDiagnostic] = useState(false);

  useEffect(() => {
    if (!agAdmin.data) return;
    setAgEnabled(Boolean(agAdmin.data.enabled));
    setAgClientId(agAdmin.data.client_id || '');
    setAgRedirectUri(agAdmin.data.redirect_uri || 'https://antigravity.google/oauth-callback');
    setAgCloudcodeUrl(agAdmin.data.cloudcode_url || 'https://daily-cloudcode-pa.googleapis.com');
    setAgUserAgent(agAdmin.data.user_agent || '');
    setAgScopes(agAdmin.data.custom_scopes && Array.isArray(agAdmin.data.allowed_scopes) ? agAdmin.data.allowed_scopes.join(' ') : '');
    setAgDiagnostic(Boolean(agAdmin.data.diagnostic_mode));
  }, [agAdmin.data]);

  const agSave = useMutation({
    mutationFn: () => request('/admin/antigravity', {
      method: 'PATCH',
      body: JSON.stringify({
        enabled: agEnabled,
        client_id: agClientId.trim(),
        ...(agClientSecret.trim() ? { client_secret: agClientSecret.trim() } : {}),
        redirect_uri: agRedirectUri.trim(),
        cloudcode_url: agCloudcodeUrl.trim(),
        user_agent: agUserAgent.trim(),
        allowed_scopes: agScopes.trim(),
        diagnostic_mode: agDiagnostic,
      }),
    }),
    onSuccess: async () => {
      setAgClientSecret('');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin', 'antigravity'] }),
        client.invalidateQueries({ queryKey: ['integrations'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'antigravity'] }),
      ]);
    },
  });

  const agValidate = useMutation({
    mutationFn: () => request('/admin/antigravity/validate', { method: 'POST' }),
  });

  const agResetSecret = useMutation({
    mutationFn: () => request('/admin/antigravity/reset-secret', { method: 'POST' }),
    onSuccess: async () => {
      setAgClientSecret('');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin', 'antigravity'] }),
        client.invalidateQueries({ queryKey: ['integrations'] }),
        client.invalidateQueries({ queryKey: ['integrations', 'antigravity'] }),
      ]);
    },
  });
  const resolve = useMutation({
    mutationFn: (id) => request(`/admin/agent-issues/${id}/resolve`, { method: 'POST' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['agent-issues'] }),
  });
  const update = useMutation({
    mutationFn: ({ name, enabled }) => request(`/admin/features/${name}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['admin'] });
      await client.invalidateQueries({ queryKey: ['me'] });
    },
  });

  if (state.isPending) return <PageLoader label="Загрузка панели администратора…" />;
  if (state.error) return <Notice tone="error">{state.error.message}</Notice>;

  const featureEntries = Object.entries(state.data.features).filter(([name]) => name in labels);

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2">
        <SectionHeader title="Функции" meta={`Пользователей: ${state.data.user_count}`} />
        <div className="border border-[#303030] bg-[#111111] divide-y divide-[#1E1E1E]">
          {featureEntries.map(([name, enabled]) => (
            <div key={name} className="flex items-center justify-between gap-3 px-3 py-2 max-md:py-3">
              <span className="text-[13px] text-[#EAEAEA]">{labels[name] ?? name}</span>
              <Switch
                aria-label={labels[name] ?? name}
                checked={enabled}
                disabled={update.isPending}
                onCheckedChange={(checked) => update.mutate({ name, enabled: checked })}
              />
            </div>
          ))}
        </div>
        {update.error && <Notice tone="error">{update.error.message}</Notice>}
      </section>

      <section className="flex flex-col gap-2">
        <SectionHeader title="Google Antigravity — системная конфигурация" />
        <div className="border border-[#303030] bg-[#111111] p-3 flex flex-col gap-3">
          {agAdmin.error && <Notice tone="error">{agAdmin.error.message}</Notice>}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Switch
              label="Google Antigravity включён для всех пользователей"
              checked={agEnabled}
              onCheckedChange={setAgEnabled}
            />
            <div className="flex flex-wrap gap-1.5">
              <Badge variant={agAdmin.data?.configured ? 'success' : 'warning'}>
                {agAdmin.data?.configured ? 'OAuth настроен' : 'Требует настройки'}
              </Badge>
              <Badge variant={agAdmin.data?.client_secret_configured ? 'success' : 'outline'}>
                {agAdmin.data?.client_secret_configured
                  ? `Secret сохранён (${agAdmin.data.client_secret_source === 'admin' ? 'админка' : 'env'})`
                  : 'Secret не задан'}
              </Badge>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            <Field label="OAuth Client ID">
              <Input
                value={agClientId}
                maxLength={300}
                onChange={(event) => setAgClientId(event.currentTarget.value)}
              />
            </Field>
            <Field label="OAuth Client Secret" hint="Не возвращается в API после сохранения; оставьте пустым, чтобы не менять.">
              <PasswordField
                placeholder={agAdmin.data?.client_secret_configured ? 'Сохранён (введите для замены)' : 'Не задан'}
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
            <Field label="Cloud Code Endpoint" hint="Разрешены только официальные HTTPS-хосты *.googleapis.com">
              <Input
                value={agCloudcodeUrl}
                maxLength={2000}
                onChange={(event) => setAgCloudcodeUrl(event.currentTarget.value)}
              />
            </Field>
            <Field label="User-Agent (опционально)">
              <Input
                placeholder="antigravity/cli/1.5.15 ..."
                value={agUserAgent}
                maxLength={300}
                onChange={(event) => setAgUserAgent(event.currentTarget.value)}
              />
            </Field>
            <Field label="Разрешённые OAuth scopes (через пробел)" hint="Пусто — стандартный набор Antigravity">
              <Input
                placeholder="По умолчанию (cloud-platform, openid, userinfo...)"
                value={agScopes}
                onChange={(event) => setAgScopes(event.currentTarget.value)}
              />
            </Field>
          </div>

          <Switch
            label="Диагностический режим (расширенные диагностические сведения без вывода секретов)"
            checked={agDiagnostic}
            onCheckedChange={setAgDiagnostic}
          />

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={agSave.isPending}
              disabled={agSave.isPending || agValidate.isPending || agResetSecret.isPending}
              onClick={() => agSave.mutate()}
            >
              Сохранить системные параметры
            </Button>
            <Button
              size="sm"
              variant="default"
              loading={agValidate.isPending}
              disabled={agSave.isPending || agValidate.isPending || agResetSecret.isPending}
              onClick={() => agValidate.mutate()}
            >
              Проверить конфигурацию
            </Button>
            {agAdmin.data?.client_secret_configured && (
              <Button
                size="sm"
                variant="danger-ghost"
                loading={agResetSecret.isPending}
                disabled={agSave.isPending || agValidate.isPending || agResetSecret.isPending}
                onClick={() => {
                  if (window.confirm('Удалить сохранённый OAuth Client Secret из системной конфигурации?')) {
                    agResetSecret.mutate();
                  }
                }}
              >
                Сбросить Client Secret
              </Button>
            )}
          </div>

          {agSave.error && <Notice tone="error">{agSave.error.message}</Notice>}
          {agValidate.error && <Notice tone="error">{agValidate.error.message}</Notice>}
          {agResetSecret.error && <Notice tone="error">{agResetSecret.error.message}</Notice>}
          {agSave.isSuccess && <Notice tone="success">Системная конфигурация Google Antigravity сохранена.</Notice>}
          {agValidate.data && (
            <Notice tone={agValidate.data.ok ? 'success' : 'warning'}>
              {agValidate.data.ok
                ? 'Все проверки пройдены: OAuth Client ID, Client Secret, Redirect URI, Cloud Code Endpoint и scopes корректны.'
                : `Проверка выявила проблемы: ${Object.entries(agValidate.data.checks || {}).filter(([, ok]) => !ok).map(([k]) => k).join(', ')}`}
            </Notice>
          )}
        </div>
      </section>

      <section className="flex flex-col gap-2">
        <SectionHeader title="Проблемы агентов" meta={issues.data ? `${issues.data.length}` : undefined} />
        {issues.error && <Notice tone="error">{issues.error.message}</Notice>}
        {!issues.isPending && !issues.data?.length ? (
          <EmptyState title="Новых сообщений о проблемах нет" />
        ) : (
          <>
            <div className="md:hidden border border-[#303030] bg-[#111111] divide-y divide-[#222222]">
              {(issues.data ?? []).map((issue) => (
                <div key={issue.id} className="p-3 flex flex-col gap-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-[13px] min-w-0">
                      <span className="font-medium text-[#EAEAEA]">{agentName(issue.agent)}</span>
                      <span className="text-[#929292]"> · {issue.tool_name}</span>
                    </div>
                    <Badge variant="warning">{categoryLabels[issue.category] ?? issue.category}</Badge>
                  </div>
                  <div className="text-[12px] text-[#DCDCDC] break-words">
                    {issue.description || '—'}
                  </div>
                  <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-[#1E1E1E]">
                    <span className="text-[11px] text-[#929292] tabular-nums">
                      №{issue.owner_id} · ×{issue.occurrences} · {new Date(issue.updated_at).toLocaleString()}
                    </span>
                    <Button size="xs" variant="outline" disabled={resolve.isPending} onClick={() => resolve.mutate(issue.id)}>
                      Закрыть
                    </Button>
                  </div>
                </div>
              ))}
            </div>
            <div className="hidden md:block">
              <TableContainer>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Агент / Инструмент</TableHead>
                      <TableHead>Категория</TableHead>
                      <TableHead>Описание</TableHead>
                      <TableHead>Контекст</TableHead>
                      <TableHead className="text-right">Действие</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(issues.data ?? []).map((issue) => (
                      <TableRow key={issue.id}>
                        <TableCell className="whitespace-nowrap">
                          <span className="font-medium text-[#EAEAEA]">{agentName(issue.agent)}</span>
                          <span className="text-[#929292]"> · {issue.tool_name}</span>
                        </TableCell>
                        <TableCell>
                          <Badge variant="warning">{categoryLabels[issue.category] ?? issue.category}</Badge>
                        </TableCell>
                        <TableCell className="text-[12px] text-[#DCDCDC] max-w-[320px] break-words">
                          {issue.description || '—'}
                        </TableCell>
                        <TableCell className="text-[11px] text-[#929292] tabular-nums whitespace-nowrap">
                          №{issue.owner_id} · ×{issue.occurrences} · {new Date(issue.updated_at).toLocaleString()}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button size="xs" variant="outline" disabled={resolve.isPending} onClick={() => resolve.mutate(issue.id)}>
                            Закрыть
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </div>
          </>
        )}
        {resolve.error && <Notice tone="error">{resolve.error.message}</Notice>}
      </section>
    </div>
  );
}
