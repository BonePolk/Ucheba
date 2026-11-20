import Markdown from 'react-markdown';
import { buildMathComponents, extractMarkdownMath } from './MathFormula';

export default function TaskMarkdown({ children }) {
  const raw = typeof children === 'string' ? children : '';
  const { text, formulas } = extractMarkdownMath(raw);
  const components = buildMathComponents(formulas);
  return (
    <div className="task-markdown">
      <Markdown skipHtml components={components}>
        {text}
      </Markdown>
    </div>
  );
}
