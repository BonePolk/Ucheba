import { Badge, Notice } from '@/components/ui/surface';
import { cn } from '@/lib/utils';

function providerLabel(item) {
  if (item.provider === 'arrodes') return 'Arrodes';
  if (item.provider === 'context') return item.task_id ? 'Monocle' : 'Straus';
  return 'Ameba';
}

export default function AgentQueue({ queue, error, compact = false }) {
  if (error) {
    return (
      <Notice tone="warning" className={compact ? 'py-1 px-2 text-[11px]' : undefined}>
        Очередь временно недоступна
      </Notice>
    );
  }
  if (!queue?.busy && !queue?.active?.length && !queue?.waiting?.length) return null;

  const active = queue.active ?? [];
  const waiting = queue.waiting ?? [];

  if (compact) {
    const first = active[0];
    return (
      <div className="inline-flex items-center gap-1.5 border border-[#303030] bg-[#111111] px-2 h-7 text-[12px] max-w-full min-w-0">
        <span className="h-1.5 w-1.5 bg-[#9E6FD0] animate-pulse shrink-0" aria-hidden="true" />
        {first ? (
          <span className="truncate max-w-[170px] sm:max-w-[240px]">
            <strong className="font-medium text-[#CFA9F5]">{providerLabel(first)}</strong>
            {': '}
            {first.task_id ? (
              <a href={`/?task=${first.task_id}&browser=1`} className="hover:underline">
                {first.title}
              </a>
            ) : (
              first.title
            )}
          </span>
        ) : (
          <span className="text-[#929292]">Очередь агентов</span>
        )}
        {waiting.length > 0 && (
          <Badge variant="outline" className="h-4 px-1 text-[10px] tabular-nums">
            +{waiting.length}
          </Badge>
        )}
      </div>
    );
  }

  return (
    <div className="border border-[#303030] bg-[#111111] px-3 py-2 flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-semibold text-[#EAEAEA]">Очередь агентов</span>
        <Badge variant="outline" className="tabular-nums">{waiting.length} ожидают</Badge>
      </div>
      {active.map((item, index) => (
        <div key={`${item.provider}-${item.task_id}-${index}`} className="flex items-center gap-2 text-[12px] min-w-0">
          <Badge variant="accent">{providerLabel(item)} · работает</Badge>
          {item.task_id ? (
            <a href={`/?task=${item.task_id}&browser=1`} className="truncate text-[#EAEAEA] hover:underline">
              {item.title} ↗
            </a>
          ) : (
            <span className="truncate text-[#EAEAEA]">{item.title}</span>
          )}
        </div>
      ))}
      {waiting.map((item, index) => (
        <div key={`${item.provider}-${item.task_id}-${index}`} className={cn('flex items-center gap-2 text-[12px] text-[#929292] min-w-0')}>
          <span className="tabular-nums">{index + 1}.</span>
          <span className="shrink-0">{providerLabel(item)}</span>
          {item.task_id ? (
            <a href={`/?task=${item.task_id}&browser=1`} className="truncate hover:text-[#EAEAEA] hover:underline">
              {item.title} ↗
            </a>
          ) : (
            <span className="truncate">{item.title}</span>
          )}
        </div>
      ))}
      {!active.length && !waiting.length && (
        <div className="text-[12px] text-[#929292]">Ожидаем освобождения агентов</div>
      )}
    </div>
  );
}
