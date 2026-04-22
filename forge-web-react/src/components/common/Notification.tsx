import { useEffect, useState } from 'react';
import { useAppStore } from '../../stores/appStore';

export function Notification() {
  const { notification, clearNotification } = useAppStore();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (notification) {
      setVisible(true);
      const timer = setTimeout(() => {
        setVisible(false);
        clearNotification();
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [notification, clearNotification]);

  if (!notification || !visible) return null;

  const typeClass = {
    success: 'notification-success',
    error: 'notification-error',
    info: 'notification-info',
  }[notification.type];

  return (
    <div className={`notification ${typeClass}`}>
      {notification.message}
    </div>
  );
}