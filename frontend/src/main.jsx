import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import { DatesProvider } from '@mantine/dates';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import 'dayjs/locale/ru';
import App from './App';

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <MantineProvider forceColorScheme="light">
      <DatesProvider settings={{ locale: 'ru' }}>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </DatesProvider>
    </MantineProvider>
  </React.StrictMode>,
);
