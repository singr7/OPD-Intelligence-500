FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined into the browser bundle at build time, so they must be
# ARGs here — a runtime env on the container does not reach the client code. The
# compose file wires these from .env (API base at the api's published port; the
# kiosk server-STT flag). Defaults keep the dev-machine build unchanged.
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ARG NEXT_PUBLIC_KIOSK_SERVER_STT=0
ARG NEXT_PUBLIC_KIOSK_SERVER_TTS=0
ARG NEXT_PUBLIC_KIOSK_ADAPTIVE=0
# The boarding pass (doc 23). These three were read by the kiosk and had no ARG,
# which meant they were unsettable in **every built image** — the pass silently
# printed roll80 geometry, autoprint could not be switched on, and Print had no
# bridge URL to POST to, so a box with a printer bolted to it had no way to say
# so. The preview still rendered, which is why it went unnoticed.
ARG NEXT_PUBLIC_PASS_GEOMETRY=roll80
ARG NEXT_PUBLIC_PASS_AUTOPRINT=0
ARG NEXT_PUBLIC_PRINT_BRIDGE_URL=
ARG NEXT_PUBLIC_DOCTOR_COMING_SOON=0
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_KIOSK_SERVER_STT=$NEXT_PUBLIC_KIOSK_SERVER_STT
ENV NEXT_PUBLIC_KIOSK_SERVER_TTS=$NEXT_PUBLIC_KIOSK_SERVER_TTS
ENV NEXT_PUBLIC_KIOSK_ADAPTIVE=$NEXT_PUBLIC_KIOSK_ADAPTIVE
ENV NEXT_PUBLIC_PASS_GEOMETRY=$NEXT_PUBLIC_PASS_GEOMETRY
ENV NEXT_PUBLIC_PASS_AUTOPRINT=$NEXT_PUBLIC_PASS_AUTOPRINT
ENV NEXT_PUBLIC_PRINT_BRIDGE_URL=$NEXT_PUBLIC_PRINT_BRIDGE_URL
ENV NEXT_PUBLIC_DOCTOR_COMING_SOON=$NEXT_PUBLIC_DOCTOR_COMING_SOON
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/public ./public
EXPOSE 3000
CMD ["npm", "run", "start"]
