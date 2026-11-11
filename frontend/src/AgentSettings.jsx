import { useState } from 'react';
import { Alert, Autocomplete, Button, Group, Paper, PasswordInput, Stack, Switch, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function AgentSettings({ name, title, description, data }) {
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(data.enabled);
  const [model, setModel] = useState(data.model || 'gpt-6-luna');
  const [key, setKey] = useState('');
  const [rewrite, setRewrite] = useState(data.rewrite_title ?? false);
  const models = useQuery({ queryKey: ['agent-models', name], queryFn: () => request(`/integrations/agents/${name}/models`), enabled: false, retry: false });
  const save = useMutation({
    mutationFn: (remove = false) => request(`/integrations/${name}`, { method: 'PATCH', body: JSON.stringify({
      enabled: remove ? false : enabled, model,
      ...(name === 'context' ? { rewrite_title: rewrite } : {}),
      ...(remove ? { api_key: null } : key ? { api_key: key } : {}),
    }) }),
    onSuccess: async (_, remove) => {
      setKey(''); if (remove) setEnabled(false);
      client.removeQueries({ queryKey: ['agent-models', name] });
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['jev-ranking'] });
    },
  });
  return <Paper withBorder p="lg" radius="md"><Stack gap="sm">
    <Title order={3}>{title}</Title><Text size="xs" c="dimmed">{description}</Text>
    <Switch label="Включён" checked={enabled} onChange={(event) => setEnabled(event.currentTarget.checked)} />
    <PasswordInput label="Личный API-ключ" placeholder={data.configured?.api_key ? 'Сохранён; пустое поле оставит прежний' : 'Ключ шлюза lenec.tech'} value={key} onChange={(event) => setKey(event.currentTarget.value)} autoComplete="new-password" />
    <Autocomplete label="Модель" value={model} onChange={setModel} data={models.data ?? [data.model || 'gpt-6-luna']} placeholder="ID модели шлюза" />
    <Button size="compact-xs" variant="subtle" loading={models.isFetching} disabled={!data.configured?.api_key || save.isPending} onClick={() => models.refetch()}>Загрузить доступные модели</Button>
    <Text size="xs" c="dimmed">Можно ввести ID вручную. Нужна поддержка Responses API и инструментов. Сначала сохрани ключ.</Text>
    {name === 'context' && <Switch label="Разрешить менять название задачи" checked={rewrite} onChange={(event) => setRewrite(event.currentTarget.checked)} />}
    <Group><Button loading={save.isPending} disabled={!model.trim()} onClick={() => save.mutate(false)}>Сохранить</Button><Button variant="subtle" color="red" disabled={save.isPending} onClick={() => save.mutate(true)}>Удалить ключ</Button></Group>
    {(save.error || models.error) && <Alert color="red">{save.error?.message || models.error?.message}</Alert>}
    {save.isSuccess && <Text size="xs" c="green">Сохранено</Text>}
    {data.last_error && <Alert color="yellow">{data.last_error}</Alert>}
  </Stack></Paper>;
}
