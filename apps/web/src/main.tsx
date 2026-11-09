import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";

// Each page brings its own stylesheet, and the pages style the same elements
// (body, buttons, inputs) differently, so they load as separate chunks.
const App = lazy(() => import("./App"));
const HostApp = lazy(() => import("./HostApp"));
const UserOnboarding = lazy(() => import("./onboarding/UserOnboarding"));

// The onboarding flow is the attendee app; /classic keeps the original single page.
const path = window.location.pathname.replace(/\/+$/, "");
const Page =
  path === "/host" ? HostApp : path === "/classic" ? App : UserOnboarding;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </React.StrictMode>,
);
