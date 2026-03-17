interface Props {
  message: string
  onDismiss: () => void
}

export function ErrorToast({ message, onDismiss }: Props) {
  return (
    <div className="fixed bottom-4 right-4 z-50 flex items-start gap-3 max-w-sm rounded-lg border border-gh-danger/30 bg-gh-surface px-4 py-3 shadow-lg">
      <p className="text-gh-text text-sm flex-1">{message}</p>
      <button
        onClick={onDismiss}
        className="text-gh-muted hover:text-gh-text transition-colors text-xs shrink-0"
        aria-label="Dismiss"
      >
        x
      </button>
    </div>
  )
}
