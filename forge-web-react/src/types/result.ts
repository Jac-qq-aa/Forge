// Result types

export interface ProcessResult {
  success: boolean;
  script_path: string;
  video_path: string;
  script_content: string;
  final_script: string;
  original_title: string;
  original_text: string;
  original_author: string;
}

export interface TaskStatus {
  task_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;
  video_path?: string;
  error?: string;
}

export interface GenerateVideoParams {
  content: string;
  target_platform?: string;
}

export interface GenerateDigitalHumanParams {
  content: string;
  avatar_url?: string;
  voice?: string;
}