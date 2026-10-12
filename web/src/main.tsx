import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App.tsx";
import { ConfirmProvider } from "./confirm.tsx";
import { SessionProvider } from "./session.tsx";
import { ToastProvider } from "./toast.tsx";

const root = document.getElementById("root");
if (!root) throw new Error("the page has no #root element");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <ToastProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </ToastProvider>
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
