import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'motion/react';
import dayjs from 'dayjs';
import 'dayjs/locale/ru';
import './styles.css';
import App from './App';
import { TooltipProvider } from './components/ui/tooltip';

dayjs.locale('ru');

// После обновления сайта открытая вкладка может запрашивать старые JS-файлы.
// Перезагружаем её один раз, чтобы получить ссылки на новую версию.
window.addEventListener('vite:preloadError', (event) => {
  const key = 'page-reloaded-after-update';
  const previous = Number(sessionStorage.getItem(key) || 0);
  if (Date.now() - previous < 60000) return;
  sessionStorage.setItem(key, String(Date.now()));
  event.preventDefault();
  window.location.reload();
});

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <TooltipProvider delayDuration={200}>
          <App />
        </TooltipProvider>
      </MotionConfig>
    </QueryClientProvider>
  </React.StrictMode>,
);
