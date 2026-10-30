import React from 'react';
import ReactDOM from 'react-dom/client';
import { MantineProvider } from '@mantine/core';
import { DatesProvider } from '@mantine/dates';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@mantine/core/styles.css';
import '@mantine/dates/styles.css';
import './styles.css';
import 'dayjs/locale/ru';
import App from './App';

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <MantineProvider forceColorScheme="dark" theme={{ primaryColor: 'purple', primaryShade: 6, colors: {
      purple: ['#f5edfc', '#e7d9f4', '#d0b5e7', '#b78fd9', '#a171cc', '#905bc0', '#7f49b4', '#6e3c9f', '#5c3287', '#4c296f'],
      dark: ['#e6e6e6', '#b8b8b8', '#999999', '#707070', '#454545', '#303030', '#1c1c1c', '#0a0a0a', '#070707', '#000000'],
    } }}>
      <DatesProvider settings={{ locale: 'ru' }}>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </DatesProvider>
    </MantineProvider>
  </React.StrictMode>,
);
