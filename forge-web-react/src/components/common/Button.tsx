import type { ReactNode } from 'react';

interface ButtonProps {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'secondary' | 'warning';
  size?: 'normal' | 'large';
  disabled?: boolean;
  loading?: boolean;
  className?: string;
}

export function Button({
  children,
  onClick,
  variant = 'primary',
  size = 'normal',
  disabled = false,
  loading = false,
  className = '',
}: ButtonProps) {
  const baseClass = 'btn';
  const variantClass = variant === 'primary' ? 'btn-primary' : variant === 'warning' ? 'bg-warning text-white hover:bg-orange-600' : 'btn-secondary';
  const sizeClass = size === 'large' ? 'btn-large' : '';

  const handleClick = () => {
    console.log('Button clicked', { disabled, loading });
    if (!disabled && !loading && onClick) {
      onClick();
    }
  };

  return (
    <button
      className={`${baseClass} ${variantClass} ${sizeClass} ${className} ${disabled || loading ? 'opacity-50 cursor-not-allowed' : ''}`}
      onClick={handleClick}
      disabled={disabled || loading}
      type="button"
    >
      {loading ? (
        <span className="flex items-center gap-2">
          <span className="spinner"></span>
          加载中...
        </span>
      ) : children}
    </button>
  );
}