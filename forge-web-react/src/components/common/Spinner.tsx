export function Spinner({ size = 'normal' }: { size?: 'small' | 'normal' | 'large' }) {
  const sizeClass = size === 'small' ? 'w-3 h-3' : size === 'large' ? 'w-8 h-8' : 'w-4 h-4';

  return (
    <div className={`${sizeClass} border-2 border-gray-300 border-t-primary rounded-full animate-spin`}></div>
  );
}

export function LoadingOverlay({ message = '加载中...' }: { message?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 p-4">
      <Spinner size="normal" />
      <span className="text-gray-600">{message}</span>
    </div>
  );
}