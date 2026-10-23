import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { TaskWidget } from "./components/TaskWidget";
import "./styles.css";

const isTaskWidget = new URLSearchParams(window.location.search).get("widget") === "1";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isTaskWidget ? <TaskWidget /> : <App />}
  </React.StrictMode>,
);
