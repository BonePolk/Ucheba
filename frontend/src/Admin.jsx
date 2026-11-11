import { Alert, Button, Group, Loader, Paper, Stack, Switch, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

const labels = { telegram: 'Telegram-аккаунты', gemini: 'Анализ Gemini', codex: 'Выполнение Codex', jev: 'Jeba (резерв)', context: 'Monocle', ameba: 'Ameba', straus: 'Straus', collector: 'Боты сбора задач', notifications: 'Боты уведомлений', registration: 'Регистрация новых пользователей' };

export default function Admin() {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ['admin'], queryFn: () => request('/admin'), refetchInterval: 15000 });
  const issues = useQuery({ queryKey: ['agent-issues'], queryFn: () => request('/admin/agent-issues'), refetchInterval: 15000 });
  const resolve = useMutation({ mutationFn: (id) => request(`/admin/agent-issues/${id}/resolve`, { method: 'POST' }), onSuccess: () => client.invalidateQueries({ queryKey: ['agent-issues'] }) });
  const update = useMutation({
    mutationFn: ({ name, enabled }) => request(`/admin/features/${name}`, { method: 'PATCH', body: JSON.stringify({ enabled }) }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ['admin'] }); await client.invalidateQueries({ queryKey: ['me'] }); },
  });
  if (state.isPending) return <Loader />;
  if (state.error) return <Alert color="red">{state.error.message}</Alert>;
  return <Stack><Title order={2}>Администрирование</Title>
    <Paper withBorder p="lg"><Text>Пользователей: {state.data.user_count}</Text></Paper>
    <Paper withBorder p="lg"><Stack><Title order={4}>Функции для всех пользователей</Title>
      {Object.entries(state.data.features).map(([name, enabled]) => <Switch key={name} label={labels[name] ?? name} checked={enabled} disabled={update.isPending} onChange={(event) => update.mutate({ name, enabled: event.currentTarget.checked })} />)}
    </Stack></Paper>
    <Paper withBorder p="md"><Stack gap="xs"><Title order={4}>Проблемы агентов</Title>
      {issues.error && <Alert color="red">{issues.error.message}</Alert>}
      {!issues.isPending && !issues.data?.length && <Text size="sm" c="dimmed">Новых сообщений нет.</Text>}
      {(issues.data ?? []).map((issue) => <Group key={issue.id} justify="space-between">
        <div><Text size="sm">{issue.agent} · {issue.tool_name} · {{ timeout: 'Таймаут', element_not_found: 'Элемент не найден', login_failed: 'Ошибка входа', unsupported_file: 'Формат не поддерживается', invalid_format: 'Неверный формат', service_unavailable: 'Сервис недоступен', missing_capability: 'Нужна новая возможность', other: 'Другая ошибка' }[issue.category]}</Text><Text size="xs" style={{ overflowWrap: 'anywhere' }}>{issue.description}</Text><Text size="xs" c="dimmed">Пользователь №{issue.owner_id} · Повторений: {issue.occurrences} · {new Date(issue.updated_at).toLocaleString()}</Text></div>
        <Button size="compact-xs" variant="subtle" disabled={resolve.isPending} onClick={() => resolve.mutate(issue.id)}>Закрыть</Button>
      </Group>)}
      {resolve.error && <Alert color="red">{resolve.error.message}</Alert>}
    </Stack></Paper>
    {update.error && <Alert color="red">{update.error.message}</Alert>}
  </Stack>;
}
