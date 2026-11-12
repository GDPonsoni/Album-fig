import '@fontsource/bowlby-one';
import '@fontsource-variable/figtree';
import { StrictMode, Suspense, lazy } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, RequireAuth } from './auth';
import { Layout } from './components/Layout';
import { Splash } from './components/ui';
import { initFx } from './fx';
import { AccountPage } from './pages/Account';
import { AlbumByIdPage, AlbumPage } from './pages/Album';
import { ConfirmPage, ForgotPage, LoginPage, RegisterPage, ResetPage } from './pages/Auth';
import { PacksPage } from './pages/Packs';
import { RedeemLinkPage } from './pages/Redeem';
import { TradeHome, TradeSession } from './pages/Trade';
import './styles.css';

const AdminPage = lazy(() => import('./pages/Admin'));
const AdminAlbumsPage = lazy(() => import('./pages/AdminAlbums'));
const AdminReviewPage = lazy(() => import('./pages/AdminReview'));
const EditorHome = lazy(() => import('./pages/Editor').then((m) => ({ default: m.EditorHome })));
const EditorChapter = lazy(() => import('./pages/Editor').then((m) => ({ default: m.EditorChapter })));

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
            path="/admin/albums"
            element={
              <RequireAuth admin>
                <Suspense fallback={<Splash />}>
                  <AdminAlbumsPage />
                </Suspense>
              </RequireAuth>
            }
          />
          <Route
            path="/admin/revisoes"
            element={
              <RequireAuth admin>
                <Suspense fallback={<Splash />}>
                  <AdminReviewPage />
                </Suspense>
              </RequireAuth>
            }
          />
          {/* A área do editor só abre para quem tem o papel: o servidor responde 404 para os demais. */}
          <Route path="/editor" element={<RequireAuth><Suspense fallback={<Splash />}><EditorHome /></Suspense></RequireAuth>} />
          <Route path="/editor/:capitulo" element={<RequireAuth><Suspense fallback={<Splash />}><EditorChapter /></Suspense></RequireAuth>} />
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
                <Layout panes={{ '/': <AlbumPage />, '/pacotes': <PacksPage />, '/trocar': <TradeHome />, '/conta': <AccountPage /> }} />
              </RequireAuth>
            }
          >
            <Route path="/" />
            <Route path="/albuns" element={<Navigate to="/conta" replace />} />
            <Route path="/albuns/:id" element={<AlbumByIdPage />} />
            <Route path="/pacotes" />
            <Route path="/r/:codigo" element={<RedeemLinkPage />} />
            <Route path="/trocar" />
            <Route path="/trocar/:token" element={<TradeSession />} />
            <Route path="/conta" />
          </Route>
          <Route path="*" element={<LoginPage />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
