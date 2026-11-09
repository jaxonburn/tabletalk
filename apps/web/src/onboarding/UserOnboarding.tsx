import { useEffect, useRef } from "react";
import markup from "./onboarding.html?raw";
import { initOnboarding } from "./onboarding";
import "./onboarding.css";

const PAGE_TITLE = "TableTalk — Attendee onboarding";
const THEME_COLOR = "#05070a";

/**
 * The attendee onboarding prototype, served at /user-onboarding.
 *
 * The markup is the template's HTML, bundled with the app (no user input), and
 * the template's script in ./onboarding.ts drives everything inside it. React
 * only mounts it, so the page stays as close to the template as possible.
 */
export default function UserOnboarding() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = container.current;
    const root = node?.querySelector<HTMLElement>("#tabletalk-auto");
    if (!node || !root) return;

    const previousTitle = document.title;
    const themeColor = document.querySelector<HTMLMetaElement>(
      'meta[name="theme-color"]',
    );
    const previousThemeColor = themeColor?.content;
    document.title = PAGE_TITLE;
    if (themeColor) themeColor.content = THEME_COLOR;

    const teardown = initOnboarding(root);
    return () => {
      teardown();
      // Start again from the template's markup if the page mounts again.
      node.innerHTML = markup;
      document.title = previousTitle;
      if (themeColor && previousThemeColor !== undefined) {
        themeColor.content = previousThemeColor;
      }
    };
  }, []);

  return <div ref={container} dangerouslySetInnerHTML={{ __html: markup }} />;
}
