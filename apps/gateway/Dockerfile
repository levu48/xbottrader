# Multi-stage build for the Node Gateway.
#
# Build context MUST be the monorepo root (the pnpm workspace needs all of
# packages/shared visible). Build with:
#
#     docker build -f apps/gateway/Dockerfile -t xbt-gateway .

FROM node:22-slim AS deps
WORKDIR /repo
RUN corepack enable   # picks pnpm version from package.json#packageManager

COPY pnpm-workspace.yaml package.json ./
COPY apps/gateway/package.json apps/gateway/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN pnpm install --prod=false --no-frozen-lockfile


FROM node:22-slim AS runtime
WORKDIR /repo
ENV NODE_ENV=production
RUN corepack enable   # picks pnpm version from package.json#packageManager

COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/gateway/node_modules ./apps/gateway/node_modules
COPY --from=deps /repo/packages/shared/node_modules ./packages/shared/node_modules

COPY pnpm-workspace.yaml package.json tsconfig.base.json ./
COPY apps/gateway ./apps/gateway
COPY packages/shared ./packages/shared

WORKDIR /repo/apps/gateway
EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:4000/healthz', r => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

# tsx runs TypeScript directly — sufficient for our scale, avoids a separate
# tsc build step that would need workspace project references to compile
# @xbt/shared. Swap for `node dist/server.js` when we add a bundler.
CMD ["pnpm", "exec", "tsx", "src/server.ts"]
