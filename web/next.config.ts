import type { NextConfig } from "next";

const config: NextConfig = {
  // The prod image copies .next/standalone; without this that directory is
  // never emitted and docker/web.Dockerfile's prod stage fails.
  output: "standalone",
  images: {
    // Product images are third-party URLs on hosts we do not control. Running
    // them through the optimizer puts an unbounded transform workload on a
    // 4 GB box (spec 12.5), so images are plain <img> with loading="lazy".
    unoptimized: true,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_INTERNAL_URL ?? "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default config;
