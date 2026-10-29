FROM ghcr.io/home-assistant/base-python:3.12-alpine3.24

ARG BUILD_VERSION=1.0.0-alpha.2
ARG BUILD_ARCH=aarch64
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

WORKDIR /opt/surplus-pv
COPY app/ ./app/
COPY web/ ./web/
COPY run.sh /run.sh
RUN chmod 755 /run.sh

# Home Assistant base images run via S6: with-contenv exposes the
# SUPERVISOR_TOKEN securely without embedding or printing the token.
CMD ["/run.sh"]

