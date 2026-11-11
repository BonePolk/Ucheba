import { Button, Group, Text } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { request } from './api';

export default function AgentProblemReport({ agent, toolName }) {
  const report = useMutation({ mutationFn: () => request('/auth/agent-issues', {
    method: 'POST', body: JSON.stringify({ agent, tool_name: toolName, category: 'other' }),
  }) });
  return <Group gap="xs">
    <Button size="compact-xs" variant="subtle" loading={report.isPending} disabled={report.isSuccess} onClick={() => report.mutate()}>{report.isSuccess ? 'Администратору сообщено' : 'Сообщить администратору'}</Button>
    {report.error && <Text size="xs" c="red">{report.error.message}</Text>}
  </Group>;
}
