// Article types

export interface Article {
  id: number;
  title: string;
  summary: string;
  source_url: string;
  url?: string; // alias for source_url
  type: string;
  author: string;
  text?: string;
}

export interface Answer {
  index: number;
  author: string;
  excerpt: string;
  likes: number;
  url: string;
  text?: string;
}

export interface SearchParams {
  source: string;
  source_platform: string;
  max_results: number;
  search_mode: string;
}

export interface ProcessParams {
  source_url: string;
  source_platform: string;
  target_platform: string;
  generate_video: boolean;
}

export interface ProcessMultiParams {
  question_url: string;
  answer_urls: string[];
  source_platform: string;
  target_platform: string;
  generate_video: boolean;
}