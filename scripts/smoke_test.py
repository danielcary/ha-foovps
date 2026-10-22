#!/usr/bin/env python3
"""Exercise the actual ARM64/AMD64 image using temporary Docker resources."""
import argparse
import http.client
import json
import os
from pathlib import Path
import ssl
import socket
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments, check=True, timeout=300):
    return subprocess.run(arguments, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)


def docker(*arguments, **kwargs):
    return run('docker', *arguments, **kwargs)


def wait_for(check, description, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                print('PASS ' + description, flush=True)
                return result
        except (OSError, ValueError, AssertionError, ssl.SSLError,
                subprocess.SubprocessError, http.client.HTTPException) as error:
            last = error
        time.sleep(1)
    raise AssertionError('Timed out: {} ({})'.format(description, last))


def response(port, path='/health', context=None, hostname=None):
    connection_class = http.client.HTTPSConnection if context else http.client.HTTPConnection
    kwargs = {'timeout': 5}
    if context:
        kwargs['context'] = context
    connection = connection_class('127.0.0.1', port, **kwargs)
    connection.request('GET', path, headers={'Host': hostname or 'localhost'})
    reply = connection.getresponse()
    contents = reply.read().decode()
    result = (reply.status, dict(reply.getheaders()), contents)
    connection.close()
    return result


def tls_hostname(port, context, hostname):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=hostname):
            return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='myvps:dev')
    parser.add_argument('--acme', action='store_true', help='Also issue and renew a real certificate through local Pebble')
    args = parser.parse_args()
    prefix = 'myvps-smoke-' + uuid.uuid4().hex[:8]
    name, volume, network = prefix, prefix + '-data', prefix + '-net'
    pebble = prefix + '-ca'
    checks = []
    with tempfile.TemporaryDirectory(prefix='myvps-smoke-') as temporary:
        directory = Path(temporary)
        key = directory / 'id_ed25519'
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        settings = {'ssh_authorized_keys': [key.with_suffix('.pub').read_text().strip()],
                    'app_enabled': True, 'app_directory': '/data/workspace/starter-app',
                    'app_command': '', 'app_port': 3000, 'packages': [],
                    'tls': {'enabled': False, 'domain': '', 'email': '', 'staging': False}}
        options = directory / 'options.json'

        def copy_options():
            options.write_text(json.dumps(settings))
            options.chmod(0o600)  # Match Supervisor's private options file.
            docker('cp', str(options), name + ':/data/options.json')

        def mapped_ports():
            ports = json.loads(docker('inspect', '--format', '{{json .NetworkSettings.Ports}}', name).stdout)
            return {port: int(ports[str(port) + '/tcp'][0]['HostPort']) for port in (22, 80, 443)}

        def start():
            docker('create', '--name', name, '--network', network,
                   '--network-alias', 'app.myvps.test', '--network-alias', 'api.myvps.test',
                   '--network-alias', 'www.myvps.test',
                   '--memory', '1g', '--cpus', '2',
                   '-e', 'MYVPS_ACME_SERVER=https://pebble:14000/dir',
                   '-e', 'REQUESTS_CA_BUNDLE=/tmp/pebble-ca.pem',
                   '-e', 'SUPERVISOR_TOKEN=must-not-reach-the-web-process',
                   '-p', '127.0.0.1::80', '-p', '127.0.0.1::443', '-p', '127.0.0.1::22',
                   '-v', volume + ':/data', args.image)
            copy_options()
            if args.acme:
                docker('cp', str(directory / 'cert.pem'), name + ':/tmp/pebble-ca.pem')
            docker('start', name)
            return mapped_ports()

        def runtime(port, expected, context=None):
            code, _, contents = response(port, context=context)
            return code == 200 and json.loads(contents).get('runtime') == expected

        try:
            docker('volume', 'create', volume)
            docker('network', 'create', network)
            if args.acme:
                # This self-signed certificate secures the local ACME server, not the web app.
                docker('run', '--rm', '-v', str(directory) + ':/certs', '--entrypoint', 'openssl', args.image,
                       'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                       '-keyout', '/certs/key.pem', '-out', '/certs/cert.pem', '-subj', '/CN=pebble',
                       '-addext', 'subjectAltName=DNS:pebble')
                (directory / 'pebble.json').write_text(json.dumps({'pebble': {
                    'listenAddress': '0.0.0.0:14000', 'managementListenAddress': '0.0.0.0:15000',
                    'certificate': '/test/cert.pem', 'privateKey': '/test/key.pem',
                    'httpPort': 80, 'tlsPort': 443}}))
                docker('run', '-d', '--name', pebble, '--network', network, '--network-alias', 'pebble',
                       '-e', 'PEBBLE_VA_NOSLEEP=1', '-v', str(directory) + ':/test:ro',
                       'ghcr.io/letsencrypt/pebble:latest', '-config', '/test/pebble.json')
            ports = start()
            wait_for(lambda: runtime(ports[80], 'node'), 'Express through Nginx')
            welcome = response(ports[80], '/')[2]
            assert '<title>starter-app</title>' in welcome
            assert '/data/workspace/starter-app/server.js' in welcome
            checks.append('express_http')
            ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'UserKnownHostsFile=' + str(directory / 'known_hosts'),
                   '-o', 'ConnectTimeout=5', '-i', str(key), '-p', str(ports[22])]
            host_key = docker('exec', name, 'cat', '/data/ssh/ssh_host_ed25519_key.pub').stdout
            known_host = '[127.0.0.1]:{} {}'.format(ports[22], host_key)
            (directory / 'known_hosts').write_text(known_host)
            wait_for(lambda: run(*ssh, 'vps@127.0.0.1', 'whoami').stdout.strip() == 'vps', 'SSH key login')
            run(*ssh, 'vps@127.0.0.1', 'myvps status && myvps packages list && sudo -n myvps check')
            assert run(*ssh, 'root@127.0.0.1', 'true', check=False).returncode != 0
            assert docker('exec', '-u', 'app', name, 'sudo', '-n', 'true', check=False).returncode != 0
            docker('exec', name, 'bash', '-c', 'echo challenge-ok > /data/certbot/webroot/.well-known/acme-challenge/test')
            assert response(ports[80], '/.well-known/acme-challenge/test')[2].strip() == 'challenge-ok'
            print('PASS root SSH denied, web user unprivileged, ACME HTTP path available', flush=True)
            checks.extend(['ssh_key_auth', 'service_privilege_separation', 'acme_http_path'])
            # Prove Supervisor credentials are absent even when the container receives one.
            pid = docker('exec', name, 'pgrep', '-u', 'app', '-f', '^node server.js$').stdout.strip()
            environment = docker('exec', '-u', 'app', name, 'cat', '/proc/' + pid + '/environ').stdout
            assert 'must-not-reach-the-web-process' not in environment
            docker('exec', name, 'pkill', '-9', '-u', 'app', '-f', '^node server.js$')
            wait_for(lambda: runtime(ports[80], 'node') and
                     docker('exec', name, 'pgrep', '-u', 'app', '-f', '^node server.js$').stdout.strip() != pid,
                     'Express recovers after process crash')
            checks.append('crash_recovery')
            docker('exec', name, 'myvps', 'packages', 'add', 'jq', timeout=240)
            docker('exec', name, 'bash', '-c', 'echo persists > /data/workspace/persistence.txt')
            fingerprint = docker('exec', name, 'ssh-keygen', '-lf', '/data/ssh/ssh_host_ed25519_key.pub').stdout
            docker('rm', '-f', name)
            ports = start()
            wait_for(lambda: runtime(ports[80], 'node'), 'Express after container replacement', timeout=240)
            assert docker('exec', name, 'cat', '/data/workspace/persistence.txt').stdout.strip() == 'persists'
            assert docker('exec', name, 'ssh-keygen', '-lf', '/data/ssh/ssh_host_ed25519_key.pub').stdout == fingerprint
            docker('exec', name, 'jq', '--version')
            print('PASS workspace, SSH host keys, and extra package survive replacement', flush=True)
            checks.append('persistent_data_host_keys_packages')
            docker('exec', name, 'bash', '-c',
                   'cp -r /opt/myvps/examples/dotnet /data/workspace/csharp && '
                   'chown -R 1000:1001 /data/workspace/csharp && chmod -R g+rwX /data/workspace/csharp')
            settings['app_directory'] = '/data/workspace/csharp'
            copy_options()
            docker('restart', '--time', '60', name)
            ports = mapped_ports()
            wait_for(lambda: runtime(ports[80], 'dotnet'), 'C# builds and serves through Nginx', timeout=180)
            checks.append('dotnet_http')
            if args.acme:
                settings['tls'].update(enabled=True, domains=['app.myvps.test', 'api.myvps.test'],
                                       email='admin@example.com')
                copy_options()
                docker('restart', '--time', '60', name)
                ports = mapped_ports()
                # Certificate provisioning is the real Certbot HTTP-01 path, using only local Pebble.
                wait_for(lambda: docker('exec', name, 'test', '-f',
                         '/data/letsencrypt/live/app.myvps.test/fullchain.pem', check=False).returncode == 0,
                         'Certbot issues a certificate using local ACME validation', timeout=120)
                root_pem = docker('exec', name, 'curl', '--cacert', '/tmp/pebble-ca.pem', '-fsS',
                                  'https://pebble:15000/roots/0').stdout
                tls_context = ssl.create_default_context(cadata=root_pem)
                tls_context.check_hostname = False  # The test connects to the ephemeral localhost port.
                wait_for(lambda: runtime(ports[443], 'dotnet', tls_context), 'HTTPS proxies C# with the issued certificate')
                def redirects_to_https():
                    status, headers, _ = response(ports[80], '/health', hostname='app.myvps.test')
                    return status == 301 and headers.get('Location') == 'https://app.myvps.test/health'
                wait_for(redirects_to_https, 'HTTP redirects after the graceful Nginx reload')
                cert = '/data/letsencrypt/live/app.myvps.test/cert.pem'
                for domain in settings['tls']['domains']:
                    result = docker('exec', name, 'openssl', 'x509', '-in', cert, '-noout', '-checkhost', domain)
                    assert 'does match certificate' in result.stdout
                settings['tls']['domains'].append('www.myvps.test')
                copy_options()
                docker('exec', name, 'myvps', 'certificates', timeout=120)
                result = docker('exec', name, 'openssl', 'x509', '-in', cert, '-noout',
                                '-checkhost', 'www.myvps.test')
                assert 'does match certificate' in result.stdout
                named_context = ssl.create_default_context(cadata=root_pem)
                wait_for(lambda: tls_hostname(ports[443], named_context, 'www.myvps.test'),
                         'Expanded certificate is served for the added hostname')
                print('PASS adding a domain updates the existing certificate automatically', flush=True)
                checks.append('acme_domain_list_update')
                before = docker('exec', name, 'openssl', 'x509', '-in', cert, '-noout', '-serial').stdout
                docker('exec', name, 'certbot', 'renew', '--force-renewal', '--non-interactive',
                       '--no-random-sleep-on-renew',
                       '--server', 'https://pebble:14000/dir', '--config-dir', '/data/letsencrypt',
                       '--work-dir', '/data/certbot/work', '--logs-dir', '/data/logs/certbot',
                       '--deploy-hook', 'myvps reload-nginx', timeout=120)
                after = docker('exec', name, 'openssl', 'x509', '-in', cert, '-noout', '-serial').stdout
                assert before != after
                assert runtime(ports[443], 'dotnet', tls_context)
                print('PASS certificate renewal, Nginx reload, and public-port HTTPS redirect', flush=True)
                checks.extend(['local_acme_issuance', 'https_proxy', 'acme_renewal_nginx_reload'])
            results = ROOT / '.test-results'
            results.mkdir(exist_ok=True)
            (results / 'smoke.json').write_text(json.dumps({'image': args.image, 'passed': checks}, indent=2) + '\n')
            print('All {} integration checks passed.'.format(len(checks)), flush=True)
        except Exception as error:
            if isinstance(error, subprocess.CalledProcessError):
                print(error.stderr, flush=True)
            print(docker('logs', '--tail', '70', name, check=False).stdout, flush=True)
            print(docker('exec', name, 'tail', '-50', '/data/logs/web.log', check=False).stdout, flush=True)
            if args.acme:
                print(docker('logs', '--tail', '50', pebble, check=False).stdout, flush=True)
            raise
        finally:
            docker('rm', '-f', name, check=False)
            if args.acme:
                docker('rm', '-f', pebble, check=False)
            docker('volume', 'rm', volume, check=False)
            docker('network', 'rm', network, check=False)


if __name__ == '__main__':
    main()
