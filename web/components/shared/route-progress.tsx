"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

/**
 * Thin top-of-viewport progress bar that starts the instant a same-page
 * navigation link is clicked, before the destination's data fetch (and
 * therefore its loading.tsx skeleton) has had a chance to appear. Without
 * this there's a beat after clicking a League tab where nothing on screen
 * acknowledges the click, which is what reads as "laggy" rather than
 * "loading". Mounted once in the root layout so it covers every route in
 * the app, not just the League section.
 *
 * There's no App Router navigation-start event to hook, so this listens for
 * clicks on same-origin, unmodified left-clicks on <a> elements (the DOM
 * shape every <Link> renders) and treats pathname changing as "done" --
 * the same technique used by nprogress-style libraries.
 */
export function RouteProgress() {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  const [state, setState] = useState<"idle" | "loading" | "done">("idle");
  const previousPathname = useRef(pathname);

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const anchor = (event.target as Element | null)?.closest("a");
      if (!anchor) return;
      if (anchor.target && anchor.target !== "_self") return;
      if (anchor.hasAttribute("download")) return;

      const href = anchor.getAttribute("href");
      if (!href || href.startsWith("#")) return;

      let url: URL;
      try {
        url = new URL(href, window.location.href);
      } catch {
        return;
      }
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;

      setState("loading");
    }

    // Capture phase, not bubble: next/link's own onClick (attached lower in
    // the tree, during the bubble phase) calls preventDefault() to do its
    // client-side transition, and this listener bails on defaultPrevented
    // (to skip links some other handler already cancelled) -- on the bubble
    // phase that would mean it always sees defaultPrevented=true and never
    // fires. Capture runs before that, while defaultPrevented is still false.
    document.addEventListener("click", handleClick, true);
    return () => document.removeEventListener("click", handleClick, true);
  }, []);

  useEffect(() => {
    if (pathname === previousPathname.current) return;
    previousPathname.current = pathname;
    if (state !== "loading") return;

    setState("done");
    const timeout = setTimeout(() => setState("idle"), 200);
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only pathname should retrigger this
  }, [pathname]);

  if (reduceMotion || state === "idle") return null;

  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden">
      <AnimatePresence>
        <motion.div
          key={state === "loading" ? "loading" : "done"}
          className="h-full bg-primary"
          style={{ boxShadow: "var(--shadow-glow-primary)" }}
          initial={{ width: state === "loading" ? "0%" : undefined }}
          animate={{
            width: state === "loading" ? "80%" : "100%",
            opacity: state === "done" ? 0 : 1,
          }}
          transition={
            state === "loading"
              ? { duration: 4, ease: "easeOut" }
              : { duration: 0.2, ease: "easeOut" }
          }
        />
      </AnimatePresence>
    </div>
  );
}
