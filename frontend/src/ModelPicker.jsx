import { useEffect, useRef, useState } from 'react';
import { Button, Group, Select, Stack, TextInput } from '@mantine/core';
import { request } from './api';

export default function ModelPicker({ path, endpoint, apiKey, configured, value, onChange }) {
  const [items, setItems] = useState([]);
  const [manual, setManual] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(null);
  useEffect(() => {
    setItems([]); setLoaded(false); setError(''); setLoading(false);
    return () => active.current?.abort();
  }, [path, endpoint, apiKey]);

  async function load() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true); setError('');
    try {
      const result = await request(path, { method: 'POST', signal: controller.signal,
        body: JSON.stringify({ base_url: endpoint.trim(), api_key: apiKey.trim() }) });
      if (controller.signal.aborted) return;
      setItems([...new Set(result.filter((item) => typeof item === 'string' && item.trim()))]);
      setLoaded(true);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  const canLoad = Boolean(endpoint.trim() && (apiKey.trim() || configured));
  const options = [...new Set([value, ...items].filter(Boolean))];
  return <Stack gap={4}>
    {manual ? <TextInput label="ID модели" value={value} onChange={(event) => onChange(event.currentTarget.value)} error={error} />
      : <Select label="Модель" searchable data={options} value={value || null} allowDeselect={false}
        onChange={(model) => { if (model) onChange(model); }} onDropdownOpen={() => { if (canLoad && !loaded && !loading) load(); }}
        nothingFoundMessage={loading ? 'Загрузка…' : 'Моделей нет — введите ID вручную'} error={error}
        filter={({ options: all, search }) => search === value ? all : all.filter((item) => item.label.toLowerCase().includes(search.toLowerCase()))} />}
    <Group gap="xs">
      <Button size="compact-xs" variant="subtle" loading={loading} disabled={!canLoad} onClick={load}>Обновить модели</Button>
      <Button size="compact-xs" variant="subtle" onClick={() => setManual(!manual)}>{manual ? 'Выбрать из списка' : 'Ввести ID'}</Button>
    </Group>
  </Stack>;
}
