import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";

// Each page brings its own stylesheet, and the pages style the same elements
// (body, buttons, inputs) differently, so they load as separate chunks.
const App = lazy(() => import("./App"));
const HostApp = lazy(() => import("./HostApp"));
const UserOnboarding = lazy(() => import("./onboarding/UserOnboarding"));
const EventOrganizer = lazy(() => import("./organizer/EventOrganizer"));

// The onboarding flow is the attendee app; /classic keeps the original single page.
// The organizer console is the host app; /host/review keeps the detailed review.
const ORGANIZER = new Set(["/host", "/organizer-workflow", "/event-organizer"]);
const path = window.location.pathname.replace(/\/+$/, "");
const Page = ORGANIZER.has(path)
  ? EventOrganizer
  : path === "/host/review"
    ? HostApp
    : path === "/classic"
      ? App
      : UserOnboarding;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </React.StrictMode>,
);
