// WebSocket Service for Deep Mode chat

const WS_BASE = import.meta.env.VITE_WS_BASE || 'ws://localhost:8000';

export interface WsMessage {
  type: 'stage_update' | 'tuning_response' | 'tuning_message' | 'finalized' | 'error' | 'ping' | 'pong';
  session_id?: string;
  stage?: string;
  content?: string;
  current_draft?: string;
  updated_draft?: string;
  tuning_history?: any[];
  final_draft?: string;
  message?: string;
}

export class DeepModeWebSocket {
  private ws: WebSocket | null = null;
  private sessionId: string;
  private onMessage: (data: WsMessage) => void;
  private onConnect: () => void;
  private onDisconnect: () => void;
  private onError: (error: string) => void;

  constructor(
    sessionId: string,
    onMessage: (data: WsMessage) => void,
    onConnect: () => void,
    onDisconnect: () => void,
    onError: (error: string) => void
  ) {
    this.sessionId = sessionId;
    this.onMessage = onMessage;
    this.onConnect = onConnect;
    this.onDisconnect = onDisconnect;
    this.onError = onError;
  }

  connect() {
    this.ws = new WebSocket(`${WS_BASE}/ws/deep_mode/${this.sessionId}`);

    this.ws.onopen = () => {
      console.log('[WebSocket] Connected');
      this.onConnect();
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data) as WsMessage;
      this.onMessage(data);
    };

    this.ws.onerror = (error) => {
      console.error('[WebSocket] Error:', error);
      this.onError('WebSocket connection error');
    };

    this.ws.onclose = () => {
      console.log('[WebSocket] Disconnected');
      this.onDisconnect();
    };
  }

  send(message: string) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        type: 'tuning_message',
        content: message,
      }));
    }
  }

  finalize() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'finalize' }));
    }
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  isConnected(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }
}

export function createWebSocket(
  sessionId: string,
  handlers: {
    onMessage: (data: WsMessage) => void;
    onConnect: () => void;
    onDisconnect: () => void;
    onError: (error: string) => void;
  }
): DeepModeWebSocket {
  return new DeepModeWebSocket(
    sessionId,
    handlers.onMessage,
    handlers.onConnect,
    handlers.onDisconnect,
    handlers.onError
  );
}