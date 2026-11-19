import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field } from '@/components/ui/input';
import { Combobox } from '@/components/ui/select';
import { Notice, SectionHeader } from '@/components/ui/surface';

export default function ProfileTimezone() {
  const client = useQueryClient();
  const [draft, setDraft] = useState(null);
  const current = useQuery({ queryKey: ['calendar-timezone'], queryFn: () => request('/calendar/timezone') });
  const zones = [...new Set(['UTC', ...(Intl.supportedValuesOf?.('timeZone') ?? [])])];

  const save = useMutation({
    mutationFn: () => request('/calendar/timezone', {
      method: 'PATCH',
      body: JSON.stringify({ timezone: draft.trim() }),
    }),
    onSuccess: async (result) => {
      client.setQueryData(['calendar-timezone'], result);
      setDraft(null);
      await client.invalidateQueries({ queryKey: ['calendar'] });
      await client.invalidateQueries({ queryKey: ['ameba-ranking'] });
    },
  });

  return (
    <section className="border border-[#303030] bg-[#111111] p-3.5 flex flex-col gap-2.5">
      <SectionHeader title="Часовой пояс" />
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Пояс для расписания и сроков" className="w-full min-w-0 sm:flex-1 sm:min-w-[200px]">
          <Combobox
            placeholder="Europe/Moscow"
            searchPlaceholder="Поиск часового пояса…"
            allowFreeText
            options={zones}
            value={draft ?? current.data?.timezone ?? ''}
            onChange={(val) => setDraft(val ?? '')}
            disabled={current.isPending || save.isPending}
          />
        </Field>
        <Button
          variant="primary"
          className="max-sm:w-full"
          disabled={draft === null || !draft.trim() || current.isError}
          loading={save.isPending}
          onClick={() => save.mutate()}
        >
          Сохранить
        </Button>
      </div>
      {(current.error || save.error) && <Notice tone="error">{(current.error || save.error).message}</Notice>}
    </section>
  );
}
