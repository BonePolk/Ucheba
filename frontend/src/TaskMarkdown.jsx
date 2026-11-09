import Markdown from 'react-markdown';
import { Anchor, TypographyStylesProvider } from '@mantine/core';

export default function TaskMarkdown({ children }) {
  return <TypographyStylesProvider className="task-markdown">
    <Markdown skipHtml components={{
      a: ({ href, children }) => <Anchor href={href} target="_blank" rel="noreferrer">{children}</Anchor>,
      img: ({ src, alt }) => <Anchor href={src} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</Anchor>,
    }}>{children}</Markdown>
  </TypographyStylesProvider>;
}
