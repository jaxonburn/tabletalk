import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import HostApp from "./HostApp";
import "./styles.css";

const isHostView = window.location.pathname.replace(/\/+$/, "") === "/host";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>{isHostView ? <HostApp /> : <App />}</React.StrictMode>,
);
