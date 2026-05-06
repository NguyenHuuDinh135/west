import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/backend/:path*',
        destination: 'http://west-alb-1710386774.us-east-1.elb.amazonaws.com/:path*',
      },
    ];
  },
};

export default nextConfig;
