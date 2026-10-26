export const navigation = [
  { href: "/", section: "/", label: "Cockpit" },
  { href: "/cases/preview", section: "/cases", label: "Case workspace" },
  { href: "/mandate", section: "/mandate", label: "Mandate studio" },
  { href: "/vendor", section: "/vendor", label: "Supplier simulator" },
] as const;

export function isCurrentSection(pathname: string, section: string): boolean {
  return pathname === section || (section !== "/" && pathname.startsWith(`${section}/`));
}
