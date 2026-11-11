import { useCallback, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, post } from '../api';

/**
 * Ações do superadmin que pedem o segundo fator. `run` tenta a ação; se o servidor responder que
 * falta confirmar o código, abre o pedido de código e repete a ação depois de confirmado.
 */
export function useMfa(): { run: <T>(action: () => Promise<T>) => Promise<T>; prompt: ReactNode } {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const waiting = useRef<{ resolve: () => void; reject: (error: Error) => void } | null>(null);

  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T> => {
    try {
      return await action();
    } catch (err) {
      if (!(err instanceof ApiError) || err.mfa !== 'verify') throw err;
      await new Promise<void>((resolve, reject) => {
        waiting.current = { resolve, reject };
        setCode('');
        setError('');
        setOpen(true);
      });
      return action();
    }
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await post('/auth/2fa/verify', { code });
      setOpen(false);
      waiting.current?.resolve();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const cancel = () => {
    setOpen(false);
    waiting.current?.reject(new Error('Ação cancelada: o código de duas etapas não foi confirmado.'));
  };

  const prompt = open ? (
    <div className="overlay mfa-prompt" role="dialog" aria-label="Código de duas etapas">
      <form className="panel paper" onSubmit={submit}>
        <h2>Código de duas etapas</h2>
        <p className="muted">Digite o código do app autenticador (ou um código de recuperação).</p>
        <input
          aria-label="Código"
          autoFocus
          autoComplete="one-time-code"
          inputMode="text"
          required
          minLength={6}
          maxLength={20}
          value={code}
          onChange={(event) => setCode(event.target.value)}
        />
        {error ? <p className="message message-error" role="alert">{error}</p> : null}
        <button className="btn btn-primary" disabled={busy}>Confirmar</button>
        <button type="button" className="btn" onClick={cancel}>Cancelar</button>
      </form>
    </div>
  ) : null;
  return { run, prompt };
}

/** Mensagem de erro que, quando falta ativar o 2FA, leva à página Conta. */
export function MfaError({ error }: { error: Error | null }) {
  if (!error) return null;
  return (
    <p className="message message-error" role="alert">
      {error.message} {error instanceof ApiError && error.mfa === 'setup' ? <Link to="/conta">Ativar agora</Link> : null}
    </p>
  );
}
