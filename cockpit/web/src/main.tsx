import React from "react";
import { createRoot } from "react-dom/client";
import App from "./app/App";

// One app, one entry: App.tsx owns the global stylesheets, routes.ts
// the hash routes — `/` and `#/w` are Home, `#/w/<id>` a workspace.
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
