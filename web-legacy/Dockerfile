# base image
ARG NODE_VERSION=24.22.3
FROM node:${NODE_VERSION}-alpine AS base
LABEL maintainer="takatost@gmail.com"

# if you located in China, you can use aliyun mirror to speed up
# RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.aliyun.com/g' /etc/apk/repositories

# if you located in China, you can use taobao registry to speed up
# RUN npm config set registry https://registry.npmmirror.com

RUN apk add --no-cache tzdata
RUN corepack enable
ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"
ARG NEXT_PUBLIC_BASE_PATH=""
ENV NEXT_PUBLIC_BASE_PATH="$NEXT_PUBLIC_BASE_PATH"


# install packages
FROM base AS packages

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml /app/
COPY patches /app/patches
COPY web-legacy/package.json /app/web-legacy/
COPY packages /app/packages

# Use packageManager from package.json
RUN corepack install

# Install workspace dependencies for the Docker build environment.
RUN pnpm install --frozen-lockfile --ignore-scripts

# build resources
FROM base AS builder
WORKDIR /app
COPY --from=packages /app/ .
COPY . .

WORKDIR /app/web-legacy
ENV NODE_OPTIONS="--max-old-space-size=4096"
ENV pnpm_config_verify_deps_before_run=false
RUN pnpm build


# production stage
FROM base AS production

ENV NODE_ENV=production
ENV DEPLOY_ENV=PRODUCTION
ENV CONSOLE_API_URL=http://127.0.0.1:5001
ENV APP_API_URL=http://127.0.0.1:5001
ENV MARKETPLACE_API_URL=https://marketplace.dify.ai
ENV MARKETPLACE_URL=https://marketplace.dify.ai
ENV PORT=3000
# Next.js standalone binds to HOSTNAME. Docker defaults it to the container ID, and
# net.Server.listen() takes an ID like 30889528e495 as a numeric backlog, not a host.
ENV HOSTNAME=0.0.0.0
ENV NEXT_TELEMETRY_DISABLED=1

# set timezone
ENV TZ=UTC
RUN ln -s /usr/share/zoneinfo/${TZ} /etc/localtime \
    && echo ${TZ} > /etc/timezone

# Create non-root user
ARG dify_uid=1001
RUN addgroup -S -g ${dify_uid} dify && \
    adduser -S -u ${dify_uid} -G dify -s /bin/ash -h /home/dify dify && \
    mkdir /app && \
    chown -R dify:dify /app


WORKDIR /app

COPY --from=builder --chown=dify:dify /app/web-legacy/public ./targets/next/web-legacy/public
COPY --from=builder --chown=dify:dify /app/web-legacy/.next/standalone ./targets/next/
COPY --from=builder --chown=dify:dify /app/web-legacy/.next/static ./targets/next/web-legacy/.next/static

COPY --chown=dify:dify --chmod=755 web-legacy/docker/entrypoint.sh ./entrypoint.sh

ARG COMMIT_SHA
ENV COMMIT_SHA=${COMMIT_SHA}

USER dify
EXPOSE 3000
ENTRYPOINT ["/bin/sh", "./entrypoint.sh"]
