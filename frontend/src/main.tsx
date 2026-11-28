import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { AppProviders } from "./app/providers";
import { AppRouter } from "./app/router";
import "./styles/visual-tokens.css";
import "./styles/app.css";

const container = document.getElementById("root");
if (!container) throw new Error("缺少 #root 容器。");

createRoot(container).render(
  <BrowserRouter>
    <AppProviders>
      <AppRouter />
    </AppProviders>
  </BrowserRouter>,
);
