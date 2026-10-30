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

export function RequireAuth({ children, admin }: { children: ReactNode; admin?: boolean }) {
  const { user } = useAuth();
  const location = useLocation();
  if (user === undefined) return <Splash />;
  if (user === null) return <Navigate to="/entrar" replace state={{ next: location.pathname + location.search }} />;
  if (admin && !user.is_admin) return <Navigate to="/" replace />;
  return <>{children}</>;
}
