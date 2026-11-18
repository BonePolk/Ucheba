import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { Badge } from '@/components/ui/surface';
import { HintTooltip } from '@/components/ui/tooltip';
import { CollapseSection } from '@/lib/motion';

export function DeadlineRisk({ reason }) {
  if (!reason) return null;
  return (
    <HintTooltip label={`Риск срока: ${reason}`}>
      <span
        tabIndex={0}
        aria-label={`Риск срока: ${reason}`}
        className="inline-flex items-center align-middle ml-1 text-[#D4A24C] cursor-help"
      >
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
      </span>
    </HintTooltip>
  );
}

export default function RankingAssessment({ assessment, compact = false }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  if (!assessment) return null;
  const plan = assessment.plan;
  const time = plan?.hours_label || (plan ? `${Math.round((plan.min_minutes / 60) * 10) / 10}–${Math.round((plan.max_minutes / 60) * 10) / 10} ч` : 'Время');

  return (
    <div className="flex flex-col gap-1.5 max-w-md min-w-0">
      {!compact && (
        <div className="flex flex-wrap items-center gap-1.5">
          {assessment.time_estimate && (
            <HintTooltip label={assessment.time_estimate}>
              <Badge variant="accent" className="cursor-help">{time}</Badge>
            </HintTooltip>
          )}
          {assessment.effort && (
            <HintTooltip label={assessment.effort}>
              <Badge variant="default" className="cursor-help">Силы</Badge>
            </HintTooltip>
          )}
          {assessment.impact && (
            <HintTooltip label={assessment.impact}>
              <Badge variant="default" className="cursor-help">Эффект</Badge>
            </HintTooltip>
          )}
          {assessment.reason && (
            <HintTooltip label={assessment.reason}>
              <button
                type="button"
                className="text-[11px] text-[#929292] hover:text-[#EAEAEA] cursor-help"
              >
                Причина
              </button>
            </HintTooltip>
          )}
        </div>
      )}

      {compact && assessment.reason && (
        <HintTooltip label={assessment.reason}>
          <button
            type="button"
            className="text-left text-[11px] text-[#929292] hover:text-[#EAEAEA] cursor-help w-fit"
          >
            Причина
          </button>
        </HintTooltip>
      )}

      {!compact && plan && (
        <div className="flex flex-col gap-1">
          <button
            type="button"
            onClick={() => setStepsOpen(!stepsOpen)}
            aria-expanded={stepsOpen}
            className="inline-flex items-center gap-1 text-[11px] text-[#929292] hover:text-[#EAEAEA] w-fit cursor-pointer"
          >
            {stepsOpen ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            <span>Этапы ({plan.steps?.length ?? 0})</span>
          </button>
          <CollapseSection open={stepsOpen}>
            <div className="border-l border-[#303030] pl-2.5 flex flex-col gap-1 text-[12px] pt-1">
              {assessment.time_estimate && <div className="text-[#929292]">Время: <span className="text-[#EAEAEA]">{assessment.time_estimate}</span></div>}
              {assessment.effort && <div className="text-[#929292]">Силы: <span className="text-[#EAEAEA]">{assessment.effort}</span></div>}
              {assessment.impact && <div className="text-[#929292]">Эффект: <span className="text-[#EAEAEA]">{assessment.impact}</span></div>}
              {plan.steps?.map((step, index) => (
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
