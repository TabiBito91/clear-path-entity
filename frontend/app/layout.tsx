import type { Metadata } from "next";
import { Geist } from "next/font/google";
import "./globals.css";

const geist = Geist({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Clear Path Entity",
  description: "Business name availability search across U.S. states and USPTO",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${geist.className} bg-neutral-50 min-h-screen antialiased`}>
        {children}
      </body>
    </html>
  );
}
