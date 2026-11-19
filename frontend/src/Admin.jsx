import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request, agentName } from './api';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
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
  const issues = useQuery({ queryKey: ['agent-issues'], queryFn: () => request('/admin/agent-issues'), refetchInterval: 15000 });
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
