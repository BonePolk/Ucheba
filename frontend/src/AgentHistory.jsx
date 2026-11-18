import { Fragment, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { request, agentName } from './api';
import { Button } from '@/components/ui/button';
import { Badge, EmptyState, Notice, PageLoader, SectionHeader } from '@/components/ui/surface';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, TableContainer } from '@/components/ui/table';
import { FAST_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

const statuses = {
  running: { label: 'В работе', variant: 'accent' },
  completed: { label: 'Завершено', variant: 'success' },
  interrupted: { label: 'Прервано', variant: 'warning' },
};

export default function AgentHistory() {
  const reduced = useReducedMotion();
  const [before, setBefore] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const history = useQuery({
    queryKey: ['agent-history', before],
    queryFn: () => request(`/auth/agent-history${before ? `?before=${before}` : ''}`),
    refetchInterval: 10000,
  });
  const items = history.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionHeader title="История агентов" />
        <Button
          size="sm"
          variant="default"
          leftIcon={<RefreshCw className={`h-3 w-3 ${history.isFetching ? 'animate-spin' : ''}`} />}
          onClick={() => {
            setBefore(null);
            history.refetch();
          }}
        >
          Обновить
        </Button>
      </div>

      {history.error && <Notice tone="error">{history.error.message}</Notice>}

      {history.isPending ? (
        <PageLoader />
      ) : items.length === 0 ? (
        <EmptyState title="История пуста" />
      ) : (
        <TableContainer>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead className="w-28">Агент</TableHead>
                <TableHead>Поручение</TableHead>
                <TableHead className="w-40">Дата</TableHead>
                <TableHead className="w-28">Статус</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((item) => {
                const open = expandedId === item.id;
                const st = statuses[item.status] ?? { label: item.status, variant: 'default' };
                const reqTitle = item.request.instruction
                  || item.request.task?.title
                  || item.request.subject
                  || (item.request.task_id ? `Задача №${item.request.task_id}` : 'Сортировка задач');
                const summary = item.result.reason || item.result.explanation || item.result.summary;

                return (
                  <Fragment key={item.id}>
                    <TableRow
                      className={`cursor-pointer ${open ? 'bg-[#17141D]' : ''}`}
                      onClick={() => setExpandedId(open ? null : item.id)}
                    >
                      <TableCell className="text-[#929292]">
                        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                      </TableCell>
                      <TableCell className="font-medium text-[#EAEAEA]">{agentName(item.agent)}</TableCell>
                      <TableCell className="max-w-[460px] truncate text-[#DCDCDC]">{reqTitle}</TableCell>
                      <TableCell className="text-[12px] text-[#929292] tabular-nums whitespace-nowrap">
                        {new Date(item.created_at).toLocaleString()}
                      </TableCell>
                      <TableCell>
                        <Badge variant={st.variant}>{st.label}</Badge>
                      </TableCell>
                    </TableRow>
                    {open && (
                      <TableRow className="bg-[#0D0D0D] hover:bg-[#0D0D0D]">
                        <TableCell colSpan={5} className="p-3">
                          <motion.div
                            initial={reduced ? false : { opacity: 0, y: -4 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={reduced ? { duration: 0.01 } : FAST_TRANSITION}
                            className="flex flex-col gap-2"
                          >
                            {summary && (
                              <div className="text-[12px] text-[#EAEAEA] whitespace-pre-wrap break-words">{summary}</div>
                            )}
                            {item.request.task_id && (
                              <a href={`/?task=${item.request.task_id}`} className="text-[12px] text-[#CFA9F5] hover:underline w-fit">
                                Задача #{item.request.task_id} →
                              </a>
                            )}
                            <details className="text-[12px]">
                              <summary className="cursor-pointer text-[#929292] hover:text-[#EAEAEA]">JSON</summary>
                              <pre className="mt-1.5 p-2.5 bg-[#090909] border border-[#242424] font-mono text-[11px] whitespace-pre-wrap break-words overflow-auto max-h-[260px]">
                                {JSON.stringify({ request: item.request, result: item.result }, null, 2)}
                              </pre>
                            </details>
                          </motion.div>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <div className="flex items-center gap-2">
        {before && <Button size="sm" variant="default" onClick={() => setBefore(null)}>Последние</Button>}
        {items.length === 20 && (
          <Button size="sm" variant="default" onClick={() => setBefore(items.at(-1).id)}>Раньше</Button>
        )}
      </div>
    </div>
  );
}
