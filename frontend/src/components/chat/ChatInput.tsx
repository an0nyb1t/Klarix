import { KeyboardEvent, useRef, useState } from 'react'

interface Props {
  onSend: (text: string) => void
  disabled?: boolean
  placeholder?: string
}

export function ChatInput({ onSend, disabled, placeholder }: Props) {
  const [text, setText] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      submit()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    const maxHeight = 6 * 24 // 6 lines × ~24px
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`
  }

  return (
    <div className="px-4 py-3 border-t border-gh-border bg-gh-bg">
      <div className="flex items-end gap-2 bg-gh-surface border border-gh-border rounded-xl px-3 py-2 focus-within:border-gh-accent/50 transition-colors">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={e => { setText(e.target.value); handleInput() }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder ?? 'Ask about this repository… (Ctrl+Enter to send)'}
          rows={1}
          className="flex-1 bg-transparent text-gh-text text-sm placeholder-gh-muted resize-none focus:outline-none leading-6 max-h-36 disabled:opacity-50"
        />
        <button
          onClick={submit}
          disabled={disabled || !text.trim()}
          className="shrink-0 px-3 py-1.5 rounded-lg bg-gh-accent text-gh-bg text-xs font-semibold hover:bg-gh-accent/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {disabled ? (
            <span className="flex items-center gap-1">
              <span className="w-3 h-3 rounded-full border-2 border-gh-bg/50 border-t-transparent animate-spin" />
              Wait
            </span>
          ) : 'Send'}
        </button>
      </div>
    </div>
  )
}
