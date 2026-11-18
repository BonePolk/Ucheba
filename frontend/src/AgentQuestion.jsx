import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { MessageSquareWarning } from 'lucide-react';
import { request } from './api';
import { Button } from '@/components/ui/button';
import { Field, Textarea } from '@/components/ui/input';
import { Notice } from '@/components/ui/surface';
import { cn } from '@/lib/utils';
import { FAST_TRANSITION, motion, useReducedMotion } from '@/lib/motion';

const services = {
  '\u0410\u0440\u0440\u043e\u0434\u0435\u0441': 'arrodes',
  Arrodes: 'arrodes',
  Codex: 'arrodes',
  Monocle: 'monocle',
  Lucheba: 'monocle',
  Straus: 'straus',
  Ameba: 'ameba',
};

export default function AgentQuestion({ question, agent }) {
  const reduced = useReducedMotion();
  const [text, setText] = useState('');
  const answer = useMutation({
    mutationFn: () => request(`/integrations/agents/${services[agent]}/questions/${question.id}/answer`, {
      method: 'POST',
      body: JSON.stringify({ text: text.trim() }),
    }),
  });

  return (
    <motion.div
      role="region"
      aria-label="Вопрос агента"
      initial={reduced ? false : { opacity: 0, y: 5 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduced ? { duration: 0.01 } : FAST_TRANSITION}
      className="border border-[#4A391D] border-l-2 border-l-[#D4A24C] bg-[#18130B] p-3 mt-2"
    >
      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim() && !answer.isPending && !answer.isSuccess) answer.mutate();
        }}
      >
        <div className="flex items-start gap-2">
          <MessageSquareWarning className="h-4 w-4 text-[#D4A24C] shrink-0 mt-0.5" aria-hidden="true" />
          <div className="text-[13px] font-medium text-[#EAEAEA] whitespace-pre-wrap break-words">
            {question.question}
          </div>
        </div>
        {question.options?.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {question.options.map((option, index) => (
              <button
                key={index}
                type="button"
                disabled={answer.isPending || answer.isSuccess}
                onClick={() => setText(option)}
                className={cn(
                  'px-2.5 py-1 text-[12px] text-left border transition-colors cursor-pointer whitespace-normal',
                  text === option
                    ? 'bg-[#7F49B4]/24 border-[#7F49B4] text-[#EAEAEA]'
                    : 'bg-[#111111] border-[#303030] text-[#D0D0D0] hover:bg-[#191919]',
                )}
              >
                {option}
              </button>
            ))}
          </div>
        )}
        <Field label="Ответ">
          <Textarea
            rows={2}
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
            maxLength={12000}
            disabled={answer.isPending || answer.isSuccess}
          />
        </Field>
        <div className="flex items-center gap-2">
          <Button
            type="submit"
            variant="primary"
            size="sm"
            loading={answer.isPending}
            disabled={!text.trim() || answer.isSuccess}
          >
            {answer.isSuccess ? 'Отправлено' : 'Ответить'}
          </Button>
        </div>
        {answer.error && <Notice tone="error">{answer.error.message}</Notice>}
      </form>
    </motion.div>
  );
}
