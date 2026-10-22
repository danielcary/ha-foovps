# Changelog

## 0.3.0

- Enable a container-only outgoing firewall by default, including existing installations.
- Block new connections to private/special-use IPv4 networks, the detected home
  public IPv4 address, configured TLS hostnames' public IPv4 addresses, and new
  outgoing IPv6 connections. Permit loopback, replies, and designated DNS resolvers.
- Apply the firewall before extra packages and managed services start. Refuse to
  start if firewall installation or required public-address discovery fails.
- Request NET_ADMIN for startup, then remove NET_ADMIN and NET_RAW from runtime
  services and SSH sessions, including sudo. Keep ordinary container administration.
- Add HA protection settings, `myvps firewall`, and network containment tests.

## 0.2.0

- Name the included Express project `starter-app` under `/data/workspace/starter-app`.
- Configure a list of certificate hostnames and the account email from Home Assistant.
- Automatically update the certificate when its hostname list changes.
- Preserve compatibility with existing single-hostname settings.
- Add configuration field labels and clarify persistent mounts and published ports.

## 0.1.0

- Initial Rocky Linux workspace for ARM64 and AMD64.
- Key-based SSH/SFTP, Node.js 24, Express example, and .NET 10 SDK.
- Nginx proxying, optional Certbot HTTP-01 certificates, and automatic renewal.
- Persistent projects, home directories, SSH host keys, logs, and certificate state.
- Saved extra system packages and supervised web processes.
