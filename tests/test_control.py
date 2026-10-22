"""Checks for boundaries that are hard to cover through ordinary HTTP requests."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('control', '/opt/myvps/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.patch = patch.object(control, 'DATA', self.data)
        self.patch.start()
        self.settings = {**control.DEFAULTS, 'app_directory': str(self.data / 'workspace/starter-app'),
                         'tls': {**control.DEFAULTS['tls']}}

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def save(self):
        (self.data / 'options.json').write_text(json.dumps(self.settings))

    def test_rejects_paths_outside_workspace_including_symlinks(self):
        for directory in ('/etc', str(self.data / 'workspace/../../etc')):
            self.settings['app_directory'] = directory
            self.save()
            with self.assertRaises(ValueError):
                control.options()
        (self.data / 'workspace').mkdir()
        (self.data / 'workspace/escape').symlink_to('/etc', target_is_directory=True)
        self.settings['app_directory'] = str(self.data / 'workspace/escape')
        self.save()
        with self.assertRaises(ValueError):
            control.options()

    def test_rejects_package_flags_and_nginx_injection(self):
        for package in ('--allowerasing', 'git; id', '../payload.rpm'):
            with self.assertRaises(ValueError):
                control.validate_package(package)
        self.settings['tls'] = {'enabled': True, 'domain': 'example.com; include /etc/*;',
                                'email': 'me@example.com'}
        self.save()
        with self.assertRaises(ValueError):
            control.options()

    def test_https_redirect_uses_public_port_and_keeps_http_challenge(self):
        self.settings['tls'].update(enabled=True, domain='app.example.com', email='me@example.com')
        certificate, key = control.cert_paths(self.settings)
        certificate.parent.mkdir(parents=True)
        certificate.touch()
        key.touch()
        rendered = control.nginx_config(self.settings)
        self.assertIn('https://$host$request_uri', rendered)
        self.assertNotIn(':8443', rendered)
        self.assertIn('location ^~ /.well-known/acme-challenge/', rendered)
        self.assertIn('listen 443 ssl;', rendered)

    def test_missing_certificate_keeps_validation_available(self):
        self.settings['tls'].update(enabled=True, domain='app.example.com', email='me@example.com')
        rendered = control.nginx_config(self.settings)
        self.assertIn('return 503', rendered)
        self.assertIn('/.well-known/acme-challenge/', rendered)
        self.assertNotIn('listen 443 ssl;', rendered)

    def test_domain_list_is_normalized_and_used_for_nginx(self):
        self.settings['tls'].update(enabled=True, domains=[
            'APP.example.com', 'www.example.com', 'app.example.com'], email='me@example.com')
        self.save()
        settings = control.options()
        self.assertEqual(settings['tls']['domains'], ['app.example.com', 'www.example.com'])
        self.assertIn('server_name app.example.com www.example.com;', control.nginx_config(settings))

    def test_empty_and_malformed_domain_lists_are_rejected(self):
        self.settings['tls'].update(enabled=True, email='me@example.com')
        for domains in ([], 'app.example.com', [42], ['*.example.com']):
            self.settings['tls']['domains'] = domains
            self.save()
            with self.assertRaises(ValueError):
                control.options()

    def test_adding_a_domain_reissues_the_same_certificate(self):
        self.settings['tls'].update(enabled=True, domains=['app.example.com', 'www.example.com'],
                                    email='me@example.com')
        self.save()
        certificate, key = control.cert_paths(self.settings)
        certificate.parent.mkdir(parents=True)
        certificate.touch()
        key.touch()
        control.write(self.data / 'letsencrypt/myvps-server.json', json.dumps({
            'app.example.com': 'https://acme-v02.api.letsencrypt.org/directory'}))
        with patch.object(control, 'root_required'), patch.object(control, 'reload_nginx'), \
                patch.object(control.subprocess, 'run') as run, patch.dict(os.environ, {}, clear=True):
            control.certificates()
        arguments = run.call_args.args[0]
        self.assertIn('--renew-with-new-domains', arguments)
        self.assertEqual([arguments[i + 1] for i, value in enumerate(arguments) if value == '-d'],
                         ['app.example.com', 'www.example.com'])

    def test_switching_staging_to_production_reissues_certificate(self):
        self.settings['tls'].update(enabled=True, domain='app.example.com', email='me@example.com')
        self.save()
        certificate, key = control.cert_paths(self.settings)
        certificate.parent.mkdir(parents=True)
        certificate.touch()
        key.touch()
        control.write(self.data / 'letsencrypt/myvps-server.json', json.dumps({
            'app.example.com': 'https://acme-staging-v02.api.letsencrypt.org/directory'}))
        with patch.object(control, 'root_required'), patch.object(control, 'reload_nginx'), \
                patch.object(control.subprocess, 'run') as run, patch.dict(os.environ, {}, clear=True):
            control.certificates()
        arguments = run.call_args.args[0]
        self.assertIn('certonly', arguments)
        self.assertIn('--force-renewal', arguments)
        self.assertIn('https://acme-v02.api.letsencrypt.org/directory', arguments)


if __name__ == '__main__':
    unittest.main(verbosity=2)
