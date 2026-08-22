/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  // infra/cloud/Dockerfile.frontend's final stage copies .next/standalone -
  // a self-contained server bundle with only the node_modules it actually
  // needs, instead of the full node_modules tree, for a much smaller final
  // image (see that Dockerfile's header).
  output: "standalone"
};

export default nextConfig;

