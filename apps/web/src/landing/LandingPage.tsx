import { useEffect } from "react";
import markup from "./landing.html?raw";
import "./landing.css";

const PAGE_TITLE = "TableTalk — Better conversations start here";
const THEME_COLOR = "#090b12";

/**
 * The landing page, served at /. Its two buttons lead to the attendee app
 * (/user-onboarding) and the organizer workflow (/organizer-workflow).
 *
 * The markup is the template's HTML, bundled with the app (no user input). It
 * needs no script: the animation is all CSS, and the buttons are plain links.
 */
export default function LandingPage() {
  useEffect(() => {
    const previousTitle = document.title;
    const themeColor = document.querySelector<HTMLMetaElement>(
      'meta[name="theme-color"]',
    );
    const previousThemeColor = themeColor?.content;
    document.title = PAGE_TITLE;
    if (themeColor) themeColor.content = THEME_COLOR;
    return () => {
      document.title = previousTitle;
      if (themeColor && previousThemeColor !== undefined) {
        themeColor.content = previousThemeColor;
      }
    };
  }, []);

  return <div dangerouslySetInnerHTML={{ __html: markup }} />;
}
