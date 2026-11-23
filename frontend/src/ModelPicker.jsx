import { useEffect, useMemo, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field, Input } from '@/components/ui/input';
import { Combobox } from '@/components/ui/select';
import { formatSafeDateTime, getModelAvailability } from '@/lib/antigravity';

function normalizeModelOption(item) {
  if (typeof item === 'string') {
    const clean = item.trim();
    return clean ? { value: clean, label: clean, meta: null } : null;
  }
  if (item && typeof item === 'object' && typeof item.id === 'string' && item.id.trim()) {
    const id = item.id.trim();
    const parts = [item.name && item.name !== id ? `${item.name} (${id})` : id];
    if (item.remaining_percent != null) parts.push(`${item.remaining_percent}%`);
    if (item.available === false) parts.push('недоступна');
    return { value: id, label: parts.join(' · '), meta: item };
  }
  return null;
}

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
  antigravityAuthorized = false,
  antigravityConfigured = false,
  gatewayUrl = '',
  connectionId = '',
  configVersion = '',
}) {
  const [items, setItems] = useState([]);
  const [manual, setManual] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const active = useRef(null);
  const isOauth = authMode === 'oauth';
  const isAntigravity = authMode === 'antigravity' || authMode === 'antigravity_gateway';
  const antigravityReady = Boolean(antigravityAuthorized || antigravityConfigured);

  useEffect(() => {
    setItems([]);
    setLoaded(false);
    setError('');
    setLoading(false);
    return () => active.current?.abort();
  }, [path, authMode, endpoint, apiKey, gatewayUrl, connectionId, configVersion]);

  async function load() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    setError('');
    try {
      const body = isOauth
        ? { auth_mode: 'oauth' }
        : isAntigravity
          ? { auth_mode: 'antigravity', detailed: true }
          : {
              auth_mode: 'api_key',
              ...(endpoint.trim() ? { base_url: endpoint.trim() } : {}),
              ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
            };
      const result = await request(path, {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify(body),
      });
      if (controller.signal.aborted) return;
      const seen = new Set();
      const normalized = [];
      for (const entry of Array.isArray(result) ? result : []) {
        const opt = normalizeModelOption(entry);
        if (!opt || seen.has(opt.value)) continue;
        seen.add(opt.value);
        normalized.push(opt);
      }
      setItems(normalized);
      setLoaded(true);
      onModelsLoaded?.();
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure.message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }

  const canLoad = isOauth
    ? Boolean(oauthAuthorized)
    : isAntigravity
      ? antigravityReady
      : Boolean(providerConfigured || (endpoint.trim() && apiKey.trim()));

  useEffect(() => {
    if (isAntigravity && canLoad && !loaded && !loading && !error) {
      load();
    }
  }, [isAntigravity, canLoad, loaded, error, path, gatewayUrl, connectionId, configVersion]);

  const options = useMemo(() => {
    if (!value) return items;
    if (items.some((opt) => opt.value === value)) return items;
    return [{ value, label: value, meta: null }, ...items];
  }, [items, value]);

  const rawCatalogModels = useMemo(() => items.map((opt) => opt.meta).filter(Boolean), [items]);
  const modelAvailability = useMemo(
    () => (isAntigravity ? getModelAvailability(rawCatalogModels, value, { catalogLoaded: loaded }) : null),
    [isAntigravity, rawCatalogModels, value, loaded],
  );
  const selectedMeta = modelAvailability?.meta ?? null;
  const resetText = selectedMeta?.reset_time ? formatSafeDateTime(selectedMeta.reset_time) : null;

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
      {isAntigravity && selectedMeta && (
        <div className="text-[11px] text-[#929292] flex flex-wrap gap-x-3 gap-y-0.5">
          <span>{selectedMeta.available === false ? 'Статус: недоступна' : 'Статус: доступна'}</span>
          <span>
            {selectedMeta.remaining_percent != null
              ? `Квота: ${selectedMeta.remaining_percent}%`
              : 'Квота неизвестна'}
          </span>
          {resetText && (
            <span>Сброс: {resetText}</span>
          )}
        </div>
      )}
      {isAntigravity && modelAvailability?.warning && (
        <div className="text-[11px] text-[#E5B567]">
          {modelAvailability.warning}
        </div>
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
