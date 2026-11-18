import Markdown from 'react-markdown';

export default function TaskMarkdown({ children }) {
  return (
    <div className="task-markdown">
      <Markdown
        skipHtml
        components={{
          a: ({ href, children: linkChildren }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {linkChildren}
            </a>
          ),
          img: ({ src, alt }) => (
            <a href={src} target="_blank" rel="noreferrer">
              {alt || 'Открыть изображение'}
            </a>
          ),
        }}
      >
        {children}
      </Markdown>
    </div>
  );
}
