"use client";

import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

/**
 * Crossfades League tab content on route change instead of letting Next
 * hard-swap it. Keyed on pathname: clicking a League tab fades the old
 * tab's content out and fades the new tab's content (its loading.tsx
 * skeleton, or real content if already cached/fast) in, using the same
 * duration/easing as revealItemVariants in ui/motion.tsx so it reads as
 * one motion language. Deliberately does not try to also animate the
 * later skeleton-to-real-content swap within a single pathname -- the
 * skeleton's own animate-pulse already signals activity there, and
 * reaching into every page component to add a second animation for that
 * swap isn't worth the added coupling.
 */
export function PageTransition({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();

  if (reduceMotion) return children;

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div
        key={pathname}
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -12 }}
        transition={{ duration: 0.3, ease: "easeOut" }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
