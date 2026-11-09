import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { pageForPath, type PageName } from "./routes";

// Each page brings its own stylesheet, and the pages style the same elements
// (body, buttons, inputs) differently, so they load as separate chunks.
const pages: Record<
  PageName,
  React.LazyExoticComponent<() => React.ReactNode>
> = {
  landing: lazy(() => import("./landing/LandingPage")),
  attendee: lazy(() => import("./onboarding/UserOnboarding")),
  organizer: lazy(() => import("./organizer/EventOrganizer")),
  host: lazy(() => import("./HostApp")),
  classic: lazy(() => import("./App")),
};

const Page = pages[pageForPath(window.location.pathname)];

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </React.StrictMode>,
);
