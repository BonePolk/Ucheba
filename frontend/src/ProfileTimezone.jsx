import { useState } from 'react';
import { Alert, Autocomplete, Button, Group, Paper, Stack } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function ProfileTimezone() {
  const client = useQueryClient();
  const [draft, setDraft] = useState(null);
  const current = useQuery({ queryKey: ['calendar-timezone'], queryFn: () => request('/calendar/timezone') });
  const zones = [...new Set(['UTC', ...(Intl.supportedValuesOf?.('timeZone') ?? [])])];
  const save = useMutation({
    mutationFn: () => request('/calendar/timezone', { method: 'PATCH', body: JSON.stringify({ timezone: draft.trim() }) }),
    onSuccess: async (result) => {
      client.setQueryData(['calendar-timezone'], result);
      setDraft(null);
      await client.invalidateQueries({ queryKey: ['calendar'] });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
  });
  return <Paper withBorder p="md"><Stack gap="xs">
    <Group align="end">
      <Autocomplete label="Часовой пояс" placeholder="Europe/Moscow" data={zones} value={draft ?? current.data?.timezone ?? ''}
        onChange={setDraft} disabled={current.isPending || save.isPending} style={{ flex: 1, minWidth: 180 }} limit={12} />
      <Button disabled={draft === null || !draft.trim() || current.isError} loading={save.isPending} onClick={() => save.mutate()}>Сохранить</Button>
    </Group>
    {(current.error || save.error) && <Alert color="red">{(current.error || save.error).message}</Alert>}
  </Stack></Paper>;
}
