#!/usr/bin/env python3
"""Prove containment against a temporary device, never the user's home devices."""
import argparse
import ipaddress
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import uuid

from smoke_test import docker, run, wait_for, response, ROOT


DEVICE = '''
import http.server, socket, threading
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'simulated-device')
class V6(http.server.ThreadingHTTPServer):
    address_family=socket.AF_INET6
    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1)
        super().server_bind()
for server in (http.server.ThreadingHTTPServer(('0.0.0.0',18080),Handler),V6(('::',18080),Handler)):
    threading.Thread(target=server.serve_forever,daemon=True).start()
udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
udp.bind(('0.0.0.0',1900))
print('device-ready',flush=True)
while True:
    payload,address=udp.recvfrom(4096)
    print('UDP_RECEIVED',payload.decode(errors='replace'),flush=True)
    udp.sendto(b'unexpected-local-access',address)
'''


def blocked_probe(address, port, udp=False):
    return f'''
import errno,socket
connection=socket.socket({'socket.AF_INET6' if ':' in address else 'socket.AF_INET'},
                         {'socket.SOCK_DGRAM' if udp else 'socket.SOCK_STREAM'})
connection.settimeout(3)
try:
    connection.connect(({address!r},{port}))
    if {udp!r}:
        connection.send(b'myvps-must-not-reach-device')
        connection.recv(128)
except OSError as error:
    assert error.errno in (errno.EACCES,errno.EPERM,errno.EHOSTUNREACH),repr(error)
    print('blocked',error.errno)
else:
    raise SystemExit('UNEXPECTED LOCAL NETWORK ACCESS')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='myvps:dev')
    args = parser.parse_args()
    prefix = 'myvps-firewall-' + uuid.uuid4().hex[:8]
    network, device, name, missing, discovery = [prefix + suffix for suffix in
        ('-net', '-device', '-app', '-missing-cap', '-discovery-fails')]
    checks = []
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        directory = Path(temporary)
        key = directory / 'key'
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key))
        settings = {'ssh_authorized_keys': [key.with_suffix('.pub').read_text().strip()],
                    'network_protection': {'enabled': True, 'public_ipv4': '1.1.1.1',
                                           'blocked_ipv4_networks': []}}
        options = directory / 'options.json'
        options.write_text(json.dumps(settings))
        options.chmod(0o600)
        try:
            subnet = 'fd' + uuid.uuid4().hex[:2] + ':' + uuid.uuid4().hex[:4] + '::/64'
            docker('network', 'create', '--ipv6', '--subnet', subnet, network)
            docker('run', '-d', '--name', device, '--network', network,
                   '--entrypoint', '/opt/myvps-tools/bin/python', args.image, '-u', '-c', DEVICE)
            wait_for(lambda: 'device-ready' in docker('logs', device).stdout, 'Simulated local device ready')
            addresses = json.loads(docker('inspect', device).stdout)[0]['NetworkSettings']['Networks'][network]
            ipv4, ipv6 = addresses['IPAddress'], addresses['GlobalIPv6Address']
            assert ipaddress.ip_address(ipv4).is_private
            for url in (f'http://{ipv4}:18080', f'http://[{ipv6}]:18080'):
                assert 'simulated-device' in docker('run', '--rm', '--network', network,
                    '--entrypoint', 'curl', args.image, '--noproxy', '*', '--max-time', '5', '-fsS', url).stdout

            # An app without the startup capability must never launch services.
            docker('create', '--name', missing, '--network', network, '-v', '/data', args.image)
            docker('cp', str(options), missing + ':/data/options.json')
            docker('start', missing)
            wait_for(lambda: docker('inspect', '--format', '{{.State.Running}}', missing).stdout.strip() == 'false',
                     'Missing NET_ADMIN fails closed')
            assert 'refusing to start' in docker('logs', missing).stdout
            checks.append('startup_fail_closed')

            blocked_options = directory / 'blocked-options.json'
            blocked_options.write_text(json.dumps({'network_protection': {
                'enabled': True, 'public_ipv4': '', 'blocked_ipv4_networks': ['0.0.0.0/0']}}))
            blocked_options.chmod(0o600)
            docker('create', '--name', discovery, '--network', network,
                   '--cap-add', 'NET_ADMIN', '-v', '/data', args.image)
            docker('cp', str(blocked_options), discovery + ':/data/options.json')
            docker('start', discovery)
            wait_for(lambda: docker('inspect', '--format', '{{.State.Running}}', discovery).stdout.strip() == 'false',
                     'Public-address discovery failure also fails closed')
            assert 'could not determine the public IPv4 address; refusing to start' in docker('logs', discovery).stdout
            checks.append('discovery_fail_closed')

            docker('create', '--name', name, '--network', network, '--cap-add', 'NET_ADMIN',
                   '--memory', '1g', '-v', '/data', '-p', '127.0.0.1::80', '-p', '127.0.0.1::22', args.image)
            docker('cp', str(options), name + ':/data/options.json')
            docker('start', name)
            ports = json.loads(docker('inspect', '--format', '{{json .NetworkSettings.Ports}}', name).stdout)
            http_port = int(ports['80/tcp'][0]['HostPort'])
            ssh_port = int(ports['22/tcp'][0]['HostPort'])
            wait_for(lambda: response(http_port)[0] == 200, 'Incoming HTTP works with network protection')
            host_key = docker('exec', name, 'cat', '/data/ssh/ssh_host_ed25519_key.pub').stdout
            known_hosts = directory / 'known_hosts'
            known_hosts.write_text(f'[127.0.0.1]:{ssh_port} {host_key}')
            ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'UserKnownHostsFile=' + str(known_hosts), '-o', 'ConnectTimeout=5',
                   '-i', str(key), '-p', str(ssh_port), 'vps@127.0.0.1']
            wait_for(lambda: run(*ssh, 'whoami').stdout.strip() == 'vps', 'Incoming SSH works with network protection')
            app_ipv6 = json.loads(docker('inspect', name).stdout)[0]['NetworkSettings']['Networks'][network]['GlobalIPv6Address']
            ipv6_echo = '''
import http.server,socket
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ipv6-reply-ok')
class Server(http.server.ThreadingHTTPServer):
    address_family=socket.AF_INET6
Server(('::',18081),Handler).serve_forever()
'''
            docker('exec', '-d', '-u', 'app', name, '/opt/myvps-tools/bin/python', '-u', '-c', ipv6_echo)
            wait_for(lambda: 'ipv6-reply-ok' in docker('exec', device, 'curl', '--noproxy', '*',
                     '--max-time', '5', '-fsS', f'http://[{app_ipv6}]:18081').stdout,
                     'Replies to incoming IPv6 connections work')
            checks.append('incoming_http_ssh')

            def managed_python(code, as_app=False):
                command = ['sudo', '-n', *(['-u', 'app'] if as_app else []),
                           '/opt/myvps-tools/bin/python', '-c', code]
                return run(*ssh, shlex.join(command))

            def rejects(family):
                document = json.loads(docker('exec', name, 'nft', '-j', 'list', 'chain',
                                            'inet', 'myvps', 'output').stdout)
                total = 0
                for entry in document['nftables']:
                    rule = entry.get('rule', {})
                    kind = 'ipv4' if '@blocked_ipv4' in json.dumps(rule) else 'ipv6'
                    if kind == family:
                        total += sum(expression.get('counter', {}).get('packets', 0)
                                     for expression in rule.get('expr', []))
                return total

            def assert_blocked(target, port, udp=False, as_app=True):
                family = 'ipv6' if ':' in target and not target.startswith('::ffff:') else 'ipv4'
                before = rejects(family)
                assert 'blocked' in managed_python(blocked_probe(target, port, udp), as_app).stdout
                # Reject errno differs across kernels. Require an actual firewall
                # counter hit so a missing route cannot give a false pass.
                assert rejects(family) > before, f'{target} failed without hitting the firewall'

            for as_app in (True, False):
                for target, port, udp in ((ipv4, 18080, False), (ipv4, 1900, True),
                                          (ipv6, 18080, False), ('::ffff:' + ipv4, 18080, False),
                                          ('1.1.1.1', 443, False)):
                    assert_blocked(target, port, udp, as_app)
            print('PASS app and SSH sudo cannot open local TCP/UDP, IPv6, mapped IPv4, or public-IP loopback', flush=True)
            checks.append('blocked_local_networks_and_public_ip')

            # Verify global IPv6 is rejected, not merely ULA/link-local addresses.
            assert_blocked('2001:4860:4860::8888', 53)
            checks.append('new_outbound_ipv6_blocked')

            firewall_change = run(*ssh, 'sudo -n nft flush table inet myvps', check=False)
            assert firewall_change.returncode != 0
            # Some setpriv versions silently ignore attempts to add a bounding
            # capability. Test the attempted privileged operation, not its parser.
            cap_raise = run(*ssh, 'sudo -n setpriv --bounding-set=+net_admin nft flush table inet myvps', check=False)
            assert cap_raise.returncode != 0
            raw_socket = managed_python('''
import errno,socket
try:
    socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(3))
except OSError as error:
    assert error.errno in (errno.EPERM,errno.EACCES)
    print('raw sockets denied')
else:
    raise SystemExit('UNEXPECTED RAW PACKET ACCESS')
''')
            assert 'raw sockets denied' in raw_socket.stdout
            process = docker('exec', name, 'pgrep', '-u', 'app', '-f', '^node server.js$').stdout.strip()
            status = docker('exec', name, 'cat', f'/proc/{process}/status').stdout
            for line in status.splitlines():
                if line.startswith(('CapBnd:', 'CapEff:', 'CapPrm:', 'CapInh:', 'CapAmb:')):
                    assert int(line.split()[1], 16) & ((1 << 12) | (1 << 13)) == 0, line
            print('PASS firewall and raw-packet privileges cannot be recovered through SSH sudo or the web process', flush=True)
            checks.append('runtime_capabilities_removed')

            public = managed_python('''
import socket,urllib.request
assert socket.getaddrinfo('example.com',443)
with urllib.request.urlopen('https://example.com',timeout=20) as response:
    assert response.status==200
print('public DNS and HTTPS work')
''', True)
            assert 'public DNS and HTTPS work' in public.stdout
            checks.append('public_dns_https_allowed')
            assert 'UDP_RECEIVED' not in docker('logs', device).stdout
            checks.append('no_udp_reaches_device')

            docker('restart', '--time', '60', name)
            ports = json.loads(docker('inspect', '--format', '{{json .NetworkSettings.Ports}}', name).stdout)
            http_port = int(ports['80/tcp'][0]['HostPort'])
            ssh_port = int(ports['22/tcp'][0]['HostPort'])
            ssh[ssh.index('-p') + 1] = str(ssh_port)
            known_hosts.write_text(f'[127.0.0.1]:{ssh_port} {host_key}')
            wait_for(lambda: response(http_port)[0] == 200, 'Firewall survives app restart')
            assert_blocked(ipv4, 18080)
            checks.append('restart_preserves_policy')
            results = ROOT / '.test-results'
            results.mkdir(exist_ok=True)
            (results / 'firewall.json').write_text(json.dumps({'image': args.image, 'passed': checks}, indent=2) + '\n')
            print(f'All {len(checks)} network containment checks passed.', flush=True)
        except Exception as error:
            if isinstance(error, subprocess.CalledProcessError):
                print(error.stderr, flush=True)
            print(docker('logs', '--tail', '100', name, check=False).stdout, flush=True)
            print(docker('exec', name, 'nft', 'list', 'table', 'inet', 'myvps', check=False).stdout, flush=True)
            raise
        finally:
            for container in (name, device, missing, discovery):
                docker('rm', '-f', '-v', container, check=False)
            docker('network', 'rm', network, check=False)


if __name__ == '__main__':
    main()
