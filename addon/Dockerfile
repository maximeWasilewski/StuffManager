# Add-on Home Assistant. Le contexte de build est ce dossier (pas la racine du dépôt).
# python:3.12-slim-bookworm existe en amd64 et arm64 / aarch64.
FROM python:3.12-slim-bookworm

ARG BUILD_VERSION=1.0.0
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="addon" \
  io.hass.arch="aarch64|amd64"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUFFMANAGER_DATA=/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8080

CMD ["/run.sh"]
