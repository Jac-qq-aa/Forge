import { useArticlesStore, useAppStore } from '../stores';
import { Button, Card } from './common';

export function ModeSelection() {
  const { selectedArticle } = useArticlesStore();
  const { setStep, sourcePlatform, manualTitle } = useAppStore();

  const handleNormalMode = async () => {
    // TODO: Call process API
    setStep('processing');
  };

  const handleDeepMode = () => {
    setStep('deep');
  };

  const handleBack = () => {
    setStep('articles');
  };

  const articleTitle = sourcePlatform === 'manual'
    ? manualTitle
    : selectedArticle?.title;

  return (
    <Card badge="步骤 2.5" title="选择改写模式">
      <p className="hint font-semibold mb-4">
        已选择：{articleTitle || '未选择文章'}
      </p>

      <div className="flex gap-4">
        <div
          className="flex-1 p-5 border-2 border-gray-300 rounded-xl cursor-pointer text-center hover:border-gray-400 transition-all"
          onClick={handleNormalMode}
        >
          <h3 className="font-semibold mb-2">⚡ 快速改写</h3>
          <p className="text-sm text-gray-600">一键生成，适合快速产出</p>
          <p className="text-xs text-gray-400 mt-1">约 20-40 秒</p>
        </div>

        <div
          className="flex-1 p-5 border-2 border-primary rounded-xl cursor-pointer text-center bg-green-50 hover:bg-green-100 transition-all"
          onClick={handleDeepMode}
        >
          <h3 className="font-semibold mb-2">🌟 深度生成</h3>
          <p className="text-sm text-gray-600">多轮对话，精细定制</p>
          <p className="text-xs text-gray-400 mt-1">可修改大纲、微调内容</p>
        </div>
      </div>

      <div className="mt-4">
        <Button onClick={handleBack} variant="secondary">
          ← 返回文章列表
        </Button>
      </div>
    </Card>
  );
}