import { create } from 'zustand';
import type { DeepSession, ChatMessage } from '../types/session';

interface DeepModeState {
  // Session data
  sessionId: string | null;
  session: DeepSession | null;

  // Outline
  outline: string;
  outlineVersion: number;
  outlineModified: boolean;

  // Content
  content: string;
  contentModified: boolean;

  // Chat
  chatHistory: ChatMessage[];
  wsConnected: boolean;

  // Actions
  setSessionId: (id: string) => void;
  setSession: (session: DeepSession) => void;
  setOutline: (outline: string, version?: number) => void;
  setContent: (content: string) => void;
  setOutlineModified: (modified: boolean) => void;
  setContentModified: (modified: boolean) => void;
  addChatMessage: (message: ChatMessage) => void;
  setWsConnected: (connected: boolean) => void;
  clear: () => void;
}

export const useDeepModeStore = create<DeepModeState>((set) => ({
  // Initial state
  sessionId: null,
  session: null,

  outline: '',
  outlineVersion: 0,
  outlineModified: false,

  content: '',
  contentModified: false,

  chatHistory: [],
  wsConnected: false,

  // Actions
  setSessionId: (id) => set({ sessionId: id }),
  setSession: (session) => set({ session }),
  setOutline: (outline, version) => set({ outline, outlineVersion: version ?? 1, outlineModified: false }),
  setContent: (content) => set({ content, contentModified: false }),
  setOutlineModified: (modified) => set({ outlineModified: modified }),
  setContentModified: (modified) => set({ contentModified: modified }),
  addChatMessage: (message) => set((state) => ({
    chatHistory: [...state.chatHistory, message],
  })),
  setWsConnected: (connected) => set({ wsConnected: connected }),
  clear: () => set({
    sessionId: null,
    session: null,
    outline: '',
    outlineVersion: 0,
    outlineModified: false,
    content: '',
    contentModified: false,
    chatHistory: [],
    wsConnected: false,
  }),
}));