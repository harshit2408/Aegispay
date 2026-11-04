import type { NextConfig } from "next";

// Browser requests stay on the frontend origin so Strict session cookies work.
const upstream = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const nextConfig: NextConfig = {
  output: "standalone",
  experimental: { proxyClientMaxBodySize: "11mb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${upstream}/api/:path*` }];
  },
};

export default nextConfig;
