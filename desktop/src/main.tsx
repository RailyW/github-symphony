import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// 函数说明：挂载 React 应用。
function mount(): void {
  const root = document.getElementById("root");
  if (!root) {
    throw new Error("Missing #root element");
  }

  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

mount();
