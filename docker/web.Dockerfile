# Next.js frontend image. The web/ directory is scaffolded in phase 5; until
# then this image has nothing to build and the `web` service stays behind the
# `web` compose profile.
FROM node:24-alpine AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1


FROM base AS deps
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install


FROM deps AS dev
EXPOSE 3000
# Source arrives via bind mount; next dev handles hot module replacement.
CMD ["npm", "run", "dev"]


FROM deps AS build
COPY web/ ./
RUN npm run build


# Runtime image relies on `output: 'standalone'` in next.config.
FROM base AS prod
ENV NODE_ENV=production
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=build --chown=nextjs:nodejs /app/public ./public
USER nextjs
EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]
