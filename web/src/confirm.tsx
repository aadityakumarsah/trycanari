import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export interface Question {
  title: string;
  body?: string;
  /** What the confirming button says. */
  confirm?: string;
  cancel?: string;
  /** For something that cannot be undone: the confirming button turns red. */
  danger?: boolean;
}

type Ask = (question: Question) => Promise<boolean>;

const Context = createContext<Ask>(() => Promise.resolve(false));

/** The site's own "are you sure?", in place of the browser's grey box. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [question, setQuestion] = useState<Question | null>(null);
  const answer = useRef<(yes: boolean) => void>(() => undefined);
  const dialog = useRef<HTMLDialogElement>(null);

  const ask = useCallback<Ask>((asked) => {
    // A question still open is answered "no" by the one that replaces it.
    answer.current(false);
    setQuestion(asked);
    return new Promise<boolean>((resolve) => {
      answer.current = resolve;
    });
  }, []);

  useEffect(() => {
    if (question && !dialog.current?.open) dialog.current?.showModal();
  }, [question]);

  function settle(yes: boolean) {
    answer.current(yes);
    answer.current = () => undefined;
    dialog.current?.close();
    setQuestion(null);
  }

  return (
    <Context value={ask}>
      {children}
      {question && (
        <dialog
          ref={dialog}
          className={question.danger ? "confirm danger" : "confirm"}
          aria-labelledby="confirm-title"
          onCancel={(event) => {
            event.preventDefault();
            settle(false);
          }}
          onClick={(event) => {
            // The dialog element itself is the backdrop; its contents sit in the card.
            if (event.target === event.currentTarget) settle(false);
          }}
        >
          <div className="confirm-card">
            <div className="confirm-bot">
              <img src="/app/brand/mascot.png" alt="" width={63} height={54} />
            </div>
            <h2 id="confirm-title">{question.title}</h2>
            {question.body && <p>{question.body}</p>}
            <div className="confirm-actions">
              {/* Cancel comes first so that Enter on a fresh dialog never confirms. */}
              <button type="button" className="confirm-no" autoFocus onClick={() => settle(false)}>
                {question.cancel ?? "Cancel"}
              </button>
              <button type="button" className="confirm-yes" onClick={() => settle(true)}>
                {question.confirm ?? "OK"}
              </button>
            </div>
          </div>
        </dialog>
      )}
    </Context>
  );
}

export function useConfirm(): Ask {
  return useContext(Context);
}
