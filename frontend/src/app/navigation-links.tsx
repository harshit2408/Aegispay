"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { isCurrentSection, navigation } from "./navigation";

export function NavigationLinks() {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary">
      {navigation.map(({ href, section, label }) => (
        <Link key={href} href={href} aria-current={isCurrentSection(pathname, section) ? "page" : undefined}>
          {label}
        </Link>
      ))}
    </nav>
  );
}
