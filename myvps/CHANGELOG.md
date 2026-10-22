# Changelog

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
