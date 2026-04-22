import { useAppStore } from '../stores';
import { Card, Spinner } from './common';

export function ProcessingSection() {
  const { loadingMessage } = useAppStore();

  return (
    <Card badge="步骤 3" title="AI 智能改写">
      <div className="text-center py-8">
        <div className="flex justify-center mb-4">
          <Spinner size="large" />
        </div>
        <p className="text-gray-600 mb-2">正在处理...</p>
        <p className="text-sm text-gray-400">
          {loadingMessage || '调用 AI 模型改写内容，预计需要 20-40 秒...'}
        </p>
      </div>
    </Card>
  );
}