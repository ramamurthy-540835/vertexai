FROM node:22-slim AS deps
WORKDIR /app
COPY lead_match_monitoring_app/package*.json ./
RUN npm install

FROM node:22-slim AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY lead_match_monitoring_app ./
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=8080

RUN groupadd --system --gid 1001 nodejs && useradd --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

USER nextjs
EXPOSE 8080
CMD ["node", "server.js"]
