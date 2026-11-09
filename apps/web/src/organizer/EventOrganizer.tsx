import { useEffect, useRef } from "react";
import markup from "./organizer.html?raw";
import { initOrganizer } from "./organizer";
import "./organizer.css";

const PAGE_TITLE = "TableTalk Organizer";
const THEME_COLOR = "#05070a";

/**
 * The event organizer prototype, served at /event-organizer.
 *
 * The markup is the template's HTML, bundled with the app (no user input), and
 * the template's script in ./organizer.ts drives everything inside it. React
 * only mounts it, so the page stays as close to the template as possible.
 */
export default function EventOrganizer() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = container.current;
    const root = node?.querySelector<HTMLElement>("#tt-admin");
    if (!node || !root) return;

    const previousTitle = document.title;
    const themeColor = document.querySelector<HTMLMetaElement>(
      'meta[name="theme-color"]',
    );
    const previousThemeColor = themeColor?.content;
    document.title = PAGE_TITLE;
    if (themeColor) themeColor.content = THEME_COLOR;

    const teardown = initOrganizer(root);
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
