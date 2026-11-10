import { Badge, Group, Progress, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { request } from './api';
import AgentProgress from './AgentProgress';
import AgentTimeline from './AgentTimeline';

export default function JebaProgress({ taskId = 0, active = false }) {
  const ranking = useQuery({ queryKey: ['jev-ranking'], queryFn: () => request('/integrations/jev/ranking'), refetchInterval: 5000 });
  return <AgentProgress taskId={taskId} providers={['context', 'jev']} active={active}>
    {(entries) => {
      const entry = entries.find((item) => item.provider === 'jev');
      const run = entry?.run;
      const running = run?.status === 'running';
      const queued = entry?.queued && !running;
      const live = running || run?.status === 'completed' && (!ranking.data?.timeline || run.started_at > (ranking.data?.generated_at ?? ''));
      const tasks = live ? run.timeline : ranking.data?.timeline;
      const schedule = live ? run.schedule : ranking.data?.schedule;
      const percent = run?.total ? Math.floor(run.completed / run.total * 100) : 0;
      return <Stack gap={8} mt="xs">
        {(running || queued) && <>
          <Group gap={6}><Badge size="xs" variant="light">Ameba</Badge><Text size="xs" c="dimmed">{queued ? 'В очереди' : run.stage === 'planning' ? 'Планирует' : 'Сортирует'}</Text></Group>
          <Progress size={3} radius="xl" value={!run?.total ? 100 : percent} animated striped aria-label="Прогресс Ameba" />
        </>}
        {(tasks?.length > 0 || running) && <AgentTimeline tasks={tasks} schedule={schedule} finished={!running && !queued} />}
      </Stack>;
    }}
  </AgentProgress>;
}
