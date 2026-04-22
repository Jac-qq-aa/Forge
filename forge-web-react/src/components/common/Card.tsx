import type { ReactNode } from 'react';

interface CardProps {
  title?: string;
  badge?: string;
  children: ReactNode;
  className?: string;
}

export function Card({ title, badge, children, className = '' }: CardProps) {
  return (
    <div className={`card ${className}`}>
      {(title || badge) && (
        <div className="card-header flex items-center mb-4">
          {badge && <span className="step-badge">{badge}</span>}
          {title && <h2 className="text-xl font-semibold text-gray-800">{title}</h2>}
        </div>
      )}
      {children}
    </div>
  );
}