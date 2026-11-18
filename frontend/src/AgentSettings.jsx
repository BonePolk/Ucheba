import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import ModelPicker from './ModelPicker';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/controls';
import { Field, Input, PasswordField } from '@/components/ui/input';
import { Notice, SectionHeader, StatusIndicator } from '@/components/ui/surface';

export default function AgentSettings({ name, title, data }) {
  const client = useQueryClient();
  const [enabled, setEnabled] = useState(data.enabled);
  const [model, setModel] = useState(data.model || 'gpt-6-luna');
  const [key, setKey] = useState('');
  const [endpoint, setEndpoint] = useState(data.base_url || 'https://lenec.tech/v1');
  const [rewrite, setRewrite] = useState(data.rewrite_title ?? false);

  useEffect(() => {
    setEnabled(data.enabled);
  }, [data.enabled]);

  const toggle = useMutation({
    mutationFn: (value) => request(`/integrations/${name}`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled: value }),
    }),
    onSuccess: async (_, value) => {
      setEnabled(value);
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
  });

  const save = useMutation({
    mutationFn: (remove = false) => request(`/integrations/${name}`, {
      method: 'PATCH',
      body: JSON.stringify({
        enabled: remove ? false : enabled,
        model,
        base_url: endpoint,
        ...(name === 'monocle' ? { rewrite_title: rewrite } : {}),
        ...(remove ? { api_key: null } : key ? { api_key: key } : {}),
      }),
    }),
    onSuccess: async (_, remove) => {
      setKey('');
      if (remove) setEnabled(false);
      await client.invalidateQueries({ queryKey: ['integrations'], exact: true });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
  });

  const checkAmeba = useMutation({
    mutationFn: () => request('/integrations/ameba/check', { method: 'POST' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['integrations'], exact: true }),
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#222222] pb-2.5">
        <SectionHeader title={title} />
        <div className="flex items-center gap-2">
          <StatusIndicator tone={enabled ? 'success' : 'idle'} label={enabled ? 'Включён' : 'Отключён'} />
          <Switch
            aria-label={`Включить ${title}`}
            checked={enabled}
            disabled={toggle.isPending || save.isPending}
            onCheckedChange={(checked) => toggle.mutate(checked)}
          />
        </div>
      </div>

      <Field label="Адрес API">
        <Input
          placeholder="https://provider.example/v1"
          value={endpoint}
          maxLength={2000}
          onChange={(event) => setEndpoint(event.currentTarget.value)}
        />
      </Field>

      <Field label="API-ключ">
        <PasswordField
          placeholder={data.configured?.api_key ? 'Сохранён' : 'API-ключ'}
          value={key}
          autoComplete="new-password"
          onChange={(event) => setKey(event.currentTarget.value)}
        />
      </Field>

      <ModelPicker
        path={`/integrations/agents/${name}/models`}
        endpoint={endpoint}
        apiKey={key}
        configured={data.configured?.api_key}
        value={model}
        onChange={setModel}
      />

      {name === 'monocle' && (
        <div className="border-t border-[#222222] pt-2">
          <Switch
            label="Разрешить Monocle переписывать названия задач"
            checked={rewrite}
            onCheckedChange={setRewrite}
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          variant="primary"
          loading={save.isPending}
          disabled={toggle.isPending || !model.trim() || !endpoint.trim()}
          onClick={() => save.mutate(false)}
        >
          Сохранить
        </Button>
        {name === 'ameba' && (
          <Button
            variant="default"
            loading={checkAmeba.isPending}
            disabled={save.isPending || !data.configured?.api_key}
            onClick={() => checkAmeba.mutate()}
          >
            Проверить
          </Button>
        )}
        <Button
          variant="danger-ghost"
          disabled={toggle.isPending || save.isPending}
          onClick={() => save.mutate(true)}
        >
          Удалить ключ
        </Button>
      </div>

      {(save.error || toggle.error || checkAmeba.error) && (
        <Notice tone="error">{(save.error || toggle.error || checkAmeba.error).message}</Notice>
      )}
      {save.isSuccess && <Notice tone="success">Сохранено.</Notice>}
      {checkAmeba.isSuccess && <Notice tone="success">Подключено.</Notice>}
      {data.last_error && <Notice tone="warning" title="Ошибка">{data.last_error}</Notice>}
      {data.last_success && (
        <div className="text-[11px] text-[#929292]">
          Успешно: {new Date(data.last_success).toLocaleString()}
        </div>
      )}
    </div>
  );
}
