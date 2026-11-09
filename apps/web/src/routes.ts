export type PageName =
  "landing" | "attendee" | "organizer" | "host" | "classic";

/** The attendee app's address. The landing page and other pages link here. */
export const ATTENDEE_PATH = "/user-onboarding";
export const ORGANIZER_PATH = "/organizer-workflow";

/**
 * Which page a path shows. Each page is a full page load with its own
 * stylesheet; unknown paths show the landing page.
 */
export function pageForPath(pathname: string): PageName {
  switch (pathname.replace(/\/+$/, "")) {
    case ATTENDEE_PATH:
      return "attendee";
    case ORGANIZER_PATH:
    case "/event-organizer":
      return "organizer";
    case "/host":
      return "host";
    case "/classic":
      return "classic";
    default:
      return "landing";
  }
}
