import { useState } from 'react';
import { Alert, Autocomplete, Button, Group, Paper, PasswordInput, Stack, Switch, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';

export default function AgentSettings({ name, title, description, data }) {
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(data.enabled);
  const [model, setModel] = useState(data.model || 'gpt-6-luna');
  const [key, setKey] = useState('');
  const [endpoint, setEndpoint] = useState(data.base_url || 'https://lenec.tech/v1');
  const [rewrite, setRewrite] = useState(data.rewrite_title ?? false);
  const models = useQuery({ queryKey: ['agent-models', name, endpoint], queryFn: () => request(`/integrations/agents/${name}/models`, { method: 'POST', body: JSON.stringify({ base_url: endpoint, api_key: key }) }), enabled: false, retry: false });
  const save = useMutation({
    mutationFn: (remove = false) => request(`/integrations/${name}`, { method: 'PATCH', body: JSON.stringify({
      enabled: remove ? false : enabled, model, base_url: endpoint,
      ...(name === 'context' ? { rewrite_title: rewrite } : {}),
      ...(remove ? { api_key: null } : key ? { api_key: key } : {}),
    }) }),
    onSuccess: async (_, remove) => {
      setKey(''); if (remove) setEnabled(false);
      client.invalidateQueries({ queryKey: ['agent-models', name] });
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['jev-ranking'] });
    },
  });
  return <Paper withBorder p="lg" radius="md"><Stack gap="sm">
    <Title order={3}>{title}</Title><Text size="xs" c="dimmed">{description}</Text>
    <Switch label="Включён" checked={enabled} onChange={(event) => setEnabled(event.currentTarget.checked)} />
    <TextInput label="Endpoint провайдера" description="Базовый URL API, доступный из контейнера, например https://provider.example/v1" value={endpoint} onChange={(event) => setEndpoint(event.currentTarget.value)} maxLength={2000} />
    <PasswordInput label="Личный API-ключ" placeholder={data.configured?.api_key ? 'Сохранён; пустое поле оставит прежний' : 'API-ключ провайдера'} value={key} onChange={(event) => setKey(event.currentTarget.value)} autoComplete="new-password" />
    <Autocomplete label="Модель" value={model} onChange={setModel} data={[...new Set([data.model, ...(models.data ?? [])].filter(Boolean))]} placeholder="ID модели шлюза" />
    <Button size="compact-xs" variant="subtle" loading={models.isFetching} disabled={(!key && !data.configured?.api_key) || !endpoint.trim() || save.isPending} onClick={() => models.refetch()}>Загрузить доступные модели</Button>
    <Text size="xs" c="dimmed">Можно ввести ID вручную. Нужна поддержка Responses API и инструментов. Список можно получить с введённым ключом и endpoint до сохранения настроек.</Text>
    {models.isSuccess && !models.data?.length && <Text size="xs" c="dimmed">Провайдер не вернул модели. ID можно указать вручную.</Text>}
    {name === 'context' && <Switch label="Разрешить менять название задачи" checked={rewrite} onChange={(event) => setRewrite(event.currentTarget.checked)} />}
    <Group><Button loading={save.isPending} disabled={!model.trim()} onClick={() => save.mutate(false)}>Сохранить</Button><Button variant="subtle" color="red" disabled={save.isPending} onClick={() => save.mutate(true)}>Удалить ключ</Button></Group>
    {(save.error || models.error) && <Alert color="red">{save.error?.message || models.error?.message}</Alert>}
    {save.isSuccess && <Text size="xs" c="green">Сохранено</Text>}
    {data.last_error && <Alert color="yellow">{data.last_error}</Alert>}
  </Stack></Paper>;
}
