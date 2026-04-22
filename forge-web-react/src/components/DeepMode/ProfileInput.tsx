import { useState } from 'react';
import { useAppStore, useArticlesStore, useDeepModeStore } from '../../stores';
import { Button, Card, TextArea } from '../common';
import { deepModeApi } from '../../services';

export function ProfileInput() {
  const [userInput, setUserInput] = useState('');
  const { setStep, setLoading, showNotification, sourcePlatform, manualTitle, manualContent } = useAppStore();
  const { selectedArticle, questionUrl, answers, selectedAnswerIndexes } = useArticlesStore();
  const { setSessionId, setOutline } = useDeepModeStore();

  const handleStart = async () => {
    if (!userInput.trim()) {
      showNotification('请填写改写需求描述', 'error');
      return;
    }

    setLoading(true, '正在生成大纲...');

    try {
      // Prepare source article
      let sourceArticle: { title: string; text: string };
      let articleId: string;

      if (sourcePlatform === 'manual') {
        sourceArticle = { title: manualTitle, text: manualContent };
        articleId = manualTitle;
      } else if (selectedAnswerIndexes.length > 0 && answers.length > 0) {
        // Merge selected answers
        const selected = selectedAnswerIndexes.map((i: number) => answers[i]);
        sourceArticle = {
          title: `${selectedArticle?.title || '合并回答'}（合并回答）`,
          text: selected.map((a: any) => a.excerpt || a.text || '').join('\n\n'),
        };
        articleId = questionUrl || selectedArticle?.url || 'merged';
      } else {
        sourceArticle = {
          title: selectedArticle?.title || '',
          text: selectedArticle?.text || selectedArticle?.summary || '',
        };
        articleId = selectedArticle?.url || selectedArticle?.title || 'unknown';
      }

      const result = await deepModeApi.createSession({
        article_id: articleId,
        source_article: sourceArticle,
        user_input: userInput,
      });

      setSessionId(result.session_id);
      setOutline(result.outline, result.outline_version);
      setStep('deep');
      showNotification('大纲已生成！', 'success');
    } catch (error: any) {
      showNotification(`生成失败：${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleBack = () => {
    setStep('mode');
  };

  return (
    <Card badge="步骤 2.6" title="填写改写需求">
      <p className="hint mb-4">请描述您的改写需求，AI 将自动生成大纲</p>

      <TextArea
        id="deep-user-input"
        label="改写需求描述"
        value={userInput}
        onChange={setUserInput}
        placeholder="例如：改成知乎回答风格，语气专业，给HR从业者看，重点讲实操案例..."
        rows={4}
        hint="可描述语气风格、目标读者、侧重点、篇幅等"
      />

      <div className="flex gap-3 mt-4">
        <Button onClick={handleStart} size="large">
          ✨ 开始深度生成
        </Button>
        <Button onClick={handleBack} variant="secondary">
          ← 返回模式选择
        </Button>
      </div>
    </Card>
  );
}