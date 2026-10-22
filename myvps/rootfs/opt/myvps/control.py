#!/opt/myvps-tools/bin/python
"""Initialize a persistent HA workspace and operate its supervised services."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time

from dotenv import dotenv_values

import firewall

DATA = Path('/data')
RUN = Path('/run/myvps')
SUPERVISOR_CONFIG = RUN / 'supervisord.conf'
DEFAULTS = {
    'ssh_authorized_keys': [], 'packages': [], 'app_enabled': True,
    'app_directory': '/data/workspace/starter-app', 'app_command': '', 'app_port': 3000,
    'tls': {'enabled': False, 'domains': [], 'domain': '', 'email': '', 'staging': False},
    'network_protection': firewall.DEFAULTS,
}
PACKAGE = re.compile(r'[A-Za-z0-9][A-Za-z0-9+_.:-]*\Z')
DOMAIN = re.compile(r'(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')


def log(message):
    print(f'[myvps] {message}', flush=True)


def read_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def tls_domains(tls):
    domains = tls.get('domains', [])
    if not isinstance(domains, list) or any(not isinstance(domain, str) for domain in domains):
        raise ValueError('tls.domains must be a list of DNS hostnames')
    # Version 0.1 used a single domain. Keep existing installations working.
    if not domains and tls.get('domain'):
        domains = [tls['domain']]
    result = []
    for domain in domains:
        if not isinstance(domain, str) or not DOMAIN.fullmatch(domain.strip().lower()):
            raise ValueError(f'Invalid TLS hostname: {domain!r}')
        domain = domain.strip().lower()
        if domain not in result:
            result.append(domain)
    return result


def options():
    source = read_json(DATA / 'options.json', {})
    settings = {**DEFAULTS, **source}
    settings['tls'] = {**DEFAULTS['tls'], **source.get('tls', {})}
    settings['network_protection'] = firewall.validate(source.get('network_protection', {}))
    port = settings['app_port']
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('app_port must be an integer between 1024 and 65535')
    directory = Path(settings['app_directory'])
    if not directory.is_absolute() or not directory.resolve().is_relative_to(DATA / 'workspace'):
        raise ValueError('app_directory must be inside /data/workspace')
    tls = settings['tls']
    tls['domains'] = tls_domains(tls)
    tls['domain'] = tls['domains'][0] if tls['domains'] else ''
    if tls['enabled']:
        if not tls['domains']:
            raise ValueError('tls.domains must contain at least one DNS hostname')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', tls['email']):
            raise ValueError('tls.email must be a valid email address')
    for package in settings['packages']:
        validate_package(package)
    if not isinstance(settings['app_command'], str) or '\n' in settings['app_command']:
        raise ValueError('app_command must be a single shell command')
    return settings


def write(path, contents, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(contents)
    temporary.chmod(mode)
    temporary.replace(path)


def root_required():
    if os.geteuid() != 0:
        raise ValueError('Run this command with sudo.')


def validate_package(package):
    if not isinstance(package, str) or not PACKAGE.fullmatch(package):
        raise ValueError(f'Invalid package name: {package!r}')


def install_packages(packages):
    packages = sorted(set(packages))
    for package in packages:
        validate_package(package)
    if packages:
        subprocess.run(['dnf', '-y', 'install', *packages], check=True)


def cert_paths(settings):
    domains = tls_domains(settings['tls'])
    directory = DATA / 'letsencrypt/live' / (domains[0] if domains else '')
    return directory / 'fullchain.pem', directory / 'privkey.pem'


def nginx_config(settings):
    tls = settings['tls']
    names = ' '.join(tls_domains(tls)) if tls['enabled'] else '_'
    certificate, key = cert_paths(settings)
    ready = tls['enabled'] and certificate.is_file() and key.is_file()
    proxy = f'''include /config/nginx/server.d/*.conf;
    location / {{
        proxy_pass http://127.0.0.1:{settings['app_port']};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 300s;
    }}'''
    if ready:
        http_route = 'location / { return 301 https://$host$request_uri; }'
    elif tls['enabled']:
        http_route = 'location / { return 503 "HTTPS certificate is being provisioned.\\n"; }'
    else:
        http_route = proxy
    servers = f'''server {{
    listen 80;
    server_name {names};
    location ^~ /.well-known/acme-challenge/ {{
        root /data/certbot/webroot;
        default_type text/plain;
        try_files $uri =404;
    }}
    location = /_myvps/health {{ access_log off; return 200 "ok\\n"; }}
    {http_route}
}}
'''
    if ready:
        servers += f'''server {{
    listen 443 ssl;
    server_name {names};
    ssl_certificate {certificate};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:TLS:10m;
    {proxy}
}}
'''
    return '''user nginx;
worker_processes auto;
pid /run/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log /dev/stdout;
    sendfile on;
    server_tokens off;
    client_max_body_size 20m;
    map $http_upgrade $connection_upgrade { default upgrade; '' close; }
    include /config/nginx/http.d/*.conf;
''' + servers + '\n}\n'


def render_nginx(settings):
    destination = RUN / 'nginx.conf'
    candidate = RUN / 'nginx.candidate.conf'
    write(candidate, nginx_config(settings))
    subprocess.run(['nginx', '-t', '-c', str(candidate)], check=True)
    candidate.replace(destination)


def supervisor_config(settings):
    header = '''[unix_http_server]
file=/run/myvps/supervisor.sock
chmod=0660
chown=root:vps
[supervisord]
nodaemon=true
user=root
logfile=/dev/null
logfile_maxbytes=0
pidfile=/run/myvps/supervisord.pid
childlogdir=/data/logs
umask=0002
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix:///run/myvps/supervisor.sock
'''
    programs = {
        'nginx': ('/usr/sbin/nginx -c /run/myvps/nginx.conf -g "daemon off;"', 'root'),
        'sshd': ('/usr/sbin/sshd -D -e -f /run/myvps/sshd_config', 'root'),
        'web': ('/usr/local/bin/myvps _web', 'app'),
        'certificates': ('/usr/local/bin/myvps _renew', 'root'),
        'web-logs': ('/usr/bin/tail -n 0 -F /data/logs/web.log', 'root'),
    }
    for name, (command, user) in programs.items():
        web = name == 'web'
        header += f'''\n[program:{name}]
command={command}
user={user}
autostart={'true' if not web or settings['app_enabled'] else 'false'}
autorestart=true
startsecs=2
startretries=3
stopasgroup=true
killasgroup=true
stopwaitsecs=30
redirect_stderr=true
stdout_logfile={'/data/logs/web.log' if web else '/dev/stdout'}
stdout_logfile_maxbytes={'2MB' if web else '0'}
stdout_logfile_backups=3
'''
    return header


def initialize():
    root_required()
    settings = options()
    network_state = firewall.install(settings['network_protection'], settings['tls']['domains'], RUN)
    log('Network protection: ' + ('enabled; new connections to local networks and outbound IPv6 blocked.'
        if network_state['enabled'] else 'DISABLED; local networks can be reached.'))
    DATA.mkdir(parents=True, exist_ok=True)
    DATA.chmod(0o755)
    RUN.mkdir(parents=True, exist_ok=True)
    for path in (Path('/config/nginx/http.d'), Path('/config/nginx/server.d')):
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 1000, 1000)
        if not (path / 'README.txt').exists():
            write(path / 'README.txt', 'Put custom Nginx .conf snippets here. Run sudo myvps reload-nginx after editing.\n')
    for relative in ('workspace', 'home', 'app-home', 'cache/npm', 'logs', 'ssh',
                     'letsencrypt', 'certbot/webroot/.well-known/acme-challenge', 'certbot/work'):
        (DATA / relative).mkdir(parents=True, exist_ok=True)
    # Never recursively chown a user workspace: repositories may intentionally have other owners.
    os.chown(DATA / 'workspace', 1000, 1001)
    (DATA / 'workspace').chmod(0o2775)
    for relative, uid, gid in (('home', 1000, 1000), ('app-home', 1001, 1001), ('cache/npm', 1001, 1001)):
        os.chown(DATA / relative, uid, gid)
        (DATA / relative).chmod(0o700)
    (DATA / 'letsencrypt').chmod(0o700)
    # Unlock the SSH account with an unknowable password; password authentication stays disabled.
    encoded = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=secrets.token_hex(32),
                             text=True, capture_output=True, check=True).stdout.strip()
    subprocess.run(['usermod', '-p', encoded, 'vps'], check=True)
    for kind in ('ed25519', 'rsa'):
        path = DATA / 'ssh' / f'ssh_host_{kind}_key'
        if not path.exists():
            subprocess.run(['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(path)], check=True)
    keys = settings['ssh_authorized_keys']
    if not isinstance(keys, list) or any(not isinstance(key, str) or '\n' in key for key in keys):
        raise ValueError('ssh_authorized_keys must be a list of single-line public keys')
    write(DATA / 'ssh/authorized_keys', '\n'.join(keys) + '\n')
    if not keys:
        log('No SSH keys configured. Add your public key in the app options to enable login.')
    write(RUN / 'sshd_config', '''Port 22
ListenAddress 0.0.0.0
HostKey /data/ssh/ssh_host_ed25519_key
HostKey /data/ssh/ssh_host_rsa_key
PidFile /run/myvps/sshd.pid
AuthorizedKeysFile /data/ssh/authorized_keys
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
UsePAM yes
AllowUsers vps
AllowTcpForwarding yes
GatewayPorts no
X11Forwarding no
PrintMotd no
Subsystem sftp internal-sftp
''')
    profile = DATA / 'home/.bash_profile'
    if not profile.exists():
        write(profile, 'export PATH=/opt/myvps-tools/bin:/usr/local/bin:$PATH\numask 0002\ncd /data/workspace\necho "myvps workspace — run myvps help"\n')
        os.chown(profile, 1000, 1000)
    target = DATA / 'workspace/starter-app'
    if not target.exists():
        shutil.copytree('/opt/myvps/examples/express', target)
        for path in [target, *target.rglob('*')]:
            if not path.is_symlink():
                os.chown(path, 1000, 1001)
                if path.is_dir():
                    path.chmod(path.stat().st_mode | 0o2070)
    packages = settings['packages'] + read_json(DATA / 'packages.json', [])
    try:
        install_packages(packages)
    except subprocess.CalledProcessError:
        log('Extra package installation failed; continuing so SSH remains available for repairs.')
    render_nginx(settings)
    # Supervisor's options.json is private to root. Do not loosen its permissions.
    web_settings = RUN / 'web.json'
    write(web_settings, json.dumps({key: settings[key] for key in
          ('app_directory', 'app_command', 'app_port')}), 0o640)
    os.chown(web_settings, 0, 1001)
    write(RUN / 'packages.json', json.dumps(settings['packages']))
    write(SUPERVISOR_CONFIG, supervisor_config(settings))
    (DATA / 'logs/web.log').touch(exist_ok=True)
    subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(RUN / 'sshd_config')], check=True)
    log('Starting Rocky workspace: SSH :22, HTTP :80, web app as unprivileged user app.')
    os.execv('/usr/bin/setpriv', ['setpriv', *firewall.DROP_CAPABILITIES,
             '/opt/myvps-tools/bin/supervisord', '-c', str(SUPERVISOR_CONFIG)])


def run_web():
    settings = read_json(RUN / 'web.json', None)
    if settings is None:
        raise ValueError('Web settings are missing; restart the Home Assistant app.')
    directory = Path(settings['app_directory'])
    command = settings['app_command'].strip()
    if not command and (directory / 'Procfile').exists():
        for line in (directory / 'Procfile').read_text().splitlines():
            if line.startswith('web:'):
                command = line.partition(':')[2].strip()
                break
    command = command or 'npm start'
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('SUPERVISOR_TOKEN', 'HASSIO_TOKEN')}
    environment.update({key: value for key, value in dotenv_values(directory / '.env', interpolate=False).items()
                        if value is not None and key not in ('SUPERVISOR_TOKEN', 'HASSIO_TOKEN')})
    environment.update(HOME='/data/app-home', USER='app', LOGNAME='app',
                       PORT=str(settings['app_port']), NODE_ENV=environment.get('NODE_ENV', 'production'),
                       ASPNETCORE_URLS=f'http://127.0.0.1:{settings["app_port"]}',
                       DOTNET_CLI_HOME='/data/app-home', npm_config_cache='/data/cache/npm')
    os.chdir(directory)
    os.umask(0o002)
    log(f'Starting web command in {directory}: {command}')
    os.execvpe('/bin/bash', ['bash', '-c', command], environment)


def supervisor(*arguments, check=True):
    return subprocess.run(['/opt/myvps-tools/bin/supervisorctl', '-c', str(SUPERVISOR_CONFIG), *arguments], check=check)


def reload_nginx():
    root_required()
    render_nginx(options())
    subprocess.run(['nginx', '-s', 'reload', '-c', str(RUN / 'nginx.conf')], check=True)


def certificates():
    root_required()
    settings = options()
    tls = settings['tls']
    if not tls['enabled']:
        log('HTTPS is disabled. Configure tls.enabled, domains, and email in the app options.')
        return
    common = ['--config-dir', '/data/letsencrypt', '--work-dir', '/data/certbot/work',
              '--logs-dir', '/data/logs/certbot', '--non-interactive']
    server = os.environ.get('MYVPS_ACME_SERVER') or (
        'https://acme-staging-v02.api.letsencrypt.org/directory' if tls['staging']
        else 'https://acme-v02.api.letsencrypt.org/directory')
    common += ['--server', server]
    certificate, key = cert_paths(settings)
    state_file = DATA / 'letsencrypt/myvps-server.json'
    previous = read_json(state_file, {})
    state = {'server': server, 'domains': sorted(tls['domains'])}
    saved = previous.get(tls['domain'])
    if isinstance(saved, str):
        saved = {'server': saved, 'domains': [tls['domain']]}
    changed = saved != state
    if not certificate.is_file() or not key.is_file() or changed:
        command = ['certbot', 'certonly', '--webroot', '-w', '/data/certbot/webroot',
                   '--agree-tos', '--email', tls['email'],
                   '--cert-name', tls['domain'], *common]
        for domain in tls['domains']:
            command += ['-d', domain]
        if certificate.is_file() and key.is_file() and changed:
            command += ['--force-renewal', '--renew-with-new-domains']
            if tls['staging']:
                command.append('--break-my-certs')
    else:
        command = ['certbot', 'renew', '--cert-name', tls['domain'], *common]
    subprocess.run(command, check=True, timeout=600)
    previous[tls['domain']] = state
    write(state_file, json.dumps(previous, indent=2) + '\n', 0o600)
    reload_nginx()


def renew_loop():
    # Nginx must be listening before the first HTTP-01 request.
    time.sleep(5)
    while True:
        if options()['tls']['enabled']:
            try:
                certificates()
            except (subprocess.SubprocessError, ValueError) as error:
                log(f'Certificate attempt failed: {error}. Retrying in one hour.')
                time.sleep(3600)
                continue
        time.sleep(12 * 3600)


def main():
    parser = argparse.ArgumentParser(description='Operate the myvps workspace')
    parser.add_argument('command', nargs='?', default='help', choices=[
        'help', 'status', 'start', 'stop', 'restart', 'logs', 'check', 'packages',
        'certificates', 'reload-nginx', 'firewall', '_init', '_web', '_renew'])
    parser.add_argument('arguments', nargs='*')
    args = parser.parse_args()
    command = args.command
    if command == '_init':
        initialize()
    elif command == '_web':
        run_web()
    elif command == '_renew':
        renew_loop()
    elif command == 'status':
        supervisor('status', check=False)
    elif command == 'firewall':
        print(json.dumps(read_json(RUN / 'firewall.json', {'error': 'App has not initialized'}), indent=2))
    elif command in ('start', 'stop', 'restart'):
        supervisor(command, 'web')
    elif command == 'logs':
        os.execvp('tail', ['tail', '-n', '100', '-F', '/data/logs/web.log'])
    elif command == 'reload-nginx':
        reload_nginx()
    elif command == 'check':
        root_required()
        options()
        subprocess.run(['nginx', '-t', '-c', str(RUN / 'nginx.conf')], check=True)
    elif command == 'certificates':
        certificates()
    elif command == 'packages':
        packages = read_json(DATA / 'packages.json', [])
        if not args.arguments or args.arguments == ['list']:
            configured = read_json(RUN / 'packages.json', [])
            print('\n'.join(sorted(set(configured + packages))))
        elif args.arguments[0] == 'add' and len(args.arguments) > 1:
            root_required()
            additions = args.arguments[1:]
            install_packages(additions)
            write(DATA / 'packages.json', json.dumps(sorted(set(packages + additions)), indent=2) + '\n')
        else:
            parser.error('Use: myvps packages list | sudo myvps packages add PACKAGE ...')
    else:
        print('''myvps status                 Show all services
myvps start|stop|restart      Control your web application
myvps logs                   Follow the web application log
myvps firewall               Show the network policy installed at startup
sudo myvps check             Validate settings and Nginx configuration
myvps packages list          List saved extra packages
sudo myvps packages add NAME Install and remember a Rocky package
sudo myvps certificates      Request/renew the configured TLS certificate
sudo myvps reload-nginx      Validate and reload Nginx

Workspace: /data/workspace/starter-app   Home: /data/home
Set app_command in HA options, or create a Procfile containing: web: npm start
Place application environment variables in the project's .env file.
The application runs as user app; SSH user vps can administer this container.''')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        log(str(error))
        sys.exit(1)
