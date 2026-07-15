/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // PWA service worker (kiosk offline-first) is wired in S7; kept plain here.
  async headers() {
    return [];
  },
};

export default nextConfig;
