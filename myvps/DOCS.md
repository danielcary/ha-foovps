# myvps setup

Add your SSH public key to `ssh_authorized_keys`, start the app, then connect:

```sh
ssh -p 2222 vps@YOUR_HA_IP
cd /data/workspace/starter-app
myvps status
myvps restart
myvps logs
```

The initial Express example is at `http://YOUR_HA_IP:8000`. Change `app_directory`
and `app_command` to use your project. An empty command uses the project's
`Procfile` web entry, or `npm start`. The application receives `PORT` and
`ASPNETCORE_URLS`. A project `.env` supplies additional environment variables.

For C#, create a project with `dotnet new web` under `/data/workspace`. Select its
directory and set `app_command` to `dotnet run --no-launch-profile`. You can also
run a published DLL with `dotnet YourApp.dll`. The installed SDK/runtime major is 10.

Use `sudo myvps packages add NAME` to install and save additional Rocky packages,
or list them in the app's `packages` option. Unrecorded `dnf` installs and changes
outside persistent directories can disappear when the container is replaced.

Code, dependencies, home directories, logs, SSH host keys, and certificate state
are saved under `/data`. Nginx snippets live under `/config/nginx/http.d` and
`/config/nginx/server.d`. Run `sudo myvps reload-nginx` after editing snippets.
Home Assistant backups include these directories when this app is selected.

## HTTPS

Forward router TCP **80 → HA_IP:8000** and **443 → HA_IP:8443**. Configure:

```yaml
tls:
  enabled: true
  domains:
    - app.example.com
    - www.example.com
  email: you@example.com
  staging: false
```

All hostnames must resolve to your public IP and validation must reach this app
on public port 80. Check any AAAA/IPv6 record as well. Existing public forwards
using ports 80/443 must be replaced or routed through a shared reverse proxy.

Certbot provisions and renews certificates automatically, reloading Nginx on
success. Before issuance, HTTPS is unavailable and normal HTTP requests return
503; the ACME challenge path remains available. Failures retry after an hour.
Use `sudo myvps certificates` for a manual attempt. Staging certificates are
untrusted; switch `staging` to false to request a production certificate.

Domain names and the account email are editable in HA's Configuration tab. One
certificate covers the listed names, which all serve the selected application.
Changes to the list take effect after restarting the app. The first hostname
names the certificate. Existing `tls.domain` single-hostname settings still work.

## Home network protection

Version 0.3 enables network protection automatically, including when upgrading.
No firewall commands are needed. Keep **Home network protection → Block access
to local devices** enabled in HA and restart the app after changing its settings.

Incoming HTTP/HTTPS and SSH still work. The app can download packages and use
public IPv4 APIs, but cannot initiate TCP/UDP connections to private IPv4 devices,
HA's internal network, local-link addresses, or multicast/broadcast destinations.
New outgoing IPv6 application connections are blocked completely, including
globally routed home IPv6 addresses. Replies to incoming IPv6 clients still work.
Loopback and TCP/UDP port 53 to the configured DNS resolvers are exceptions.

The app detects your public IPv4 at startup using ipify, with an AWS fallback,
and blocks it to prevent router loopback into other forwarded services. Public
IPv4 addresses of configured TLS hostnames are also blocked. Set
`network_protection.public_ipv4` to your actual public IPv4 if automatic detection
is unavailable. **Restart after your public IP or DNS resolver settings change.**
Use `blocked_ipv4_networks` for extra ranges, additional WAN addresses, or local
networks using globally routable IPv4 addresses. There is no LAN allowlist.

Run `myvps firewall` over SSH to see the policy installed at startup. If installing
the firewall or detecting the required public address fails, the app stops and
explains why in HA's Log tab; it does not silently start without protection.

The startup process uses NET_ADMIN only inside this container's network namespace.
Before starting services it removes NET_ADMIN and NET_RAW from the runtime
capability sets, including the bounding set. SSH sudo cannot change the firewall
or open raw packet sockets; other container administration remains available.
HA Protection mode can remain enabled. Docker administrators can still bypass
these restrictions. Root compromise can tamper with files used by a future boot,
and kernel/container escape vulnerabilities remain possible. This is additional
containment, not the same boundary as a firewall outside the container.

## Workspace and network ports

`/data` is the persistent mount; `/data/workspace` is your project directory and
`/data/workspace/starter-app` is the included Express example. Other project folders
under `/data/workspace` persist too. Select a project's folder in `app_directory`
and its start command in `app_command`. One backend process is managed at a time.

Folders do not publish servers automatically. The selected app is served through
Nginx on host ports 8000/8443. A different arbitrary server port must be published
in the HA app's port configuration as well as forwarded by your router.
`/config` is the separate persistent mount for Nginx snippets.

Keep Protection mode enabled. SSH user `vps` has sudo within the container;
web applications run as user `app`. The container shares CPU, RAM, disk, and the
kernel with HAOS. Avoid workloads that exhaust those shared resources.
