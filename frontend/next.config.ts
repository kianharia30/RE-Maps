import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Next 16 blocks cross-origin requests for dev-server assets by default,
  // which breaks loading over 127.0.0.1 when the page was opened as localhost
  // (or vice versa) and silently 403s the dynamic-import chunks.
  allowedDevOrigins: ["127.0.0.1", "localhost", "0.0.0.0"],
  // The API base is read at build time for the browser bundle; everything
  // secret stays server-side (see backend/app/config.py).
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000",
    NEXT_PUBLIC_MAP_STYLE:
      process.env.NEXT_PUBLIC_MAP_STYLE ?? "https://tiles.openfreemap.org/styles/liberty",
  },
};

export default nextConfig;
