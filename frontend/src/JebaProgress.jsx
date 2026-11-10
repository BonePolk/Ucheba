import { useEffect, useState } from 'react';
import { Progress, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';

export default function JebaProgress({ taskId = 0, active = false }) {
  const [now, setNow] = useState(Date.now());
  const query = useQuery({
    queryKey: ['jev-progress', taskId],
    queryFn: () => request(`/integrations/progress?providers=jev&task_id=${taskId}`),
    retry: false,
    refetchInterval: (query) => active || query.state.data?.[0]?.queued || query.state.data?.[0]?.run?.status === 'running' ? 1000 : false,
  });
  useEffect(() => { query.refetch(); }, [active, query.refetch]);
  const run = query.data?.[0]?.run;
  const queued = query.data?.[0]?.queued && run?.status !== 'running';
  const running = active || queued || run?.status === 'running';
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  if (!running && !run) return null;
  const failed = !queued && (run?.status === 'failed' || run?.status === 'interrupted');
  const percent = queued ? 0 : run?.status === 'completed' ? 100 : run?.total ? Math.floor(run.completed / run.total * 100) : 0;
  const label = queued ? 'В очереди: ожидаем завершения Lucheba и освобождения Jeba…' : failed ? 'Сортировка прервана' : run?.status === 'completed' ? 'Сравнение завершено' :
    run?.stage === 'loading' ? 'Загружается модель Jeba…' : run?.stage === 'comparing' ? `Сравниваются задачи #${run.left_id} и #${run.right_id}…` : run?.stage === 'sorting' ? 'Jeba сортирует задачи…' : run?.stage === 'waiting' ? 'В очереди: ожидаем завершения Lucheba и освобождения Jeba…' : 'Ожидаем сортировку Jeba…';
  return <Stack gap={4}>
    <Text size="xs" c={failed ? 'red' : 'dimmed'}>{label}{run?.total > 0 && ` ${percent}% · Сравнений: ${run.comparisons}`}</Text>
    <Progress value={running && !run?.total ? 100 : percent} animated={running} striped={running} color={failed ? 'red' : 'purple'} aria-label="Прогресс сортировки Jeba" />
    {running && run?.stage === 'comparing' && <>
      <Text size="xs" c="dimmed">Проверок пары: {run.windows_done ?? 0} / {run.windows_total || '…'} · Сравнение идёт {Math.max(0, Math.floor((now - new Date(run.comparison_started_at).getTime()) / 1000))} с</Text>
      <Progress size="xs" value={run.windows_total ? Math.min(100, (run.windows_done ?? 0) / run.windows_total * 100) : 0} aria-label="Прогресс двух проверок пары задач" />
    </>}
    {(query.error || query.data?.[0]?.available === false) && <Text size="xs" c="yellow">Прогресс временно недоступен</Text>}
  </Stack>;
}
