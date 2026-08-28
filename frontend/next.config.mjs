/** @type {import('next').NextConfig} */
const nextConfig = {
  poweredByHeader: false,
  // infra/cloud/Dockerfile.frontend's final stage copies .next/standalone -
  // a self-contained server bundle with only the node_modules it actually
  // needs, instead of the full node_modules tree, for a much smaller final
  // image (see that Dockerfile's header). Netlify's own Next.js Runtime
  // (auto-applied on a git-connected deploy - see DEPLOYMENT.md) traces and
  // packages the build output itself and does not want `output: "standalone"`
  // set - the two deployment paths want different shapes from the same
  // `next build`. NEXT_OUTPUT_STANDALONE=true is set only inside
  // infra/cloud/Dockerfile.frontend's build stage; a Netlify build never
  // sets it, so this falls back to Next's normal output there.
  output: process.env.NEXT_OUTPUT_STANDALONE === "true" ? "standalone" : undefined
};

export default nextConfig;

