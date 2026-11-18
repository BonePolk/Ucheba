import { useQuery } from '@tanstack/react-query';
import { request } from './api';
import AgentQueue from './AgentQueue';
import ArrodesWorkspace from './ArrodesWorkspace';
import { EmptyState, PageHeader } from '@/components/ui/surface';

export default function AgentsHub({ features = {}, search = '' }) {
  const params = new URLSearchParams(search);
  const requestedTask = params.get('task');

  const queue = useQuery({
    queryKey: ['agent-queue'],
    queryFn: () => request('/integrations/queue'),
    refetchInterval: 2000,
  });

  if (features.arrodes === false) {
    return <EmptyState title="Arrodes отключён" />;
  }

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        title="Arrodes"
        actions={<AgentQueue queue={queue.data} error={queue.error} compact />}
      />
      <ArrodesWorkspace features={features} initialTaskId={requestedTask} />
    </div>
  );
}
