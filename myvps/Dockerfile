FROM node:24-bookworm-slim AS node

FROM rockylinux:9

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN sed -i -e 's|^mirrorlist=|#mirrorlist=|' \
       -e 's|^#baseurl=http://dl.rockylinux.org/|baseurl=https://dl.rockylinux.org/|' \
       /etc/yum.repos.d/rocky*.repo \
    && dnf -y -q upgrade --refresh \
    && dnf -y -q install nginx openssh-server openssh-clients sudo git curl-minimal \
       ca-certificates openssl tar gzip unzip findutils procps-ng nano vim-minimal \
       make gcc gcc-c++ python3.12 python3.12-pip dotnet-sdk-10.0 \
    && dnf clean all \
    && rm -rf /var/cache/dnf \
    && groupadd --gid 1000 vps \
    && groupadd --gid 1001 app \
    && useradd --uid 1000 --gid vps --groups app --home-dir /data/home \
       --no-create-home --shell /bin/bash vps \
    && useradd --uid 1001 --gid app --home-dir /data/app-home \
       --no-create-home --shell /sbin/nologin app \
    && echo 'vps ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/myvps \
    && chmod 0440 /etc/sudoers.d/myvps \
    && python3.12 -m venv /opt/myvps-tools

COPY --from=node /usr/local/ /usr/local/
COPY requirements.txt /opt/myvps/requirements.txt
RUN /opt/myvps-tools/bin/pip install --no-cache-dir -r /opt/myvps/requirements.txt \
    && ln -s /opt/myvps-tools/bin/certbot /usr/local/bin/certbot

COPY rootfs/ /
COPY examples/express/ /opt/myvps/examples/express/
COPY examples/dotnet/ /opt/myvps/examples/dotnet/
RUN chmod 0755 /opt/myvps/control.py \
    && chmod 0440 /etc/sudoers.d/myvps && visudo -cf /etc/sudoers.d/myvps \
    && ln -s /opt/myvps/control.py /usr/local/bin/myvps \
    && cd /opt/myvps/examples/express && npm ci --omit=dev \
    && node --version && dotnet --list-sdks && certbot --version

ENV PATH="/opt/myvps-tools/bin:${PATH}" \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1

ARG BUILD_ARCH=aarch64
ARG BUILD_VERSION=0.2.0
LABEL io.hass.name="myvps" \
      io.hass.description="Rocky Linux development and web server workspace" \
      io.hass.type="addon" \
      io.hass.arch="${BUILD_ARCH}" \
      io.hass.version="${BUILD_VERSION}"

EXPOSE 22 80 443
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/myvps", "_init"]
