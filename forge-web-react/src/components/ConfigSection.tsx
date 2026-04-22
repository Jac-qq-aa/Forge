import { useAppStore, useArticlesStore } from '../stores';
import { Select, Input, TextArea, Button } from './common';
import { searchApi } from '../services/api';

export function ConfigSection() {
  const {
    sourcePlatform,
    targetPlatform,
    searchMode,
    searchKeyword,
    bloggerId,
    maxResults,
    manualTitle,
    manualContent,
    setSourcePlatform,
    setTargetPlatform,
    setSearchMode,
    setSearchKeyword,
    setBloggerId,
    setMaxResults,
    setManualTitle,
    setManualContent,
    loading,
    loadingMessage,
    setLoading,
    showNotification,
    setStep,
  } = useAppStore();

  const { setArticles } = useArticlesStore();

  const showManualInput = sourcePlatform === 'manual';
  const showKeywordGroup = searchMode === 'keyword' && !showManualInput;
  const showBloggerGroup = searchMode === 'blogger' && !showManualInput;

  const handleSearch = async () => {
    console.log('handleSearch called', { sourcePlatform, searchMode, searchKeyword, bloggerId });
    // Manual input - go directly to mode selection
    if (sourcePlatform === 'manual') {
      if (!manualTitle.trim() || !manualContent.trim()) {
        showNotification('请填写标题和内容', 'error');
        return;
      }
      setStep('mode');
      return;
    }

    // Validate search params
    if (searchMode === 'keyword' && !searchKeyword.trim()) {
      showNotification('请填写搜索关键词', 'error');
      return;
    }
    if (searchMode === 'blogger' && !bloggerId.trim()) {
      showNotification('请填写博主 ID', 'error');
      return;
    }

    setLoading(true, '正在搜索文章...');

    try {
      const result = await searchApi.searchArticles({
        source: searchMode === 'keyword' ? searchKeyword : bloggerId,
        source_platform: sourcePlatform,
        max_results: maxResults,
        search_mode: searchMode,
      });

      if (result.success && result.articles.length > 0) {
        // Convert API articles to store format
        const articles = result.articles.map((a: any, i: number) => ({
          id: i,
          title: a.title || '',
          summary: a.summary || a.excerpt || '',
          source_url: a.url || a.source_url || '',
          url: a.url || a.source_url || '',
          type: a.type || sourcePlatform,
          author: a.author || '',
          text: a.text || '',
        }));
        setArticles(articles);
        setStep('articles');
        showNotification(`找到 ${articles.length} 篇文章`, 'success');
      } else {
        showNotification('没有找到文章', 'error');
      }
    } catch (error: any) {
      showNotification(`搜索失败：${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <div className="card-header flex items-center mb-4">
        <span className="step-badge">步骤 1</span>
        <h2 className="text-xl font-semibold text-gray-800">配置参数</h2>
      </div>

      <Select
        id="source-platform"
        label="来源平台"
        value={sourcePlatform}
        onChange={(v) => setSourcePlatform(v as any)}
        options={[
          { value: 'zhihu', label: '知乎' },
          { value: 'wechat', label: '微信公众号' },
          { value: 'manual', label: '手动输入' },
        ]}
      />

      {showManualInput && (
        <>
          <Input
            id="manual-title"
            label="文章标题"
            value={manualTitle}
            onChange={setManualTitle}
            placeholder="输入文章标题..."
          />
          <TextArea
            id="manual-content"
            label="文章内容"
            value={manualContent}
            onChange={setManualContent}
            placeholder="输入文章内容..."
            rows={10}
          />
        </>
      )}

      <Select
        id="target-platform"
        label="目标平台"
        value={targetPlatform}
        onChange={(v) => setTargetPlatform(v as any)}
        options={[
          { value: 'zhihu_article', label: '知乎文章' },
          { value: 'wechat_article', label: '微信公众号文章' },
        ]}
      />

      {!showManualInput && (
        <>
          <Select
            id="search-mode"
            label="搜索方式"
            value={searchMode}
            onChange={(v) => setSearchMode(v as any)}
            options={[
              { value: 'keyword', label: '关键词搜索' },
              { value: 'blogger', label: '博主文章' },
            ]}
          />

          {showKeywordGroup && (
            <Input
              id="search-keyword"
              label="搜索关键词"
              value={searchKeyword}
              onChange={setSearchKeyword}
              placeholder="输入搜索关键词..."
            />
          )}

          {showBloggerGroup && (
            <Input
              id="blogger-id"
              label="博主 ID"
              value={bloggerId}
              onChange={setBloggerId}
              placeholder="知乎用户主页链接中的ID..."
              hint="如 zhihu.com/people/rui-bo-ji-tuan-5"
            />
          )}

          <Input
            id="max-results"
            label="最大结果数"
            value={String(maxResults)}
            onChange={(v) => setMaxResults(parseInt(v) || 5)}
            type="number"
          />
        </>
      )}

      <Button
        onClick={handleSearch}
        loading={loading}
        disabled={loading}
        size="large"
        className="mt-4 w-full"
      >
        🔍 {sourcePlatform === 'manual' ? '确认输入' : '搜索文章'}
      </Button>

      {loading && (
        <p className="hint mt-2 text-center">{loadingMessage}</p>
      )}
    </div>
  );
}