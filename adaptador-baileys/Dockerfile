# syntax=docker/dockerfile:1

# Builder: full toolchain, compiles TypeScript, then drops dev dependencies.
FROM node:22.21.0-alpine3.22 AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY tsconfig.json ./
COPY src ./src
RUN npm run build
# Keep only production dependencies for the runtime stage.
RUN npm prune --omit=dev

# Runtime: only dist/ and production node_modules, as the unprivileged node user.
FROM node:22.21.0-alpine3.22 AS runtime
ENV NODE_ENV=production \
    PORT=8080 \
    SESSION_DIR=/data/session
WORKDIR /app
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY package.json ./

# Session credentials persist on a declared volume owned by node.
RUN mkdir -p /data/session && chown -R node:node /data
VOLUME ["/data/session"]
USER node

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||8080)+'/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

LABEL org.opencontainers.image.source="https://github.com/projeto-doceterapia/assistente-whatsapp" \
      org.opencontainers.image.title="adaptador-baileys" \
      org.opencontainers.image.description="WhatsApp Baileys adapter for the stock assistant"

CMD ["node", "dist/main.js"]
