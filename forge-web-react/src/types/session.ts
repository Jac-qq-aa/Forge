// Session types for deep mode

export interface DeepSession {
  session_id: string;
  article_id: string;
  stage: SessionStage;
  outline: string;
  outline_version: number;
  draft_v1: string;
  current_draft: string;
  tuning_history: ChatMessage[];
  source_article: {
    title: string;
    text: string;
  };
  rag_context: string;
  final_draft: string;
  finalized_at: string | null;
  created_at: string;
  updated_at: string;
}

export type SessionStage =
  | 'waiting_input'
  | 'generating_outline'
  | 'waiting_outline'
  | 'generating_content'
  | 'tuning'
  | 'completed'
  | 'cancelled';

export interface ChatMessage {
  role: 'user' | 'agent';
  content: string;
  timestamp: string;
}

export interface CreateSessionParams {
  article_id: string;
  source_article: {
    title: string;
    text: string;
  };
  user_input: string;
}

export interface OutlineActionParams {
  session_id: string;
  action: 'accept' | 'modify';
  modification?: string;
}