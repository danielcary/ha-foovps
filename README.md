# myvps

A Home Assistant app that gives you a Rocky Linux 9 workspace on a Raspberry Pi 5
or another ARM64/AMD64 HAOS machine. SSH in, edit your project, and run an Express
or ASP.NET Core server behind Nginx.

Includes Node.js 24 LTS, npm, Git, the .NET 10 SDK and ASP.NET Core runtime,
Nginx, Certbot, SSH/SFTP, Python 3.12, editors, and native build tools. This is
a container sharing HAOS's kernel. It does not boot a VM or expose the host's Docker socket.

## Install on Home Assistant

[![Add the myvps repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fdanielcary%2Fha-foovps.git)

1. Copy this repository URL: `https://github.com/danielcary/ha-foovps.git`.
2. Open **Settings → Apps → Install app → ⋮ → Repositories**, paste the URL,
   and select **Add**. Select **myvps** under **myvps Home Assistant apps**.
   Refresh the store if needed, and enable Advanced mode in your user profile
   if the app is not visible.
3. Install the app. Home Assistant downloads the source and builds its image on
   your device; the .NET SDK makes the initial build fairly large.
4. In the app's Configuration tab, add your **public** SSH key to `ssh_authorized_keys`:

   ```yaml
   ssh_authorized_keys:
     - "ssh-ed25519 AAAA... your-key-comment"
   ```

5. Start the app. Open `http://YOUR_HA_IP:8000` to see the included Express server.
6. Connect using `ssh -p 2222 vps@YOUR_HA_IP`.

For local development, you can instead copy the `myvps` directory into Home
Assistant's **local apps directory**, check for updates in the app store, and
install **myvps** under **Local apps**.

Protection mode can stay on. The app requests `AUDIT_WRITE` for SSH. Your SSH user
has passwordless `sudo` **inside this container**. Web applications run separately
as the unprivileged `app` user. SSH password login and root SSH login are disabled.
VS Code Remote SSH and SFTP can use the same connection.

## Your Express project

The initial project lives at `/data/workspace/starter-app`:

```sh
cd /data/workspace/starter-app
npm install express
nano server.js
myvps restart
myvps logs
```

Replace that project with your own files or clone a repository under `/data/workspace`.
Configure `app_directory` in HA to select the project. By default, myvps runs `npm start`.
Set `app_command` to another shell command, or leave it empty and supply a `Procfile`:

```text
web: node server.js
```

Your server should listen on the injected `PORT` environment variable (default 3000).
Nginx reaches it over `127.0.0.1`; there is no need to expose port 3000 on the host.
Put application secrets and environment variables in your project's `.env` file.
Values are read literally; `${VARIABLE}` interpolation is disabled. Restart the
web process after editing the file. Supervisor tokens are withheld from the web process.

`myvps start`, `stop`, `restart`, `status`, and `logs` manage the application.
The app restarts after a process crash. Changes to HA options require restarting
the HA app. The initial project is an example, not a deployment service.

If Express uses HTTPS detection, client addresses, redirects, or secure cookies,
configure `app.set('trust proxy', 'loopback')`: Nginx forwards the original protocol
and client address over the loopback connection. ASP.NET Core applications should
enable Forwarded Headers middleware and trust the loopback proxy for the same reason.

## C# / ASP.NET Core

The .NET SDK can build projects inside the workspace:

```sh
cd /data/workspace
dotnet new web -n csharp
```

Set the HA options to:

```yaml
app_directory: /data/workspace/csharp
app_command: "dotnet run --no-launch-profile"
```

Restart the HA app. `ASPNETCORE_URLS` is supplied automatically for Nginx.
For deployment, publish the project and change the command to run its DLL:

```sh
dotnet publish /data/workspace/csharp -c Release -o /data/workspace/published
```

Use `app_directory: /data/workspace/published` and `app_command: dotnet csharp.dll`.
A minimal C# example is also included at `/opt/myvps/examples/dotnet`.
.NET 10 is installed; applications targeting another major runtime need that runtime
installed or must be published self-contained for `linux-arm64` on the Pi.

## HTTPS and Let's Encrypt

Default network mappings are configurable in the app's Network section:

| Purpose | Container port | HA host port |
| --- | --- | --- |
| SSH | 22 | 2222 |
| HTTP / ACME validation | 80 | 8000 |
| HTTPS | 443 | 8443 |

Create a DNS record pointing your hostname at your public IP. On the router,
forward **public TCP 80 → HA_IP:8000** and **public TCP 443 → HA_IP:8443**.
This leaves existing HA listeners on the Pi's ports 80/443 available locally.
Existing public forwarding rules on those ports must be replaced or combined
through a common reverse proxy. Public reachability is required for HTTP-01;
CGNAT or ISP port blocking can prevent it. Any published AAAA record must also
reach this server over IPv6; IPv4 router forwarding does not configure IPv6.

Configure:

```yaml
tls:
  enabled: true
  domains:
    - app.example.com
    - www.example.com
  email: you@example.com
  staging: false
```

Nginx first serves the HTTP challenge path. Until a certificate is obtained,
ordinary HTTP requests return 503 and HTTPS is unavailable. Successful issuance
enables HTTPS and redirects normal HTTP requests to public HTTPS port 443.
Certbot checks renewal every 12 hours, reloads Nginx after success, and retries
failed attempts after an hour. Certificate failures do not disable SSH.

The Domain names and account email fields are available directly in HA's app
Configuration tab. All listed hostnames serve the selected project, and a single
certificate covers the whole list. Every hostname must resolve and pass validation.
Adding or removing a name updates the certificate automatically after restarting
the app. Keep the first hostname stable: it is used as the certificate name.
The old `tls.domain` setting remains supported for existing single-hostname setups;
the `tls.domains` list takes precedence when it is populated.

Use `staging: true` to test against Let's Encrypt's staging service first; those
certificates are not browser-trusted. Changing it to `false` requests a production
certificate. Avoid repeated production reissuance because CA rate limits apply.

Visitors use `https://app.example.com`. For direct LAN access to the TLS listener,
use port 8443 with the certificate's hostname. HTTP redirects deliberately use
public port 443. A suitable local DNS override can also provide normal-port LAN
access if the frontend routing supports it.

Request a manual certificate check with `sudo myvps certificates`.
Custom Nginx snippets go in `/config/nginx/http.d/*.conf` (HTTP-level directives
or additional server blocks) or `/config/nginx/server.d/*.conf` (application
server directives). Then run `sudo myvps reload-nginx`; invalid configurations
are rejected before replacing the running configuration.

## What persists

**`/data` is the persistent mount.** `/data/workspace` is a normal directory inside
it, and `/data/workspace/starter-app` is only the initial Express project's directory.
You can create other projects, for example `/data/workspace/csharp` or
`/data/workspace/another-site`, and select the one to start in HA's `app_directory`
setting. Creating a folder does not start a service or publish a network port.
The current app automatically manages one backend web process at a time.

`/config` is a second persistent mount used for editable Nginx configuration.
For standalone Docker Compose, the volume named `workspace` mounts at `/data`
and the volume named `configuration` mounts at `/config`.

| Location | Contents |
| --- | --- |
| `/data/workspace` | Source code, npm dependencies, builds, application `.env` |
| `/data/home` | SSH user's home, shell settings, private SSH client keys |
| `/data/app-home`, `/data/cache` | Web user's .NET home and npm cache |
| `/data/ssh` | Stable SSH server host keys and configured authorized keys |
| `/data/letsencrypt`, `/data/certbot` | Certificates, ACME account, validation files |
| `/data/packages.json` | Extra packages recorded with the helper command |
| `/data/logs` | Rotated application logs and Certbot logs |
| `/config/nginx` | Editable Nginx snippets, in the app's public config directory |

Home Assistant backups include `/data` and the app config directory. Cold backups
stop the app to obtain a consistent copy. Include myvps when selecting backup content.

The web path is `router → HA host port 8000/8443 → Nginx → your selected app`.
A server on another arbitrary port must also have that port published by the HA
app before router forwarding can reach it. Binding that server only to loopback
will not allow direct Docker port forwarding. For web applications, using the
existing Nginx ports avoids exposing each backend port separately.

To install an extra system package **and remember it for replacement containers**:

```sh
sudo myvps packages add jq
myvps packages list
```

Alternatively, put package names in the HA `packages` option. Direct `sudo dnf install`
works, but unrecorded packages and changes elsewhere under `/usr` or `/etc` can
disappear when HA recreates the container. System packages cannot be made persistent
simply by saving the home directory. If restoring extras fails, SSH still starts.

## Local Docker test

```sh
docker compose up --build -d
curl http://localhost:8000/health
docker compose exec myvps myvps status
```

The Compose ports bind only to the local machine. Its named volume preserves data.
The separate configuration volume preserves Nginx snippets.
For SSH, create an options JSON with your public key, copy it to `/data/options.json`
using `docker compose cp`, then restart the container. HA writes this file itself
in the real app environment. Do not put private keys in the HA options.

Run the automated tests:

```sh
docker build -t myvps:dev myvps
docker run --rm --entrypoint /opt/myvps-tools/bin/python \
  -v "$PWD/tests:/tests:ro" myvps:dev /tests/test_control.py
python3 scripts/smoke_test.py --acme
```

The smoke test creates temporary containers, keys, and volumes, exercises Express,
SSH, app crash recovery, saved package restoration, container replacement, and a
C# server, and certificate issuance/renewal using Let's Encrypt's local Pebble
test CA, then removes its own resources. It does not modify other containers
or request public certificates. Omit `--acme` to skip the local CA test.

## Full Home Assistant + Supervisor test

Plain Home Assistant Container has no app store. The official HA development image
runs a separate Docker engine and Supervisor inside a privileged development
container. This privilege is for the test environment; **myvps itself needs no such mode**.

```sh
bash scripts/ha_dev.sh start
# Wait for Home Assistant setup at http://localhost:7123/
bash scripts/ha_dev.sh install
bash scripts/ha_dev.sh status
```

Local app HTTP is `http://localhost:8000`; SSH is `localhost:2222`; HTTPS is
`localhost:8443` when configured. Configure SSH keys in the test HA's myvps options.
Stop the standalone Compose service first if it is already using these ports.
Use `bash scripts/ha_dev.sh rebuild` after source changes and `stop` to stop the
environment without deleting its data. VS Code users can instead reopen this
repository in its included devcontainer and run the supplied tasks.

The full-stack test checks Supervisor configuration, image installation, app startup,
network mappings, and storage lifecycle. It cannot verify your Pi's power, storage
performance, router forwarding, public DNS, or real public certificate issuance.
