import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/input';
import { Combobox } from '@/components/ui/select';

export default function ModelPicker({
  path,
  endpoint = '',
  apiKey = '',
  configured,
  providerConfigured = configured,
  value,
  onChange,
  onModelsLoaded,
  chatgpt = false,
  authMode = chatgpt ? 'oauth' : 'api_key',
  oauthAuthorized = true,
}) {
  const [items, setItems] = useState([]);
  const [manual, setManual] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(null);
  const isOauth = authMode === 'oauth';

  useEffect(() => {
    setItems([]);
    setLoaded(false);
    setError('');
    setLoading(false);
    return () => active.current?.abort();
  }, [path, authMode, endpoint, apiKey]);

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
        body: JSON.stringify(
          isOauth
            ? { auth_mode: 'oauth' }
            : {
                auth_mode: 'api_key',
                ...(endpoint.trim() ? { base_url: endpoint.trim() } : {}),
                ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
              },
        ),
      });
      if (controller.signal.aborted) return;
      setItems([...new Set(result.filter((item) => typeof item === 'string' && item.trim()))]);
      setLoaded(true);
      onModelsLoaded?.();
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  const canLoad = isOauth ? Boolean(oauthAuthorized) : Boolean(providerConfigured || (endpoint.trim() && apiKey.trim()));
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
            clearable={isOauth}
            allowFreeText
            placeholder={isOauth ? 'По умолчанию' : 'Выберите модель…'}
            searchPlaceholder="Поиск модели…"
            emptyText={loading ? 'Загрузка…' : 'Моделей нет — введите ID вручную'}
            onOpen={() => {
              if (canLoad && !loaded && !loading) load();
            }}
            onChange={(model) => {
              if (isOauth || model) onChange(model || '');
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
