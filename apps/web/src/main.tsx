import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";

// Each page brings its own stylesheet, and the two style the same elements
// (body, buttons, inputs) differently, so they load as separate chunks.
const App = lazy(() => import("./App"));
const UserOnboarding = lazy(() => import("./onboarding/UserOnboarding"));

const Page = /^\/user-onboarding\/?$/.test(window.location.pathname)
  ? UserOnboarding
  : App;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </React.StrictMode>,
);
