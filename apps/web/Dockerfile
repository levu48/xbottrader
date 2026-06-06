# Web (Next.js) container.
#
# Build context MUST be the monorepo root (the pnpm workspace needs
# packages/shared visible — the buildpack can't resolve `workspace:*`). Build:
#
#     docker build -f apps/web/Dockerfile -t xbt-web .

FROM node:22-slim AS builder
WORKDIR /repo
RUN corepack enable   # picks pnpm version from package.json#packageManager

COPY pnpm-workspace.yaml package.json tsconfig.base.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/shared/package.json packages/shared/package.json
RUN pnpm install --no-frozen-lockfile

COPY packages/shared ./packages/shared
COPY apps/web ./apps/web

# NEXT_PUBLIC_* are inlined at build time. App Platform passes BUILD_TIME env
# vars as build args for Dockerfile components.
ARG NEXT_PUBLIC_GATEWAY_WS
ARG NEXT_PUBLIC_GATEWAY_HTTP
ENV NEXT_PUBLIC_GATEWAY_WS=${NEXT_PUBLIC_GATEWAY_WS}
ENV NEXT_PUBLIC_GATEWAY_HTTP=${NEXT_PUBLIC_GATEWAY_HTTP}
RUN pnpm --filter @xbt/web build


FROM node:22-slim AS runner
WORKDIR /repo
ENV NODE_ENV=production
RUN corepack enable
COPY --from=builder /repo ./

WORKDIR /repo/apps/web
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "require('http').get('http://localhost:3000/', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))"

CMD ["pnpm", "start"]
