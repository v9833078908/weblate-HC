/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false,
  // The whole console is a client-side SPA rendered from app/page.js.
  // Rewrite deep links (project/run/dev/email routes) back to it so hard
  // refreshes on a deep URL still resolve. /api/* is untouched.
  async rewrites() {
    return {
      beforeFiles: [
        { source: "/projects/:path*", destination: "/" },
        { source: "/runs/:path*", destination: "/" },
        { source: "/dev/:path*", destination: "/" },
        { source: "/emails/:path*", destination: "/" },
        { source: "/advanced", destination: "/" },
      ],
    };
  },
};

module.exports = nextConfig;
