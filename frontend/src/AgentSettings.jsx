import { useState } from 'react';
import { Alert, Button, Group, Paper, PasswordInput, Stack, Switch, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import ModelPicker from './ModelPicker';

export default function AgentSettings({ name, title, data }) {
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(data.enabled);
  const [model, setModel] = useState(data.model || 'gpt-6-luna');
  const [key, setKey] = useState('');
  const [endpoint, setEndpoint] = useState(data.base_url || 'https://lenec.tech/v1');
  const [rewrite, setRewrite] = useState(data.rewrite_title ?? false);
  const save = useMutation({
    mutationFn: (remove = false) => request(`/integrations/${name}`, { method: 'PATCH', body: JSON.stringify({
      enabled: remove ? false : enabled, model, base_url: endpoint,
      ...(name === 'context' ? { rewrite_title: rewrite } : {}),
      ...(remove ? { api_key: null } : key ? { api_key: key } : {}),
    }) }),
    onSuccess: async (_, remove) => {
      setKey(''); if (remove) setEnabled(false);
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['jev-ranking'] });
    },
  });
  return <Paper withBorder p="md" radius="md"><Stack gap="sm">
    <Group justify="space-between"><Title order={4}>{title}</Title><Switch label="Включить" checked={enabled} onChange={(event) => setEnabled(event.currentTarget.checked)} /></Group>
    <TextInput label="Адрес API" placeholder="https://provider.example/v1" value={endpoint} onChange={(event) => setEndpoint(event.currentTarget.value)} maxLength={2000} />
    <PasswordInput label="API-ключ" placeholder={data.configured?.api_key ? 'Ключ сохранён' : 'API-ключ провайдера'} value={key} onChange={(event) => setKey(event.currentTarget.value)} autoComplete="new-password" />
    <ModelPicker path={`/integrations/agents/${name}/models`} endpoint={endpoint} apiKey={key} configured={data.configured?.api_key} value={model} onChange={setModel} />
    {name === 'context' && <Switch label="Менять названия задач" checked={rewrite} onChange={(event) => setRewrite(event.currentTarget.checked)} />}
    <Group><Button loading={save.isPending} disabled={!model.trim() || !endpoint.trim()} onClick={() => save.mutate(false)}>Сохранить</Button><Button variant="subtle" color="red" disabled={save.isPending} onClick={() => save.mutate(true)}>Удалить ключ</Button></Group>
    {save.error && <Alert color="red">{save.error.message}</Alert>}
    {save.isSuccess && <Text size="xs" c="green">Сохранено</Text>}
    {data.last_error && <Alert color="yellow">{data.last_error}</Alert>}
  </Stack></Paper>;
}
