import katex from 'katex';
import 'katex/dist/katex.min.css';

const MATH_COMMAND_RE = /\\(?:frac|dfrac|tfrac|sqrt|pi|omega|Omega|alpha|beta|gamma|Gamma|delta|Delta|theta|Theta|lambda|Lambda|mu|nu|xi|rho|sigma|Sigma|tau|phi|varphi|Phi|psi|Psi|chi|eta|zeta|epsilon|varepsilon|kappa|sum|prod|int|iint|oint|lim|infty|pm|mp|cdot|times|div|approx|equiv|sim|simeq|propto|neq|ne|le|leq|ge|geq|ll|gg|to|rightarrow|leftarrow|leftrightarrow|Rightarrow|Leftarrow|Leftrightarrow|partial|nabla|vec|hat|bar|overline|underline|dot|ddot|mathrm|mathbf|mathit|mathbb|mathcal|text|left|right|sin|cos|tan|tg|ctg|arcsin|arccos|arctan|ln|log|lg|exp|max|min|sup|inf|binom)\b/;

function isEscaped(src, index) {
  let backslashes = 0;
  for (let i = index - 1; i >= 0 && src[i] === '\\'; i -= 1) {
    backslashes += 1;
  }
  return backslashes % 2 === 1;
}

function findUnescaped(src, token, start, { stopOnNewline = false, stopOnBlankLine = false } = {}) {
  for (let i = start; i <= src.length - token.length; i += 1) {
    const ch = src[i];
    if (stopOnNewline && ch === '\n') return -1;
    if (stopOnBlankLine && ch === '\n' && src[i + 1] === '\n') return -1;
    if (src.startsWith(token, i) && !isEscaped(src, i)) {
      return i;
    }
  }
  return -1;
}

function findSingleDollarEnd(src, start) {
  for (let i = start; i < src.length; i += 1) {
    const ch = src[i];
    if (ch === '\n') return -1;
    if (ch === '$' && !isEscaped(src, i)) {
      if (src[i + 1] === '$') {
        i += 1;
        continue;
      }
      if (i > start && !/[ \t]/.test(src[i - 1]) && !(src[i + 1] && /\d/.test(src[i + 1]))) {
        return i;
      }
    }
  }
  return -1;
}

function findBareParenLatexEnd(src, start) {
  if (start > 0 && (src[start - 1] === ']' || isEscaped(src, start))) return -1;
  let depth = 0;
  const maxEnd = Math.min(src.length, start + 240);
  for (let i = start; i < maxEnd; i += 1) {
    const ch = src[i];
    if (ch === '\n') return -1;
    if (ch === '(' && !isEscaped(src, i)) {
      depth += 1;
    } else if (ch === ')' && !isEscaped(src, i)) {
      depth -= 1;
      if (depth === 0) {
        const inner = src.slice(start + 1, i);
        if (!inner || /^\s|\s$/.test(inner)) return -1;
        if (/https?:\/\//i.test(inner) || /[A-Za-z]:\\/.test(inner)) return -1;
        const withoutText = inner.replace(/\\(?:text|mathrm|textbf|textit)\s*\{[^}]*\}/g, '');
        if (/[А-Яа-яЁё]+\s+[А-Яа-яЁё]+/.test(withoutText)) return -1;
        if (!MATH_COMMAND_RE.test(inner)) return -1;
        return i;
      }
    }
  }
  return -1;
}

export function extractMarkdownMath(input) {
  const src = String(input ?? '').replace(/\r\n?/g, '\n');
  const formulas = [];
  let out = '';
  let fence = null;
  let atLineStart = true;
  let i = 0;

  const pushFormula = (tex, display) => {
    const id = formulas.length;
    formulas.push({ tex: tex.trim(), display });
    return `\uE000M${id}\uE001`;
  };

  while (i < src.length) {
    if (atLineStart) {
      const rest = src.slice(i);
      const fenceMatch = rest.match(/^ {0,3}(`{3,}|~{3,})[^\n]*/);
      if (!fence && fenceMatch) {
        fence = { char: fenceMatch[1][0], len: fenceMatch[1].length };
        out += fenceMatch[0];
        i += fenceMatch[0].length;
        atLineStart = false;
        continue;
      }
      if (fence) {
        const lineEnd = src.indexOf('\n', i);
        const line = lineEnd === -1 ? src.slice(i) : src.slice(i, lineEnd + 1);
        const closeRe = new RegExp(`^ {0,3}${fence.char}{${fence.len},}\\s*$`);
        if (closeRe.test(line.replace(/\n$/, ''))) fence = null;
        out += line;
        i += line.length;
        atLineStart = line.endsWith('\n');
        continue;
      }
    }

    if (!fence) {
      if (src[i] === '`' && !isEscaped(src, i)) {
        let tickLen = 1;
        while (src[i + tickLen] === '`') tickLen += 1;
        const closer = '`'.repeat(tickLen);
        let j = i + tickLen;
        let found = -1;
        while (j <= src.length - tickLen) {
          if (src[j] === '\n' && src[j + 1] === '\n') break;
          if (src.startsWith(closer, j) && src[j - 1] !== '`' && src[j + tickLen] !== '`') {
            found = j;
            break;
          }
          j += 1;
        }
        if (found !== -1) {
          const span = src.slice(i, found + tickLen);
          out += span;
          i = found + tickLen;
          atLineStart = span.endsWith('\n');
          continue;
        }
      }

      if (src[i] === '$' && src[i + 1] === '$' && !isEscaped(src, i)) {
        const end = findUnescaped(src, '$$', i + 2);
        if (end !== -1 && end > i + 2) {
          const inner = src.slice(i + 2, end);
          if (inner.trim()) {
            out += pushFormula(inner, true);
            i = end + 2;
            atLineStart = false;
            continue;
          }
        }
      }

      if (src[i] === '\\' && src[i + 1] === '[' && !isEscaped(src, i)) {
        const end = findUnescaped(src, '\\]', i + 2);
        if (end !== -1 && end > i + 2) {
          const inner = src.slice(i + 2, end);
          if (inner.trim()) {
            out += pushFormula(inner, true);
            i = end + 2;
            atLineStart = false;
            continue;
          }
        }
      }

      if (src[i] === '\\' && src[i + 1] === '(' && !isEscaped(src, i)) {
        const end = findUnescaped(src, '\\)', i + 2, { stopOnBlankLine: true });
        if (end !== -1 && end > i + 2) {
          const inner = src.slice(i + 2, end);
          if (inner.trim()) {
            out += pushFormula(inner, false);
            i = end + 2;
            atLineStart = false;
            continue;
          }
        }
      }

      if (src[i] === '$' && src[i + 1] !== '$' && !isEscaped(src, i)) {
        if (src[i + 1] && !/[ \t\n]/.test(src[i + 1])) {
          const end = findSingleDollarEnd(src, i + 1);
          if (end !== -1) {
            const inner = src.slice(i + 1, end);
            if (inner.trim()) {
              out += pushFormula(inner, false);
              i = end + 1;
              atLineStart = false;
              continue;
            }
          }
        }
      }

      if (src[i] === '(') {
        const end = findBareParenLatexEnd(src, i);
        if (end !== -1) {
          out += pushFormula(src.slice(i + 1, end), false);
          i = end + 1;
          atLineStart = false;
          continue;
        }
      }
    }

    const ch = src[i];
    out += ch;
    atLineStart = ch === '\n';
    i += 1;
  }

  return { text: out, formulas };
}

export function MathFormula({ tex, display = false }) {
  const html = katex.renderToString(String(tex ?? ''), {
    displayMode: Boolean(display),
    throwOnError: false,
    errorColor: '#D46A6A',
    strict: 'ignore',
    trust: false,
    output: 'htmlAndMathml',
  });
  return (
    <span
      className={display ? 'ucheba-math-block' : 'ucheba-math-inline'}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function hydrateString(text, formulas, keyPrefix) {
  if (!text.includes('\uE000M')) return text;
  const parts = [];
  const re = /\uE000M(\d+)\uE001/g;
  let lastIndex = 0;
  let match;
  let partIdx = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const item = formulas[Number(match[1])];
    if (item) {
      parts.push(<MathFormula key={`${keyPrefix}-${partIdx}`} tex={item.tex} display={item.display} />);
      partIdx += 1;
    } else {
      parts.push(match[0]);
    }
    lastIndex = re.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts.length === 1 ? parts[0] : parts;
}

export function hydrateMathChildren(children, formulas, keyPrefix = 'm') {
  if (!formulas?.length || children == null) return children;
  if (typeof children === 'string') return hydrateString(children, formulas, keyPrefix);
  if (Array.isArray(children)) {
    let changed = false;
    const mapped = children.map((child, idx) => {
      if (typeof child !== 'string') return child;
      const next = hydrateString(child, formulas, `${keyPrefix}-${idx}`);
      if (next !== child) changed = true;
      return next;
    });
    return changed ? mapped.flat() : children;
  }
  return children;
}

function isSafeHref(href) {
  return typeof href === 'string' && /^(https?:|mailto:|#)/i.test(href.trim());
}

export function buildMathComponents(formulas, overrides = {}) {
  const wrap = (Tag) => ({ children }) => <Tag>{hydrateMathChildren(children, formulas)}</Tag>;
  return {
    p: wrap('p'),
    h1: wrap('h1'),
    h2: wrap('h2'),
    h3: wrap('h3'),
    h4: wrap('h4'),
    h5: wrap('h5'),
    h6: wrap('h6'),
    strong: wrap('strong'),
    em: wrap('em'),
    del: wrap('del'),
    blockquote: wrap('blockquote'),
    li: wrap('li'),
    td: wrap('td'),
    th: wrap('th'),
    a: ({ href, children }) => (
      isSafeHref(href)
        ? <a href={href} target="_blank" rel="noreferrer">{hydrateMathChildren(children, formulas)}</a>
        : <span>{hydrateMathChildren(children, formulas)}</span>
    ),
    img: ({ src, alt }) => (
      isSafeHref(src)
        ? <a href={src} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</a>
        : <span>{alt || 'Изображение'}</span>
    ),
    ...overrides,
  };
}
