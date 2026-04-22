import { create } from 'zustand';
import type { Article, Answer } from '../types/article';

interface ArticlesState {
  // Articles list
  articles: Article[];
  selectedArticle: Article | null;

  // Answers for Zhihu question
  questionUrl: string;
  questionTitle: string;
  answers: Answer[];
  selectedAnswerIndexes: number[];

  // Actions
  setArticles: (articles: Article[]) => void;
  selectArticle: (article: Article) => void;
  clearArticles: () => void;

  setQuestion: (url: string, title: string) => void;
  setAnswers: (answers: Answer[]) => void;
  toggleAnswer: (index: number) => void;
  clearAnswers: () => void;
}

export const useArticlesStore = create<ArticlesState>((set) => ({
  // Initial state
  articles: [],
  selectedArticle: null,

  questionUrl: '',
  questionTitle: '',
  answers: [],
  selectedAnswerIndexes: [],

  // Actions
  setArticles: (articles) => set({ articles }),
  selectArticle: (article) => set({ selectedArticle: article }),
  clearArticles: () => set({ articles: [], selectedArticle: null }),

  setQuestion: (url, title) => set({ questionUrl: url, questionTitle: title }),
  setAnswers: (answers) => set({ answers }),
  toggleAnswer: (index) => set((state) => {
    const indexes = state.selectedAnswerIndexes.includes(index)
      ? state.selectedAnswerIndexes.filter((i) => i !== index)
      : [...state.selectedAnswerIndexes, index];
    return { selectedAnswerIndexes: indexes };
  }),
  clearAnswers: () => set({ questionUrl: '', questionTitle: '', answers: [], selectedAnswerIndexes: [] }),
}));