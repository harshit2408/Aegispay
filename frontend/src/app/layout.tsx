import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AegisPay · AG Studio spike",
  description: "SPIKE-7: official PayPal + AG Grid starter, React widget and scripted agent.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
