import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { CodeBlock } from './CodeBlock'
import { DiffViewer } from './DiffViewer'
import type { Message } from '../../types'

interface Props {
  message: Message | { role: 'assistant'; content: string; isStreaming: boolean }
  repoId: string
  patchReady: boolean
}

function formatTimestamp(iso: string): string {
  // Backend stores UTC — ensure JS parses as UTC so toLocaleString converts to local
  const utcIso = iso.endsWith('Z') ? iso : iso + 'Z'
  return new Date(utcIso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function MessageBubble({ message, repoId, patchReady }: Props) {
  const isUser = message.role === 'user'
  const isStreaming = 'isStreaming' in message && message.isStreaming
  const timestamp = 'created_at' in message ? formatTimestamp(message.created_at) : undefined

  // Build markdown components inside the component so DiffViewer receives
  // repoId and patchReady from closure (module-level constant can't receive props)
  const markdownComponents: Components = {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    code({ className, children }: any) {
      const match = /language-(\w+)/.exec(className ?? '')
      const lang = match?.[1]
      const code = String(children).replace(/\n$/, '')

      // react-markdown v9 removed the `inline` prop.
      // Block code has a language className or contains newlines.
      const isBlock = Boolean(className) || code.includes('\n')

      if (isBlock && lang === 'diff') {
        return <DiffViewer diff={code} repoId={repoId} patchReady={patchReady} />
      }
      if (isBlock) {
        return <CodeBlock code={code} language={lang} />
      }
      return (
        <code className="px-1 py-0.5 rounded bg-gh-border/50 text-gh-accent font-mono text-xs">
          {children}
        </code>
      )
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    pre({ children }: any) {
      return <>{children}</>
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    a({ href, children }: any) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          className="text-gh-accent hover:underline"
        >
          {children}
        </a>
      )
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    p({ children }: any) {
      return <p className="mb-3 last:mb-0">{children}</p>
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ul({ children }: any) {
      return <ul className="list-disc list-inside mb-3 space-y-1">{children}</ul>
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ol({ children }: any) {
      return <ol className="list-decimal list-inside mb-3 space-y-1">{children}</ol>
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    h1({ children }: any) { return <h1 className="text-lg font-semibold text-gh-text mb-2 mt-4">{children}</h1> },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    h2({ children }: any) { return <h2 className="text-base font-semibold text-gh-text mb-2 mt-3">{children}</h2> },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    h3({ children }: any) { return <h3 className="text-sm font-semibold text-gh-text mb-1 mt-2">{children}</h3> },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    blockquote({ children }: any) {
      return (
        <blockquote className="border-l-2 border-gh-accent/40 pl-3 text-gh-muted italic my-2">
          {children}
        </blockquote>
      )
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    table({ children }: any) {
      return (
        <div className="overflow-x-auto my-3">
          <table className="w-full text-xs border-collapse border border-gh-border">
            {children}
          </table>
        </div>
      )
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    thead({ children }: any) {
      return <thead className="bg-gh-bg">{children}</thead>
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    th({ children }: any) {
      return (
        <th className="border border-gh-border px-3 py-1.5 text-left text-gh-text font-semibold">
          {children}
        </th>
      )
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    td({ children }: any) {
      return (
        <td className="border border-gh-border px-3 py-1.5 text-gh-muted">
          {children}
        </td>
      )
    },
    hr() {
      return <hr className="border-gh-border my-4" />
    },
  }

  return (
    <div className={`flex flex-col mb-4 ${isUser ? 'items-end' : 'items-start'}`}>
      <div className={`flex w-full ${isUser ? 'justify-end' : 'justify-start'}`}>
        {!isUser && (
          <div className="w-7 h-7 rounded-full bg-gh-accent/20 flex items-center justify-center shrink-0 mr-2.5 mt-0.5">
            <span className="text-gh-accent text-xs font-bold">G</span>
          </div>
        )}

        <div
          className={`max-w-[80%] min-w-0 overflow-hidden break-words rounded-xl px-4 py-3 text-sm leading-relaxed ${
            isUser
              ? 'bg-gh-accent/15 text-gh-text rounded-tr-sm'
              : 'bg-gh-surface border border-gh-border text-gh-text rounded-tl-sm'
          }`}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
          )}
          {isStreaming && (
            <span className="inline-block w-1.5 h-4 bg-gh-accent ml-0.5 animate-pulse" />
          )}
        </div>
      </div>
      {timestamp && (
        <span className={`text-[10px] text-gh-muted mt-1 ${isUser ? 'mr-1' : 'ml-10'}`}>
          {timestamp}
        </span>
      )}
    </div>
  )
}
