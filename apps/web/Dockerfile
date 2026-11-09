FROM node:22-alpine AS development
WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
RUN npm ci
COPY apps/web apps/web
EXPOSE 5173
CMD ["npm", "run", "dev"]

FROM development AS build
ARG APP_VERSION=development
RUN npm run build
RUN printf '{"version":"%s"}\n' "$APP_VERSION" > /app/apps/web/dist/version.json

FROM nginxinc/nginx-unprivileged:1.28-alpine AS production
COPY --from=build /app/apps/web/dist /usr/share/nginx/html
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
