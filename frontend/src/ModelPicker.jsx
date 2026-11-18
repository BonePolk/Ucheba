import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/input';
import { Combobox } from '@/components/ui/select';

export default function ModelPicker({ path, endpoint = '', apiKey = '', configured, value, onChange, chatgpt = false }) {
  const [items, setItems] = useState([]);
  const [manual, setManual] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(null);

  useEffect(() => {
    setItems([]);
    setLoaded(false);
    setError('');
    setLoading(false);
    return () => active.current?.abort();
  }, [path, endpoint, apiKey]);

  async function load() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError('');
    try {
      const result = await request(path, {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify(chatgpt ? {} : { base_url: endpoint.trim(), api_key: apiKey.trim() }),
      });
      if (controller.signal.aborted) return;
      setItems([...new Set(result.filter((item) => typeof item === 'string' && item.trim()))]);
      setLoaded(true);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  const canLoad = chatgpt || Boolean(endpoint.trim() && (apiKey.trim() || configured));
  const options = [...new Set([value, ...items].filter(Boolean))];

  return (
    <div className="flex flex-col gap-1.5">
      {manual ? (
        <Field label="ID модели" error={error}>
          <Input value={value} error={Boolean(error)} onChange={(event) => onChange(event.currentTarget.value)} />
        </Field>
      ) : (
        <Field label="Модель" error={error}>
          <Combobox
            value={value || null}
            options={options}
            clearable={chatgpt}
            allowFreeText
            placeholder={chatgpt ? 'По умолчанию' : 'Выберите модель…'}
            searchPlaceholder="Поиск модели…"
            emptyText={loading ? 'Загрузка…' : 'Моделей нет — введите ID вручную'}
            onOpen={() => {
              if (canLoad && !loaded && !loading) load();
            }}
            onChange={(model) => {
              if (chatgpt || model) onChange(model || '');
            }}
          />
        </Field>
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="xs" variant="subtle" leftIcon={<RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />} loading={loading} disabled={!canLoad} onClick={load}>
          Обновить модели
        </Button>
        <Button size="xs" variant="subtle" onClick={() => setManual(!manual)}>
          {manual ? 'Выбрать из списка' : 'Ввести ID вручную'}
        </Button>
      </div>
    </div>
  );
}
