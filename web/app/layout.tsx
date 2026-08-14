import type { Metadata } from "next";
import { Fira_Code, Fira_Sans, Orbitron } from "next/font/google";
import Image from "next/image";
import Link from "next/link";
import "./globals.css";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RouteProgress } from "@/components/shared/route-progress";

const firaSans = Fira_Sans({
  variable: "--font-body",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
});

const firaCode = Fira_Code({
  variable: "--font-heading",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

// Header wordmark only -- not the app's general heading font (that's
// firaCode above, used everywhere else via font-heading). A sci-fi/
// display face reads as "brand mark," not body or section-heading text.
const orbitron = Orbitron({
  variable: "--font-brand",
  subsets: ["latin"],
  weight: ["600", "700", "800"],
});

export const metadata: Metadata = {
  title: "Fantavo",
  description: "Simulation-driven fantasy football analytics",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${firaSans.variable} ${firaCode.variable} ${orbitron.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <TooltipProvider delay={150}>
          <RouteProgress />
          <header className="sticky top-0 z-40 border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
            <div className="flex h-16 items-center justify-start gap-6 px-4">
              <Link
                href="/"
                className="flex items-center gap-3 font-[family-name:var(--font-brand)] text-xl font-bold tracking-wide text-primary uppercase"
              >
                <Image src="/fantavo-logo.svg" alt="" width={40} height={30} className="h-10 w-auto" priority />
                Fantavo
              </Link>
            </div>
          </header>
          <main className="flex flex-1 flex-col">{children}</main>
        </TooltipProvider>
      </body>
    </html>
  );
}
