import { useEffect } from 'react';
import { Progress, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';

export default function JevProgress({ taskId = 0, active = false }) {
  const query = useQuery({
    queryKey: ['jev-progress', taskId],
    queryFn: () => request(`/integrations/progress?providers=jev&task_id=${taskId}`),
    retry: false,
    refetchInterval: (query) => active || query.state.data?.[0]?.run?.status === 'running' ? 1000 : false,
  });
  useEffect(() => { query.refetch(); }, [active, query.refetch]);
  const run = query.data?.[0]?.run;
  const running = active || run?.status === 'running';
  if (!running && !run) return null;
  const failed = run?.status === 'failed' || run?.status === 'interrupted';
  const percent = run?.status === 'completed' ? 100 : run?.total ? Math.floor(run.completed / run.total * 100) : 0;
  const label = failed ? 'Сортировка прервана' : run?.status === 'completed' ? 'Сравнение завершено' :
    run?.stage === 'loading' ? 'Загружается модель Jev…' : run?.stage === 'sorting' ? 'Jev сортирует задачи…' : 'Ожидаем сортировку Jev…';
  return <Stack gap={4}>
    <Text size="xs" c={failed ? 'red' : 'dimmed'}>{label}{run?.total > 0 && ` ${percent}% · Сравнений: ${run.comparisons}`}</Text>
    <Progress value={running && !run?.total ? 100 : percent} animated={running} striped={running} color={failed ? 'red' : 'purple'} aria-label="Прогресс сортировки Jev" />
    {(query.error || query.data?.[0]?.available === false) && <Text size="xs" c="yellow">Прогресс временно недоступен</Text>}
  </Stack>;
}
