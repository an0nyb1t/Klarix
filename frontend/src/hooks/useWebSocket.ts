import { useCallback, useEffect, useRef, useState } from 'react'
import { wsUrl } from '../api/client'
import type { Message, WsMessage } from '../types'

interface UseWebSocketReturn {
  sendMessage: (text: string) => void
  messages: Message[]
  streamingContent: string | null
  isConnected: boolean
  isLoading: boolean
  error: string | null
  rateLimited: { message: string; resets_at: string | null } | null
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
}

export function useWebSocket(conversationId: string | null): UseWebSocketReturn {
  const [messages, setMessages] = useState<Message[]>([])
  const [streamingContent, setStreamingContent] = useState<string | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rateLimited, setRateLimited] = useState<{ message: string; resets_at: string | null } | null>(null)

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Reset all state when switching conversations
  useEffect(() => {
    setMessages([])
    setStreamingContent(null)
    setError(null)
    setRateLimited(null)
    setIsLoading(false)
  }, [conversationId])

  const connect = useCallback(() => {
    if (!conversationId) return

    const ws = new WebSocket(wsUrl(`/api/conversations/${conversationId}/chat`))
    wsRef.current = ws

    ws.onopen = () => {
      setIsConnected(true)
      setError(null)
    }

    ws.onmessage = (event: MessageEvent) => {
      const msg = JSON.parse(event.data as string) as WsMessage

      if (msg.type === 'chunk') {
        setStreamingContent(prev => (prev ?? '') + msg.content)
      } else if (msg.type === 'done') {
        setStreamingContent(prev => {
          if (prev !== null) {
            const finalContent = prev
            setMessages(msgs => [
              ...msgs,
              {
                id: msg.message_id,
                conversation_id: conversationId,
                role: 'assistant',
                content: finalContent,
                has_diff: finalContent.includes('```diff'),
                created_at: new Date().toISOString(),
              },
            ])
          }
          return null
        })
        setIsLoading(false)
      } else if (msg.type === 'error') {
        setError(msg.message)
        setStreamingContent(null)
        setIsLoading(false)
      } else if (msg.type === 'rate_limited') {
        setRateLimited({ message: msg.message, resets_at: msg.resets_at })
        setStreamingContent(null)
        setIsLoading(false)
      }
    }

    ws.onclose = () => {
      setIsConnected(false)
      // Reconnect after 2s if still mounted
      reconnectTimer.current = setTimeout(() => connect(), 2000)
    }

    ws.onerror = () => {
      setError('WebSocket connection error.')
      ws.close()
    }
  }, [conversationId])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendMessage = useCallback(
    (text: string) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
      setError(null)
      setRateLimited(null)
      setIsLoading(true)
      setStreamingContent('')

      // Optimistically add user message to the list
      setMessages(prev => [
        ...prev,
        {
          id: crypto.randomUUID(),
          conversation_id: conversationId ?? '',
          role: 'user',
          content: text,
          has_diff: false,
          created_at: new Date().toISOString(),
        },
      ])

      wsRef.current.send(JSON.stringify({ message: text }))
    },
    [conversationId],
  )

  return {
    sendMessage,
    messages,
    streamingContent,
    isConnected,
    isLoading,
    error,
    rateLimited,
    setMessages,
  }
}
