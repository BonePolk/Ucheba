import { Badge, Group, Paper, Stack, Text } from '@mantine/core';

export default function AgentQueue({ queue, error }) {
  if (error) return <Text size="xs" c="yellow">Очередь временно недоступна</Text>;
  if (!queue?.busy && !queue?.active?.length && !queue?.waiting?.length) return null;
  const active = queue.active ?? [];
  const waiting = queue.waiting ?? [];
  return <Paper withBorder p="sm" radius="md"><Stack gap="xs">
    <Group justify="space-between"><Text size="sm" fw={500}>Очередь агентов</Text><Badge size="xs" variant="light">{waiting.length} ожидают</Badge></Group>
    {active.map((item, index) => <Stack key={`${item.provider}-${item.task_id}-${index}`} gap={6}>
      <Group gap={6}><Badge size="xs" color="purple" variant="light">{item.provider === 'context' ? item.task_id ? 'Lucheba' : 'Straus' : 'Ameba'} · работает</Badge>
        {item.task_id ? <Text component="a" size="sm" href={`/?task=${item.task_id}&browser=1`}>{item.title} ↗</Text> : <Text size="sm">{item.title}</Text>}
      </Group>
    </Stack>)}
    {waiting.map((item, index) => <Group key={`${item.provider}-${item.task_id}-${index}`} gap={6}>
      <Text size="xs" c="dimmed">{index + 1}.</Text><Text size="xs" c="dimmed">{item.provider === 'context' ? item.task_id ? 'Lucheba' : 'Straus' : 'Ameba'}</Text>
      {item.task_id ? <Text component="a" size="xs" href={`/?task=${item.task_id}&browser=1`}>{item.title} ↗</Text> : <Text size="xs">{item.title}</Text>}
    </Group>)}
    {!active.length && !waiting.length && <Text size="xs" c="dimmed">Ожидаем освобождения агентов</Text>}
  </Stack></Paper>;
}
