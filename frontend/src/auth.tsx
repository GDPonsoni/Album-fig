import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { ApiError, api, post, type User } from './api';
import { Splash } from './components/ui';

interface AuthState {
  user: User | null | undefined;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      setUser(await api<User>('/me'));
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) setUser(null);
      else setUser((current) => current ?? null);
    }
  }, []);

  const signOut = useCallback(async () => {
    await post('/auth/logout');
    setUser(null);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return <AuthContext.Provider value={{ user, refresh, signOut }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

// Destino guardado enquanto a pessoa entra ou confirma o e-mail (o link de confirmação
// abre em outra aba, então o estado da navegação não basta). Só caminhos internos.
const NEXT_KEY = 'album.next';
const internal = (path: string | null | undefined): path is string => !!path && path.startsWith('/') && !path.startsWith('//');

export function rememberNext(path: string) {
  try {
    if (internal(path) && path !== '/') localStorage.setItem(NEXT_KEY, path);
  } catch {
    // Sem armazenamento (modo privado): o destino só vale nesta navegação.
  }
}

export function storedNext(): string | null {
  try {
    const path = localStorage.getItem(NEXT_KEY);
    return internal(path) ? path : null;
  } catch {
    return null;
  }
}

export function clearNext() {
  try {
    localStorage.removeItem(NEXT_KEY);
  } catch {
    // Nada a limpar.
  }
}

export function RequireAuth({ children, admin, staff }: { children: ReactNode; admin?: boolean; staff?: boolean }) {
  const { user } = useAuth();
  const location = useLocation();
  const here = location.pathname + location.search;
  useEffect(() => {
    // Chegou ao destino guardado: ele não deve valer para um login futuro.
    if (user && storedNext() === here) clearNext();
  }, [user, here]);
  if (user === undefined) return <Splash />;
  if (user === null) {
    rememberNext(here);
    return <Navigate to="/entrar" replace state={{ next: here }} />;
  }
  if (admin && !user.is_admin) return <Navigate to="/" replace />;
  if (staff && !user.is_admin && !user.reviewer_chapters) return <Navigate to="/" replace />;
  return <>{children}</>;
}
