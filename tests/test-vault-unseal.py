#!/usr/bin/env python3
"""Exercise unseal stdin handling without accessing a cluster or real keys."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
stdin = sys.stdin.read() if '-i' in args else ''
log = pathlib.Path(os.environ['MOCK_LOG'])
with log.open('a') as f:
    f.write(json.dumps({'args': args, 'stdin': stdin}) + '\n')
args = [a for a in args if not a.startswith('--request-timeout=')]
if args[0] == 'get':
    print(os.environ.get('MOCK_PHASE', 'Running'))
elif 'status' in args:
    if os.environ.get('MOCK_STATUS_EMPTY'):
        sys.exit(1)
    written = pathlib.Path(os.environ['MOCK_WRITTEN']).exists()
    sealed = os.environ.get('MOCK_SEALED', 'true') == 'true'
    if written and not os.environ.get('MOCK_REMAIN_SEALED'):
        sealed = False
    print(json.dumps({'sealed': sealed}))
    sys.exit(2 if sealed else 0)
elif 'write' in args:
    assert args[-4:] == ['write', '-format=json', 'sys/unseal', '-']
    assert '-i' in args
    payload = json.loads(stdin)
    assert list(payload) == ['key']
    if os.environ.get('MOCK_WRITE_FAIL'):
        sys.exit(1)
    pathlib.Path(os.environ['MOCK_WRITTEN']).touch()
    print(json.dumps({'sealed': False}))
else:
    raise AssertionError('Unexpected command')
'''


class UnsealTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vault-unseal-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.key = self.directory / 'unseal-key'
        self.key.write_text('fixture-key+/==\n')
        self.key.chmod(0o600)
        self.log = self.directory / 'calls.jsonl'
        mock = self.directory / 'kubectl'
        mock.write_text(MOCK)
        mock.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.directory) + os.pathsep + os.environ['PATH'],
                        VAULT_UNSEAL_KEY_FILE=str(self.key), VAULT_NAMESPACE='fixture-namespace',
                        VAULT_POD='fixture-vault-0', VAULT_ADDR='http://127.0.0.1:8200',
                        MOCK_LOG=str(self.log), MOCK_WRITTEN=str(self.directory / 'written'))

    def run_helper(self, **env):
        return subprocess.run(['bash', str(ROOT / 'scripts/vault-unseal.sh')],
                              env=dict(self.env, **env), text=True, capture_output=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def writes(self):
        return [x for x in self.calls() if 'write' in x['args']]

    def assert_secret_only_on_stdin(self, key, result):
        writes = self.writes()
        self.assertEqual(len(writes), 1)
        self.assertEqual(json.loads(writes[0]['stdin']), {'key': key})
        self.assertIn('-i', writes[0]['args'])
        self.assertNotIn(key, json.dumps([x['args'] for x in self.calls()]))
        self.assertNotIn(key, result.stdout + result.stderr)

    def test_sealed_server_receives_exact_key_and_is_checked_afterward(self):
        key = 'fixture-"quoted"-\\-$(must-not-run)-+/=='
        self.key.write_text(key + '\n\n')
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_secret_only_on_stdin(key, result)
        self.assertEqual(sum('status' in c['args'] for c in self.calls()), 2)

    def test_unsealed_server_does_not_receive_key(self):
        result = self.run_helper(MOCK_SEALED='false')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.writes(), [])

    def test_missing_or_empty_key_skips_cluster_access(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.key.unlink()
                else:
                    self.key.write_bytes(b'')
                self.assertEqual(self.run_helper().returncode, 0)
                self.assertEqual(self.calls(), [])

    def test_nonrunning_pod_is_skipped(self):
        self.assertEqual(self.run_helper(MOCK_PHASE='Pending').returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_unavailable_status_does_not_trigger_unseal(self):
        self.assertEqual(self.run_helper(MOCK_STATUS_EMPTY='true').returncode, 0)
        self.assertEqual(self.writes(), [])

    def test_failed_unseal_is_reported(self):
        self.assertNotEqual(self.run_helper(MOCK_WRITE_FAIL='true').returncode, 0)

    def test_remaining_sealed_is_reported(self):
        result = self.run_helper(MOCK_REMAIN_SEALED='true')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Vault remains sealed', result.stderr)

    def test_installer_unseal_streams_the_same_anonymous_api_request(self):
        source = (ROOT / 'scripts/configure-vault.sh').read_text()
        blocks = re.findall(r'if \[\[ "\$sealed" == "true" \]\]; then\n.*?\nfi', source, re.S)
        self.assertEqual(len(blocks), 1)
        key = 'fixture-installer-"quoted"-+/=='
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', 'info() { :; }\n' + blocks[0]],
            env=dict(self.env, sealed='true', unseal_key=key, NAMESPACE='fixture-namespace',
                     VAULT_POD='fixture-vault-0', VAULT_ADDR='http://127.0.0.1:8200'),
            text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_secret_only_on_stdin(key, result)



class RecoveryKitTests(unittest.TestCase):
    """The offline recovery kit decrypts, holds the keys and never exposes them in arguments."""

    PASSPHRASE = "correct-horse-battery-staple-42"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        state, data, bin_dir = self.dir / "state", self.dir / "k3s/server/cred", self.dir / "bin"
        for directory in (state, data, bin_dir):
            directory.mkdir(parents=True)
        (state / "vault-unseal-key").write_text("unseal-key-fixture-value\n")
        (state / "vault-bootstrap-token").write_text("hvs.bootstrap-fixture-value\n")
        (self.dir / "k3s/server/token").write_text("k3s-token-fixture-value\n")
        (data / "encryption-config.json").write_text('{"fixture": true}\n')
        (self.dir / "backup.env").write_text("RESTIC_PASSWORD=restic-fixture-value\n")
        log = self.dir / "argv.log"
        # sudo runs the command as this user (dropping -o/-g); openssl logs its arguments.
        (bin_dir / "sudo").write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "sudo $*" >> {log}
if [[ "$1" == install ]]; then
  shift; args=(); while (( $# )); do case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac; done
  exec install "${{args[@]}}"
fi
exec "$@"
""")
        (bin_dir / "openssl").write_text(f"""#!/usr/bin/env bash
printf '%s\\n' "openssl $*" >> {log}
exec /usr/bin/openssl "$@"
""")
        for shim in ("sudo", "openssl"):
            (bin_dir / shim).chmod(0o700)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(self.dir),
                    "SWIRL_CLOUD_STATE_DIR": str(state), "K3S_DATA_DIR": str(self.dir / "k3s"),
                    "SWIRL_CLOUD_BACKUP_ENV": str(self.dir / "backup.env")}
        self.log, self.state = log, state

    def tearDown(self):
        self.temp.cleanup()

    def run_kit(self, *args, answers):
        import pty
        import select
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(str(ROOT / "scripts/export-recovery-kit.sh"), ["export-recovery-kit.sh", *args], self.env)
        output, pending = b"", list(answers)
        while True:
            ready, _, _ = select.select([fd], [], [], 30)
            if not ready:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            output += chunk
            if pending and chunk.rstrip().endswith(b":"):
                os.write(fd, pending.pop(0).encode() + b"\n")
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status), output.decode(errors="replace")

    def test_kit_round_trips_and_keys_never_reach_arguments(self):
        kit = self.dir / "kit.enc"
        code, output = self.run_kit("--output", str(kit), "--separate-archives", answers=[self.PASSPHRASE] * 2)
        self.assertEqual(0, code, output)
        self.assertEqual(0o600, kit.stat().st_mode & 0o777)
        clear = subprocess.run(["/usr/bin/openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-pass", "stdin",
                                "-in", str(kit)], input=self.PASSPHRASE.encode(), capture_output=True, check=True).stdout
        listing = subprocess.run(["tar", "-tf", "-"], input=clear, capture_output=True, check=True).stdout.decode()
        for name in ("vault-unseal-key", "vault-bootstrap-token", "token", "encryption-config.json", "backup.env"):
            self.assertIn("kit/" + name, listing)
        manifest = json.loads((self.state / "recovery-kit.json").read_text())
        self.assertTrue(manifest["separateArchives"])
        self.assertIn(str(self.state / "vault-unseal-key"), manifest["fingerprints"])
        arguments = self.log.read_text()
        for secret in (self.PASSPHRASE, "unseal-key-fixture-value", "hvs.bootstrap-fixture-value", "restic-fixture-value"):
            self.assertNotIn(secret, arguments)
        self.assertNotIn(self.PASSPHRASE, output)
        code, output = self.run_kit("--verify", str(kit), answers=[self.PASSPHRASE])
        self.assertEqual(0, code, output)
        (self.state / "vault-unseal-key").write_text("rotated\n")
        code, output = self.run_kit("--verify", str(kit), answers=[self.PASSPHRASE])
        self.assertEqual(1, code)
        self.assertIn("no longer matches", output)
        code, output = self.run_kit("--verify", str(kit), answers=["wrong-passphrase-entirely"])
        self.assertIn("Wrong passphrase", output)

    def test_short_or_mismatched_passphrases_write_nothing(self):
        kit = self.dir / "kit.enc"
        for answers in (["short"], [self.PASSPHRASE, self.PASSPHRASE + "x"]):
            code, _ = self.run_kit("--output", str(kit), answers=answers)
            self.assertEqual(1, code)
            self.assertFalse(kit.exists())
            self.assertFalse((self.state / "recovery-kit.json").exists())

if __name__ == '__main__':
    unittest.main()
