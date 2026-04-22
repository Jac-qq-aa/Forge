import { useArticlesStore, useAppStore } from '../stores';
import { Button, Card } from './common';
import type { Article } from '../types/article';

export function ArticlesList() {
  const { articles, selectedArticle, selectArticle } = useArticlesStore();
  const { setStep } = useAppStore();

  const handleSelect = (article: Article) => {
    selectArticle(article);
    setStep('mode');
  };

  const handleBack = () => {
    setStep('config');
  };

  return (
    <Card badge="步骤 2" title="选择文章">
      <p className="hint mb-4">找到 {articles.length} 篇文章</p>

      <div className="articles-list">
        {articles.map((article, index) => (
          <div
            key={index}
            className={`article-card ${selectedArticle?.id === article.id ? 'selected' : ''}`}
            onClick={() => handleSelect(article)}
          >
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs bg-gray-200 px-2 py-1 rounded">{article.type}</span>
              {article.author && (
                <span className="text-xs text-gray-500">{article.author}</span>
              )}
              <span className="text-xs text-gray-400"># {article.id}</span>
            </div>
            <h3 className="font-medium text-gray-800 mb-1">{article.title}</h3>
            {article.summary && (
              <p className="text-sm text-gray-500 truncate">{article.summary}</p>
            )}
          </div>
        ))}
      </div>

      <div className="flex gap-3 mt-4">
        <Button onClick={handleBack} variant="secondary">
          ← 返回配置
        </Button>
      </div>
    </Card>
  );
}