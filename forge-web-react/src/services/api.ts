// API Service - 封装所有 API 调用

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

// Helper function for API calls
async function apiCall<T>(
  endpoint: string,
  method: 'GET' | 'POST' | 'DELETE' = 'GET',
  body?: object
): Promise<T> {
  const options: RequestInit = {
    method,
    headers: {
      'Content-Type': 'application/json',
    },
  };

  if (body) {
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE}${endpoint}`, options);
  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || `API Error: ${response.status}`);
  }

  return data;
}

// Search API
export const searchApi = {
  searchArticles: (params: {
    source: string;
    source_platform: string;
    max_results: number;
    search_mode: string;
  }) => apiCall<{ success: boolean; articles: any[]; count: number }>('/api/search', 'POST', params),
};

// Process API
export const processApi = {
  processArticle: (params: {
    source_url: string;
    source_platform: string;
    target_platform: string;
    generate_video: boolean;
  }) => apiCall<any>('/api/process', 'POST', params),

  processManual: (params: {
    title: string;
    text: string;
    source_platform: string;
    target_platform: string;
    generate_video: boolean;
  }) => apiCall<any>('/api/process_manual', 'POST', params),

  processMulti: (params: {
    question_url: string;
    answer_urls: string[];
    source_platform: string;
    target_platform: string;
    generate_video: boolean;
  }) => apiCall<any>('/api/process_multi', 'POST', params),

  getAnswers: (params: { question_url: string; max_answers: number }) =>
    apiCall<{ success: boolean; answers: any[]; question_title: string }>('/api/get_answers', 'POST', params),
};

// Deep Mode API
export const deepModeApi = {
  createSession: (params: {
    article_id: string;
    source_article: { title: string; text: string };
    user_input: string;
  }) =>
    apiCall<{
      session_id: string;
      stage: string;
      outline: string;
      outline_version: number;
    }>('/api/deep_mode/create_session', 'POST', params),

  getSession: (sessionId: string) =>
    apiCall<any>(`/api/deep_mode/session/${sessionId}`, 'GET'),

  updateOutline: (params: { session_id: string; outline: string }) =>
    apiCall<{ status: string; session_id: string; outline: string }>('/api/deep_mode/update_outline', 'POST', params),

  outlineAction: (params: {
    session_id: string;
    action: 'accept' | 'modify';
    modification?: string;
  }) =>
    apiCall<{
      status: string;
      session_id: string;
      stage: string;
      outline?: string;
      outline_version?: number;
      draft?: string;
    }>('/api/deep_mode/outline_action', 'POST', params),

  finalize: (params: { session_id: string }) =>
    apiCall<{ status: string; session_id: string; final_draft: string; finalized_at: string }>('/api/deep_mode/finalize', 'POST', params),

  cancelSession: (sessionId: string) =>
    apiCall<{ status: string; session_id: string }>(`/api/deep_mode/session/${sessionId}`, 'DELETE'),

  listSessions: (params?: { article_id?: string; stage?: string }) => {
    const query = new URLSearchParams(params as any).toString();
    return apiCall<{ sessions: any[]; count: number }>(`/api/deep_mode/sessions?${query}`, 'GET');
  },
};

// Video API
export const videoApi = {
  generateVideo: (params: { content: string; target_platform?: string }) =>
    apiCall<{ success: boolean; video_path: string }>('/api/generate_video', 'POST', params),

  generateDigitalHuman: (params: { content: string; avatar_url?: string; voice?: string }) =>
    apiCall<{ success: boolean; task_id: string; message: string }>('/api/generate_digital_human', 'POST', params),

  getTaskStatus: (taskId: string) =>
    apiCall<{ success: boolean; task_id: string; status: string; progress: number; video_path?: string }>(`/api/task_status/${taskId}`, 'GET'),

  downloadFile: (path: string, type: string) => `${API_BASE}/api/download_file?path=${encodeURIComponent(path)}&type=${type}`,
  downloadTaskDir: (taskDir: string) => `${API_BASE}/api/download_task_dir?taskDir=${encodeURIComponent(taskDir)}`,
};

// Other API
export const miscApi = {
  getStatus: () => apiCall<{ status: string; service: string }>('/api/status', 'GET'),

  saveContent: (params: { script_path: string; content: string }) =>
    apiCall<{ success: boolean; script_path: string }>('/api/save', 'POST', params),

  uploadImage: async (file: File): Promise<{ success: boolean; url?: string; error?: string }> => {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE}/api/upload_image`, {
      method: 'POST',
      body: formData,
    });

    return response.json();
  },
};

// Export all
export const api = {
  search: searchApi,
  process: processApi,
  deepMode: deepModeApi,
  video: videoApi,
  misc: miscApi,
};

export default api;