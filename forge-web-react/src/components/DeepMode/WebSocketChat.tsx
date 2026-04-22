import { useState, useEffect, useRef } from 'react';
import { useAppStore, useDeepModeStore } from '../../stores';
import { Button, Card } from '../common';
import { DeepModeWebSocket } from '../../services';

export function WebSocketChat() {
  const [input, setInput] = useState('');
  const { sessionId, chatHistory, addChatMessage, setWsConnected } = useDeepModeStore();
  const { showNotification, setStep } = useAppStore();

  const wsRef = useRef<DeepModeWebSocket | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!sessionId) return;

    wsRef.current = new DeepModeWebSocket(
      sessionId,
      (data: any) => {
        if (data.type === 'tuning_response') {
          addChatMessage({ role: 'agent', content: data.content || '', timestamp: new Date().toISOString() });
          setInput('');
        }
      },
      () => setWsConnected(true),
      () => setWsConnected(false),
      (error: string) => showNotification(error, 'error')
    );

    wsRef.current.connect();

    return () => {
      wsRef.current?.disconnect();
    };
  }, [sessionId]);

  useEffect(() => {
    // Scroll to bottom when new messages arrive
    if (historyRef.current) {
      historyRef.current.scrollTop = historyRef.current.scrollHeight;
    }
  }, [chatHistory]);

  const handleSend = () => {
    if (!input.trim()) return;

    addChatMessage({ role: 'user', content: input, timestamp: new Date().toISOString() });
    wsRef.current?.send(input);
    setInput('');
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFinalize = () => {
    wsRef.current?.finalize();
  };

  const handleBack = () => {
    wsRef.current?.disconnect();
    setStep('deep');
  };

  return (
    <Card badge="步骤 2.9" title="微调对话">
      <p className="hint mb-4">全文已生成，您可以通过对话微调内容</p>

      {/* Chat history */}
      <div
        ref={historyRef}
        className="bg-gray-100 p-4 rounded-lg mb-4 max-h-300 overflow-y-auto"
        style={{ maxHeight: '300px' }}
      >
        <div className="chat-message agent-message">
          <p>全文已生成完成！您可以提出修改意见，比如：</p>
          <ul className="list-disc ml-5 text-gray-600 text-sm">
            <li>"把第二段改得更通俗一点"</li>
            <li>"整体语气太严肃了，改轻松点"</li>
            <li>"查一下'360度评估'的定义"</li>
          </ul>
        </div>

        {chatHistory.map((msg: any, i: number) => (
          <div key={i} className={`chat-message ${msg.role === 'user' ? 'user-message' : 'agent-message'}`}>
            <strong className="text-sm">
              {msg.role === 'user' ? '👤 您' : '🤖 Agent'}：
            </strong>
            <p className="whitespace-pre-wrap">{msg.content}</p>
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="flex gap-3">
        <input
          type="text"
          className="input flex-1"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyPress={handleKeyPress}
          placeholder="输入修改意见..."
        />
        <Button onClick={handleSend}>发送</Button>
      </div>

      {/* Actions */}
      <div className="flex gap-3 mt-4">
        <Button onClick={handleFinalize}>
          ✅ 定稿保存
        </Button>
        <Button onClick={handleBack} variant="secondary">
          ← 返回预览
        </Button>
      </div>
    </Card>
  );
}