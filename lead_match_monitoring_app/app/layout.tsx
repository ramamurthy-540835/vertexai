import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lead Match Monitor",
  description: "Costco Lead Match pipeline monitoring dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <header className="header-bar">
          <div className="header-inner">
            <div className="header-left">
              <Link href="/">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src="/image/Costco-Logo-Registered.png"
                  alt="Costco"
                  className="logo"
                />
              </Link>
              <div className="header-title">
                <span className="header-app-name">
                  Lead Match Pipeline Monitor
                </span>
              </div>
            </div>
          </div>
        </header>

        <nav className="nav-strip">
          <div className="nav-inner">
            <Link href="/" className="nav-link active">
              Monitor
            </Link>
            <Link href="/api/snapshot" className="nav-link">
              Snapshot JSON
            </Link>
          </div>
        </nav>

        <main className="main">{children}</main>

        <footer className="footer">
          Internal Costco Lead Match pipeline monitor — not an official Costco
          consumer property.
        </footer>
      </body>
    </html>
  );
}
