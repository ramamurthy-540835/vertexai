import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { NavLinks } from "@/components/NavLinks";
import { WarehouseSwitcher } from "@/components/WarehouseSwitcher";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lead Match Reporting",
  description: "Costco Lead-to-POS match reporting tool",
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
                  Lead &rarr; POS Match Reporting
                </span>
              </div>
            </div>
            <div className="header-right">
              <Suspense>
                <WarehouseSwitcher />
              </Suspense>
            </div>
          </div>
        </header>

        <nav className="nav-strip">
          <div className="nav-inner">
            <NavLinks />
          </div>
        </nav>

        <main className="main">{children}</main>

        <footer className="footer">
          Internal Costco Lead-to-POS reporting tool — not an official Costco
          consumer property.
        </footer>
      </body>
    </html>
  );
}
