import { useEffect, useRef } from 'react'
import { useWebSocket } from '../../hooks/useWebSocket'
import { conversationsApi } from '../../api/conversations'
import { MessageBubble } from './MessageBubble'
import { ChatInput } from './ChatInput'
import { ModelPicker } from './ModelPicker'
import type { Conversation, Settings } from '../../types'

interface Props {
  conversation: Conversation
  repoName: string
  globalSettings: Settings | null
  onModelChange: (updated: Conversation) => void
}

export function ChatWindow({ conversation, repoName, globalSettings, onModelChange }: Props) {
  const {
    sendMessage,
    messages,
    streamingContent,
    isConnected,
    isLoading,
    error,
    rateLimited,
    setMessages,
  } = useWebSocket(conversation.id)

  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    conversationsApi.messages(conversation.id).then(setMessages).catch(() => {})
  }, [conversation.id, setMessages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Conversation title bar */}
      <div className="flex items-center px-4 h-10 border-b border-gh-border shrink-0 gap-2">
        <span className="text-gh-muted text-xs">{repoName}</span>
        <span className="text-gh-border mx-1 text-xs">›</span>
        <span className="text-gh-text text-xs truncate min-w-0 flex-1">{conversation.title}</span>
        {!isConnected && (
          <span className="text-gh-muted text-xs flex items-center gap-1 shrink-0">
            <span className="w-1.5 h-1.5 rounded-full bg-gh-muted animate-pulse" />
            Connecting…
          </span>
        )}
        <div className="shrink-0">
          <ModelPicker
            conversation={conversation}
            globalSettings={globalSettings}
            onModelChange={onModelChange}
          />
        </div>
      </div>

      {/* Messages scroll area */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 && streamingContent === null && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <p className="text-gh-muted text-sm">
                Ask anything about{' '}
                <span className="text-gh-text font-medium">{repoName}</span>
              </p>
              <p className="text-gh-muted text-xs mt-1">
                Code structure, commits, issues, PRs, or request a code change.
              </p>
            </div>
          </div>
        )}

        {messages.map(msg => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {streamingContent !== null && (
          <MessageBubble
            message={{ role: 'assistant', content: streamingContent, isStreaming: true }}
          />
        )}

        {error && (
          <p className="text-gh-danger text-xs text-center py-2">{error}</p>
        )}

        {rateLimited && (
          <div className="mx-auto max-w-sm rounded-lg border border-gh-amber/30 bg-gh-amber/10 px-4 py-3 my-2">
            <p className="text-gh-amber text-sm font-medium">Rate limit reached</p>
            <p className="text-gh-muted text-xs mt-1">{rateLimited.message}</p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <ChatInput
        onSend={sendMessage}
        disabled={isLoading || !isConnected}
        placeholder={
          isConnected
            ? 'Ask about this repository… (Ctrl+Enter to send)'
            : 'Connecting…'
        }
      />
    </div>
  )
}
