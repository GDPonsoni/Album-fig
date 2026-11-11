import '@fontsource/bowlby-one';
import '@fontsource-variable/figtree';
import { StrictMode, Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider, RequireAuth } from './auth';
import { Layout } from './components/Layout';
import { Splash } from './components/ui';
import { initFx } from './fx';
import { AccountPage } from './pages/Account';
import { AlbumPage } from './pages/Album';
import { ConfirmPage, ForgotPage, LoginPage, RegisterPage, ResetPage } from './pages/Auth';
import { PacksPage } from './pages/Packs';
import { RedeemLinkPage } from './pages/Redeem';
import { TradeHome, TradeSession } from './pages/Trade';
import './styles.css';

const AdminPage = lazy(() => import('./pages/Admin'));

initFx();

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/entrar" element={<LoginPage />} />
          <Route path="/cadastro" element={<RegisterPage />} />
          <Route path="/confirmar" element={<ConfirmPage />} />
          <Route path="/esqueci" element={<ForgotPage />} />
          <Route path="/redefinir" element={<ResetPage />} />
          <Route
            path="/admin/*"
            element={
              <RequireAuth admin>
                <Suspense fallback={<Splash />}>
                  <AdminPage />
                </Suspense>
              </RequireAuth>
            }
          />
          <Route
            element={
              <RequireAuth>
                <Layout />
              </RequireAuth>
            }
          >
            <Route path="/" element={<AlbumPage />} />
            <Route path="/pacotes" element={<PacksPage />} />
            <Route path="/r/:codigo" element={<RedeemLinkPage />} />
            <Route path="/trocar" element={<TradeHome />} />
            <Route path="/trocar/:token" element={<TradeSession />} />
            <Route path="/conta" element={<AccountPage />} />
          </Route>
          <Route path="*" element={<LoginPage />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
