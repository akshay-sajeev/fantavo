"use client";

import { useRef } from "react";
import type { PointerEvent, ReactNode } from "react";
import {
  motion,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
  type Variants,
} from "framer-motion";
import { cn } from "@/lib/utils";

/**
 * Shared entrance-animation variants -- exported so pages that can't use the
 * <Reveal>/<RevealItem> div wrappers directly (e.g. StandingsTable, which
 * needs motion.tbody/motion.tr to stagger real table rows) still get the
 * exact same timing/easing rather than duplicating magic numbers.
 */
export const revealContainerVariants: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08 } },
};

export const revealItemVariants: Variants = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: "easeOut" } },
};

/**
 * Stagger container for a page-level grid of cards. Wrap the grid in
 * <Reveal>, wrap each direct card in <RevealItem>. Respects
 * prefers-reduced-motion by rendering a plain div with no animation.
 */
export function Reveal({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      className={className}
      variants={revealContainerVariants}
      initial={reduceMotion ? false : "hidden"}
      animate="show"
    >
      {children}
    </motion.div>
  );
}

export function RevealItem({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      className={className}
      variants={revealItemVariants}
      initial={reduceMotion ? false : undefined}
    >
      {children}
    </motion.div>
  );
}

const MAX_TILT_DEG = 4;

/**
 * Opt-in wrapper for a single featured/interactive card: subtle
 * mouse-tracked tilt (transform-only, capped, no layout shift) plus a
 * glow-ring hover shadow. Not applied blanket to every card -- only ones
 * meant to feel interactive/featured (e.g. the dashboard's current-matchup
 * hero card).
 */
export function TiltCard({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const x = useMotionValue(0.5);
  const y = useMotionValue(0.5);
  const springX = useSpring(x, { stiffness: 200, damping: 20 });
  const springY = useSpring(y, { stiffness: 200, damping: 20 });
  const rotateX = useTransform(springY, [0, 1], [MAX_TILT_DEG, -MAX_TILT_DEG]);
  const rotateY = useTransform(springX, [0, 1], [-MAX_TILT_DEG, MAX_TILT_DEG]);

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    const bounds = ref.current?.getBoundingClientRect();
    if (!bounds) return;
    x.set((event.clientX - bounds.left) / bounds.width);
    y.set((event.clientY - bounds.top) / bounds.height);
  }

  function handlePointerLeave() {
    x.set(0.5);
    y.set(0.5);
  }

  return (
    <motion.div
      ref={ref}
      className={cn(
        "[perspective:1000px] rounded-xl transition-shadow duration-300 hover:shadow-[var(--shadow-glow-primary-lg)]",
        className
      )}
      onPointerMove={reduceMotion ? undefined : handlePointerMove}
      onPointerLeave={reduceMotion ? undefined : handlePointerLeave}
      style={{
        rotateX: reduceMotion ? 0 : rotateX,
        rotateY: reduceMotion ? 0 : rotateY,
        transformPerspective: 1000,
        transformStyle: "preserve-3d",
      }}
    >
      {children}
    </motion.div>
  );
}

/**
 * Animates a 0-1 fraction (already computed by the API, never derived here)
 * from 0 to its real value as a glowing horizontal meter. Additive only --
 * callers keep rendering the formatted percentage text alongside this; the
 * meter never replaces the number.
 */
export function AnimatedMeter({
  value,
  label,
  glow = "primary",
}: {
  value: number;
  label: string;
  glow?: "primary" | "accent";
}) {
  const reduceMotion = useReducedMotion();
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const glowShadow =
    glow === "accent" ? "var(--shadow-glow-accent)" : "var(--shadow-glow-primary)";
  const barColor = glow === "accent" ? "bg-brand-accent" : "bg-primary";

  return (
    <div
      className="h-1.5 w-16 shrink-0 overflow-hidden rounded-full bg-muted"
      role="meter"
      aria-label={label}
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <motion.div
        className={cn("h-full rounded-full", barColor)}
        style={{ boxShadow: glowShadow }}
        initial={reduceMotion ? false : { width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: reduceMotion ? 0 : 0.6, ease: "easeOut" }}
      />
    </div>
  );
}
