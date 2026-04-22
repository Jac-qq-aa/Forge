import { useAppStore, useDeepModeStore } from '../../stores';
import { Button, Card, TextArea } from '../common';

export function ContentPreview() {
  const { sessionId, content, contentModified, setContent, setContentModified } = useDeepModeStore();
  const { setLoading, showNotification, setStep } = useAppStore();

  const handleStartChat = () => {
    // TODO: Start WebSocket chat
    setStep('deep');
  };

  const handleFinalize = async () => {
    if (!sessionId) return;

    setLoading(true, '正在定稿...');

    try {
      // TODO: Call finalize API
      showNotification('已定稿保存！', 'success');
      setStep('config');
    } catch (error: any) {
      showNotification(`定稿失败：${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleBack = () => {
    setStep('deep');
  };

  const handleBackToQuick = () => {
    setStep('mode');
  };

  const handleContentChange = (value: string) => {
    setContent(value);
    setContentModified(true);
  };

  return (
    <Card badge="步骤 2.8" title="全文预览">
      <p className="hint text-green-600 mb-4">全文已生成，可直接编辑或进入对话微调</p>

      <div className="bg-gray-100 p-4 rounded-lg mb-4">
        <h4 className="font-medium text-gray-700 mb-2">生成的全文（可直接编辑）：</h4>
        <TextArea
          id="deep-content-preview"
          value={content}
          onChange={handleContentChange}
          rows={15}
        />
      </div>

      <div className="flex flex-wrap gap-3">
        <Button onClick={handleStartChat}>
          💬 进入微调对话
        </Button>

        {contentModified && (
          <Button variant="warning" onClick={() => setContentModified(false)}>
            💾 保存编辑
          </Button>
        )}

        <Button onClick={handleFinalize} variant="secondary">
          ✅ 定稿保存
        </Button>

        <Button onClick={handleBack} variant="secondary">
          ← 返回修改大纲
        </Button>

        <Button onClick={handleBackToQuick} variant="secondary">
          ← 返回快速改写
        </Button>
      </div>
    </Card>
  );
}