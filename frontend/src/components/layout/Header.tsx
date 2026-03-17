interface Props {
  onSettings: () => void
}

export function Header({ onSettings }: Props) {
  return (
    <header className="flex items-center justify-between px-4 h-12 bg-gh-surface border-b border-gh-border shrink-0">
      <div className="flex items-center gap-2">
        <span className="text-gh-accent font-semibold text-base tracking-tight">GitChat</span>
      </div>
      <button
        onClick={onSettings}
        title="Settings"
        className="p-1.5 rounded text-gh-muted hover:text-gh-text hover:bg-white/5 transition-colors"
      >
        <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Zm7-1.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z" />
          <path d="M7.5 2.5a.5.5 0 0 1 1 0v1.25a4.5 4.5 0 0 1 3.61 3.61H13.5a.5.5 0 0 1 0 1h-1.39a4.5 4.5 0 0 1-3.61 3.61V13.5a.5.5 0 0 1-1 0v-1.39A4.5 4.5 0 0 1 3.89 8.5H2.5a.5.5 0 0 1 0-1h1.39A4.5 4.5 0 0 1 7.5 3.89V2.5Z" />
        </svg>
      </button>
    </header>
  )
}
