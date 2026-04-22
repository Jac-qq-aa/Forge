import { create } from 'zustand';

// Types
type Step = 'config' | 'articles' | 'mode' | 'processing' | 'result' | 'deep';

interface NotificationState {
  message: string;
  type: 'success' | 'error' | 'info';
}

interface AppState {
  // UI state
  currentStep: Step;
  notification: NotificationState | null;
  loading: boolean;
  loadingMessage: string;

  // Config
  sourcePlatform: 'zhihu' | 'wechat' | 'manual';
  targetPlatform: 'zhihu_article' | 'wechat_article';
  searchMode: 'keyword' | 'blogger';
  searchKeyword: string;
  bloggerId: string;
  maxResults: number;
  manualTitle: string;
  manualContent: string;

  // Actions
  setStep: (step: Step) => void;
  setLoading: (loading: boolean, message?: string) => void;
  showNotification: (message: string, type: 'success' | 'error' | 'info') => void;
  clearNotification: () => void;
  setSourcePlatform: (platform: 'zhihu' | 'wechat' | 'manual') => void;
  setTargetPlatform: (platform: 'zhihu_article' | 'wechat_article') => void;
  setSearchMode: (mode: 'keyword' | 'blogger') => void;
  setSearchKeyword: (keyword: string) => void;
  setBloggerId: (id: string) => void;
  setMaxResults: (count: number) => void;
  setManualTitle: (title: string) => void;
  setManualContent: (content: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  // Initial state
  currentStep: 'config',
  notification: null,
  loading: false,
  loadingMessage: '',

  sourcePlatform: 'zhihu',
  targetPlatform: 'zhihu_article',
  searchMode: 'keyword',
  searchKeyword: '人力资源',
  bloggerId: '',
  maxResults: 5,
  manualTitle: '',
  manualContent: '',

  // Actions
  setStep: (step) => set({ currentStep: step }),
  setLoading: (loading, message = '') => set({ loading, loadingMessage: message }),
  showNotification: (message, type) => set({ notification: { message, type } }),
  clearNotification: () => set({ notification: null }),

  setSourcePlatform: (platform) => set({ sourcePlatform: platform }),
  setTargetPlatform: (platform) => set({ targetPlatform: platform }),
  setSearchMode: (mode) => set({ searchMode: mode }),
  setSearchKeyword: (keyword) => set({ searchKeyword: keyword }),
  setBloggerId: (id) => set({ bloggerId: id }),
  setMaxResults: (count) => set({ maxResults: count }),
  setManualTitle: (title) => set({ manualTitle: title }),
  setManualContent: (content) => set({ manualContent: content }),
}));