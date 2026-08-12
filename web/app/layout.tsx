import type { Metadata } from "next";
import { Fira_Code, Fira_Sans } from "next/font/google";
import Link from "next/link";
import "./globals.css";
import { TooltipProvider } from "@/components/ui/tooltip";

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

export const metadata: Metadata = {
  title: "Fantavo",
  description: "Simulation-driven fantasy football analytics",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${firaSans.variable} ${firaCode.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <TooltipProvider delay={150}>
          <header className="sticky top-0 z-40 border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
            <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
              <Link href="/" className="font-heading text-lg font-semibold tracking-tight text-primary">
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
