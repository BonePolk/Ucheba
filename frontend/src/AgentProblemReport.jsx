import { Button } from '@/components/ui/button';
import { useMutation } from '@tanstack/react-query';
import { request } from './api';

export default function AgentProblemReport({ agent, toolName }) {
  const report = useMutation({
    mutationFn: () => request('/auth/agent-issues', {
      method: 'POST',
      body: JSON.stringify({ agent, tool_name: toolName, category: 'other' }),
    }),
  });
  return (
    <div className="flex items-center gap-2 flex-wrap mt-1">
      <Button
        size="xs"
        variant="subtle"
        loading={report.isPending}
        disabled={report.isSuccess}
        onClick={() => report.mutate()}
      >
        {report.isSuccess ? 'Администратору сообщено' : 'Сообщить администратору'}
      </Button>
      {report.error && <span className="text-[11px] text-[#D46A6A]">{report.error.message}</span>}
    </div>
  );
}
