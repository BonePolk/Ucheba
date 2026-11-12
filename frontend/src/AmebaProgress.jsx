import { Badge, Group, Progress, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';
import AgentProgress from './AgentProgress';
import AgentTimeline from './AgentTimeline';

export default function AmebaProgress({ taskId = 0, active = false }) {
  const ranking = useQuery({ queryKey: ['ameba-ranking'], queryFn: () => request('/integrations/ameba/ranking'), refetchInterval: 5000 });
  return <AgentProgress taskId={taskId} providers={['context', 'ameba']} active={active}>
    {(entries, current) => {
      const entry = entries.find((item) => item.provider === 'ameba');
      const run = entry?.run;
      const running = run?.status === 'running';
      const queued = entry?.queued && !running;
      const live = running || run?.status === 'completed' && (!ranking.data?.timeline || run.started_at > (ranking.data?.generated_at ?? ''));
      const tasks = !running && ranking.data?.plan_reset ? [] : live ? run.timeline : ranking.data?.timeline;
      const plan = !running && ranking.data?.plan_reset ? null : live ? run.schedule : ranking.data?.schedule;
      const schedule = plan ?? ranking.data?.calendar_schedule;
      const calendarOnly = !plan;
      const percent = run?.total ? Math.floor(run.completed / run.total * 100) : 0;
      if (!current || current.provider !== 'ameba') return null;
      return <Stack gap={8} mt="xs">
        {(running || queued) && <Stack gap={8}>
          <Group gap={6}><Badge size="xs" variant="light">Ameba</Badge><Text size="xs" c="dimmed">{queued ? 'В очереди' : run?.stage === 'planning' ? 'Планирует' : 'Сортирует'}</Text></Group>
          <Progress size={3} radius="xl" value={!run?.total ? 100 : percent} animated striped aria-label="Прогресс Ameba" />
        </Stack>}
        {(schedule?.blocks?.length > 0 || tasks?.some((task) => task.plan)) && <AgentTimeline tasks={tasks} schedule={schedule} calendarOnly={calendarOnly} finished={!running && !queued} />}
      </Stack>;
    }}
  </AgentProgress>;
}
