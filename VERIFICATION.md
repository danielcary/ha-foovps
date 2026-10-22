# Local verification

Verified on October 21, 2026, using Docker Desktop on ARM64 macOS.

## Built image and automated tests

- Rocky Linux 9 ARM64 image built successfully with Node.js 24, .NET 10 SDK,
  Nginx, OpenSSH, and Certbot 5.8.
- Eight focused tests passed for workspace-path boundaries, package/hostname
  validation, challenge routing, public HTTPS redirect ports, and switching the
  ACME staging service to production, including domain-list validation and updates.
- Eleven integration checks passed with a temporary data volume and a 1 GiB memory
  limit: Express/Nginx, SSH key authentication, privilege separation, ACME HTTP
  routing, crash recovery, persistence after container replacement, saved package
  restoration, C# serving, HTTPS, and local ACME issuance/renewal with Nginx reload.
  Certificates covered multiple hostnames; adding another name updated the same
  certificate and passed a real TLS hostname check against the local CA.
- The local ACME test used Let's Encrypt's Pebble server and performed HTTP-01
  validation. No certificates were requested from the public production CA.

Run these again with the commands in README.md. CI runs the same container tests
on ARM64 and AMD64 runners; the local run verified ARM64.

## Home Assistant integration

The official HA development image ran an isolated nested Docker engine with:

- Home Assistant Core 2026.10.2.
- Supervisor 2026.10.3.
- myvps 0.2.0 installed through Supervisor's local app store and rebuilt through Supervisor.
- The renamed `/data/workspace/starter-app` project served its updated welcome page.
  Supervisor accepted the certificate domain list and email fields; original disabled
  TLS settings were restored after checking the configuration round trip.
- Protection mode on, default AppArmor configuration, `full_access: false`, and
  `docker_api: false`. SSH used the requested `AUDIT_WRITE` capability.
- All five managed processes running. Express HTTP and key-based SSH were checked
  through the published host ports, including noninteractive service commands and sudo.
- Workspace data and SSH keys retained across app rebuilds and replacement of the
  outer development container. Nginx snippets were also checked through the app config mount.

A brief idle sample showed approximately 78 MiB of app memory use with the Express
example. This is a measurement of that workload, not a resource reservation or limit.
The real HA app shares the host's resources; the 1 GiB limit above belongs to the
standalone integration test.

The test environment is available at http://localhost:7123/ while running, and
myvps HTTP is at http://localhost:8000/. It uses isolated development data and
does not connect to or modify the user's real Pi/Home Assistant instance.

## Still needs the deployment environment

The Raspberry Pi 5 itself, its storage/power behavior, router port forwarding,
public DNS/IPv6 reachability, and real Let's Encrypt issuance were not exercised.
Configure those on deployment using the installation and HTTPS instructions.
