import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import * as PopoverPrimitive from '@radix-ui/react-popover';
import { useState } from 'react';
import { Badge } from '@/components/ui/surface';
import { CollapseSection } from '@/lib/motion';

export function DeadlineRisk({ reason }) {
  if (!reason) return null;
  return (
    <PopoverPrimitive.Root>
      <PopoverPrimitive.Trigger asChild>
        <button
          type="button"
          title={`Риск срока: ${reason}`}
          aria-label={`Риск срока: ${reason}`}
          onClick={(event) => event.stopPropagation()}
          className="inline-flex items-center justify-center align-middle ml-1 text-[#D4A24C] cursor-pointer max-md:p-1 max-md:-m-1"
        >
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          sideOffset={4}
          className="z-50 max-w-[280px] rounded-none bg-[#191919] border border-[#4A391D] px-2.5 py-2 text-[12px] leading-snug text-[#EAEAEA] whitespace-pre-wrap break-words"
        >
          Риск срока: {reason}
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}

export default function RankingAssessment({ assessment, compact = false }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  if (!assessment) return null;
  const plan = assessment.plan;
  const time = plan?.hours_label || (plan ? `${Math.round((plan.min_minutes / 60) * 10) / 10}–${Math.round((plan.max_minutes / 60) * 10) / 10} ч` : null);
  const timeText = assessment.time_estimate && assessment.time_estimate !== time
    ? `${time ? `${time} · ` : ''}${assessment.time_estimate}`
    : (time || assessment.time_estimate);

  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      {!compact && (
        <div className="flex flex-col gap-1.5 text-[12px]">
          <div className="flex flex-wrap items-center gap-1.5">
            {timeText && <Badge variant="accent">{timeText}</Badge>}
            {plan?.completed_work_minutes > 0 && (
              <Badge variant="outline">Сделано {plan.completed_work_minutes} мин</Badge>
            )}
          </div>
          {assessment.reason && (
            <p className="text-[#EAEAEA] leading-snug m-0 break-words">{assessment.reason}</p>
          )}
          {(assessment.effort || assessment.impact) && (
            <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[12px] text-[#929292]">
              {assessment.effort && (
                <span>Сложность: <span className="text-[#DCDCDC]">{assessment.effort}</span></span>
              )}
              {assessment.impact && (
                <span>Эффект: <span className="text-[#DCDCDC]">{assessment.impact}</span></span>
              )}
            </div>
          )}
        </div>
      )}

      {compact && assessment.reason && (
        <p className="text-[12px] text-[#B8B8B8] leading-snug m-0 break-words">{assessment.reason}</p>
      )}

      {!compact && plan?.steps?.length > 0 && (
        <div className="flex flex-col gap-1">
          <button
            type="button"
            onClick={() => setStepsOpen(!stepsOpen)}
            aria-expanded={stepsOpen}
            className="inline-flex items-center gap-1 text-[12px] text-[#929292] hover:text-[#EAEAEA] w-fit cursor-pointer"
          >
            {stepsOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            <span>Этапы ({plan.steps.length})</span>
          </button>
          <CollapseSection open={stepsOpen}>
            <div className="border-l border-[#303030] pl-2.5 flex flex-col gap-1 text-[12px] pt-1">
              {plan.steps.map((step, index) => (
                <div key={index} className="text-[#DCDCDC]">
                  {index + 1}. {step.title} · <span className="text-[#929292] tabular-nums">{step.min_minutes}–{step.max_minutes} м{step.wait_minutes > 0 ? ` (+${step.wait_minutes}м)` : ''}</span>
                </div>
              ))}
              {plan.assumptions?.map((text, index) => (
                <div key={index} className="text-[11px] text-[#929292]">• {text}</div>
              ))}
            </div>
          </CollapseSection>
        </div>
      )}
    </div>
  );
}
