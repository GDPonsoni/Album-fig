# Imagem única: o backend entrega o front compilado na mesma origem.
FROM node:22-slim AS front
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_ROOT_USER_ACTION=ignore
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=front /app/frontend/dist /app/frontend/dist
# Um processo só: os avisos da troca ficam em memória. O plano gratuito do Render
# não tem pre-deploy, então a migração roda na subida.
CMD alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} \
    --proxy-headers --forwarded-allow-ips='*'
