import type { NextConfig } from "next";

// frontend/ and backend/ deploy as two separate services (two Vercel
// projects from the same repo, one rooted at each folder). The browser only
// ever talks to this Next.js app's own origin; Next.js proxies /api/* to
// BACKEND_URL server-side, so no CORS setup is needed on the FastAPI side.
// Locally that's uvicorn on :8000; in production it's set in the frontend
// Vercel project's env vars to the deployed backend project's URL.
const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Allows testing the dev server from another device on the LAN (e.g. a
  // phone at http://<mac's LAN IP>:3000) — otherwise Next.js blocks
  // cross-origin dev requests (HMR, dev assets) from unlisted hosts.
  allowedDevOrigins: ["192.168.1.27"],
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
