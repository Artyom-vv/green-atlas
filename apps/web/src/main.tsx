import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import '@green/ui/styles.css';
import 'ol/ol.css';
import './styles.css';
import { App } from './app/App';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 5_000 }, mutations: { retry: 0 } },
});
const router = createBrowserRouter([{ path: '*', element: <App /> }], { future: { v7_relativeSplatPath: true } });

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </QueryClientProvider>
  </React.StrictMode>,
);
