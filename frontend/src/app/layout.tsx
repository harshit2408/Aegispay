import type { Metadata } from "next";
import Link from "next/link";
import { JudgeGuide } from "./judge-guide";
import { NavigationLinks } from "./navigation-links";
import "./globals.css";

export const metadata: Metadata = {
  title: "AegisPay · Payables workspace",
  description: "AegisPay payables cockpit, case workspace and supplier simulator preview.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">Skip to main content</a>
        <div className="replay-banner">REPLAY · Development preview · Synthetic data only</div>
        <header className="app-header">
          <Link className="brand" href="/">AEGISPAY <span>Payables workspace</span></Link>
          <NavigationLinks />
        </header>
        <div className="workspace">
          <p className="preview-status">Frontend preview · Backend actions are not connected</p>
          <JudgeGuide />
          <main id="main" tabIndex={-1}>{children}</main>
        </div>
      </body>
    </html>
  );
}
