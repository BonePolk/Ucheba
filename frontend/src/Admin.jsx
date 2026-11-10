import { Alert, Loader, Paper, Stack, Switch, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

const labels = { telegram: 'Telegram-аккаунты', gemini: 'Анализ Gemini', codex: 'Выполнение Codex', jev: 'Порядок задач Ameba', context: 'Контекст Lucheba', collector: 'Боты сбора задач', notifications: 'Боты уведомлений', registration: 'Регистрация новых пользователей' };

export default function Admin() {
  const client = useQueryClient();
  const state = useQuery({ queryKey: ['admin'], queryFn: () => request('/admin'), refetchInterval: 15000 });
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
    {update.error && <Alert color="red">{update.error.message}</Alert>}
  </Stack>;
}
