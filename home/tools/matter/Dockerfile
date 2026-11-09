FROM node:24-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY *.mjs ./
COPY upstream-rvc ./upstream-rvc
CMD ["node", "device.mjs"]
