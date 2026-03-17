import { useEffect, useRef, useState } from 'react'
import hljs from 'highlight.js'

interface Props {
  code: string
  language?: string
}

export function CodeBlock({ code, language }: Props) {
  const ref = useRef<HTMLElement>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!ref.current) return
    if (language) {
      try {
        const result = hljs.highlight(code, { language, ignoreIllegals: true })
        ref.current.innerHTML = result.value
        return
      } catch {
        // Fall through to auto-detect
      }
    }
    hljs.highlightElement(ref.current)
  }, [code, language])

  const copy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative group rounded-lg overflow-hidden border border-gh-border bg-gh-surface my-2">
      {language && (
        <div className="flex items-center justify-between px-3 py-1.5 bg-gh-border/30 border-b border-gh-border">
          <span className="text-gh-muted text-xs font-mono">{language}</span>
          <button
            onClick={copy}
            className="text-gh-muted hover:text-gh-text text-xs transition-colors"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      )}
      {!language && (
        <button
          onClick={copy}
          className="absolute top-2 right-2 text-gh-muted hover:text-gh-text text-xs opacity-0 group-hover:opacity-100 transition-opacity"
        >
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      )}
      <pre className="overflow-x-auto p-4 text-sm font-mono leading-relaxed">
        <code ref={ref} className={language ? `language-${language}` : ''}>
          {code}
        </code>
      </pre>
    </div>
  )
}
