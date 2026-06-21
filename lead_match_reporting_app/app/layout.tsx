import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lead Match Reporting",
  description: "Secure reporting access for lead match results in GCS",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <main className="shell">
          <nav className="nav">
            <Link className="brand" href="/">
              Lead Match Reporting
            </Link>
            <div className="links">
              <Link className="pill" href="/">
                Latest
              </Link>
              <Link className="pill" href="/search">
                Search
              </Link>
              <Link className="pill" href="/graph">
                Graph
              </Link>
              <Link className="pill" href="/talk-with-data">
                Talk with Data
              </Link>
            </div>
          </nav>
          {children}
        </main>
      </body>
    </html>
  );
}
