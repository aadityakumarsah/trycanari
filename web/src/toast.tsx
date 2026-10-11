import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

type Kind = "ok" | "bad";
interface Toast {
  id: number;
  kind: Kind;
  text: string;
}

const Context = createContext<(text: string, kind?: Kind) => void>(() => undefined);
let next = 1;

/** Brief confirmations in the corner: something was copied, saved or sent. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const show = useCallback((text: string, kind: Kind = "ok") => {
    const id = next++;
    setToasts((current) => [...current.slice(-2), { id, kind, text }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 3200);
  }, []);
  const value = useMemo(() => show, [show]);
  return (
    <Context value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <p key={toast.id} className={`toast toast-${toast.kind}`}>
            {toast.text}
          </p>
        ))}
      </div>
    </Context>
  );
}

export function useToast(): (text: string, kind?: Kind) => void {
  return useContext(Context);
}
