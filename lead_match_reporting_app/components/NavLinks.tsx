"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Latest" },
  { href: "/search", label: "Search" },
  { href: "/graph", label: "Graph" },
  { href: "/talk-with-data", label: "Talk with Data" },
];

export function NavLinks() {
  const pathname = usePathname();

  return (
    <>
      {links.map((link) => {
        const active =
          link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`nav-link${active ? " active" : ""}`}
          >
            {link.label}
          </Link>
        );
      })}
    </>
  );
}
