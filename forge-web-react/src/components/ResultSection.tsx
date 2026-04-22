import { useAppStore } from '../stores';
import { Button, Card, TextArea } from './common';

export function ResultSection() {
  const { setStep } = useAppStore();

  // TODO: Get result from store

  const handleDownload = () => {
    // TODO: Implement download
  };

  const handleGenerateVideo = () => {
    // TODO: Implement video generation
  };

  const handleReset = () => {
    setStep('config');
  };

  return (
    <Card badge="步骤 4" title="改写结果">
      <div className="mb-4">
        <h4 className="font-medium text-gray-700 mb-2">改写后的文案：</h4>
        <TextArea
          id="result-content"
          value="改写结果内容..."
          onChange={() => {}}
          rows={15}
        />
      </div>

      <div className="flex flex-wrap gap-3">
        <Button onClick={handleDownload}>
          📥 下载文案
        </Button>
        <Button onClick={handleGenerateVideo}>
          🎬 生成视频
        </Button>
        <Button onClick={handleReset} variant="secondary">
          🔄 开始新任务
        </Button>
      </div>
    </Card>
  );
}