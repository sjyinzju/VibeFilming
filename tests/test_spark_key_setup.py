"""Offline regression for the real PowerShell -> native argv -> POSIX shell seam."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_spark_key.ps1"
POWERSHELLS = [name for name in ("powershell", "pwsh") if shutil.which(name)]
SHELL_PARAMS = POWERSHELLS or [pytest.param("missing", marks=pytest.mark.skip(reason="PowerShell unavailable"))]


def public_key(offset=0):
    algorithm = b"ssh-ed25519"
    blob = struct.pack(">I", len(algorithm)) + algorithm + struct.pack(">I", 32) + bytes(range(offset, offset + 32))
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
    return b"ssh-ed25519 " + base64.b64encode(blob) + b" synthetic-test-key", fingerprint


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def native_install_command(powershell, fingerprint):
    # Evaluate only the actual remote-command assignment, never run the installer or SSH.
    source = f"""
$ProgressPreference = 'SilentlyContinue'
$taskTokens = $null
$taskErrors = $null
$taskAst = [System.Management.Automation.Language.Parser]::ParseFile({ps_literal(SCRIPT)}, [ref]$taskTokens, [ref]$taskErrors)
if ($taskErrors.Count) {{ throw 'PowerShell parse error' }}
$taskAssignment = $taskAst.Find({{ param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$sparkInstallCommand' }}, $true)
$sparkExpectedFingerprint = {ps_literal(fingerprint)}
Invoke-Expression $taskAssignment.Extent.Text
& {ps_literal(sys.executable)} -c 'import sys,json; print(json.dumps(sys.argv[1]))' $sparkInstallCommand
"""
    encoded = base64.b64encode(source.encode("utf-16-le")).decode()
    result = subprocess.run([powershell, "-NoProfile", "-EncodedCommand", encoded],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


@pytest.mark.parametrize("powershell", SHELL_PARAMS)
def test_carriage_return_filter_survives_native_argument_passing(powershell):
    _, fingerprint = public_key()
    command = native_install_command(powershell, fingerprint)
    match = re.search(r"tr -d (.+?) >>", command)
    assert match is not None
    assert shlex.split("tr -d " + match[1]) == ["tr", "-d", r"\r"]


def posix_shell():
    if os.name != "nt":
        shell = shutil.which("sh")
        if shell and shutil.which("ssh-keygen"):
            return shell, os.environ.copy()
    else:
        for candidate in (Path.home() / "scoop/apps/git/current/bin/sh.exe",
                          Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/sh.exe"):
            if candidate.is_file():
                env = os.environ.copy()
                env["PATH"] = str(candidate.parent.parent / "usr/bin") + os.pathsep + env["PATH"]
                return str(candidate), env
    pytest.skip("A POSIX shell with coreutils and ssh-keygen is required for offline remote simulation")


def run_in_local_sandbox(command, content, directory):
    shell, env = posix_shell()
    if " " in str(directory):
        pytest.skip("Remote script uses the verified, space-free Spark home path")
    # ONLY this temporary directory is modified; never touch a real authorized_keys.
    command = command.replace("/home/Developer/.ssh", directory.as_posix())
    assert "/home/Developer/" not in command
    return subprocess.run([shell, "-c", command], input=content, capture_output=True,
                          timeout=20, env=env)


@pytest.mark.parametrize("powershell", SHELL_PARAMS)
def test_backup_and_append_preserve_existing_bytes_and_verify_fingerprint(powershell, tmp_path):
    new_key, fingerprint = public_key()
    existing_key, _ = public_key(32)
    original = existing_key + b"\ninvalid-old-entry-without-final-newline"
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_bytes(original)
    command = native_install_command(powershell, fingerprint)
    assert "\r" not in command

    for run_number in (1, 2):
        before = authorized_keys.read_bytes()
        result = run_in_local_sandbox(command, new_key + b"\r\n", tmp_path)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert b"PUBLIC_KEY_FINGERPRINT_VERIFIED" in result.stdout
        assert fingerprint.encode() in result.stdout
        backup_line = next(line for line in result.stdout.decode().splitlines()
                           if line.startswith("AUTHORIZED_KEYS_BACKUP="))
        backup = Path(backup_line.split("=", 1)[1])
        assert backup.parent.resolve() == tmp_path.resolve()
        assert backup.read_bytes() == before
        assert len(list(tmp_path.glob("authorized_keys.backup.*"))) == run_number
        assert authorized_keys.read_bytes() == before + b"\n" + new_key + b"\n"


@pytest.mark.parametrize("powershell", SHELL_PARAMS)
def test_wrong_remote_fingerprint_cannot_report_success(powershell, tmp_path):
    new_key, _ = public_key()
    _, different_fingerprint = public_key(32)
    command = native_install_command(powershell, different_fingerprint)
    result = run_in_local_sandbox(command, new_key + b"\r\n", tmp_path)
    assert result.returncode != 0
    assert b"PUBLIC_KEY_FINGERPRINT_VERIFIED" not in result.stdout


@pytest.mark.parametrize("powershell", SHELL_PARAMS)
@pytest.mark.parametrize("valid_public_key", [True, False])
def test_installer_checks_public_key_and_pins_private_identity(powershell, valid_public_key, tmp_path):
    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen is required")
    key, _ = public_key()
    public_path, private_path = tmp_path / "test.pub", tmp_path / "test-private-placeholder"
    public_path.write_bytes(key if valid_public_key else b"ssh-ed25519 AAAA invalid-fixture")
    # No real private key is generated, read, copied or used by this test.
    private_path.write_text("not a private key; SSH is intercepted", encoding="ascii")
    invocation_log = tmp_path / "ssh-invocations.jsonl"
    source = f"""
$ProgressPreference = 'SilentlyContinue'
$taskInvocationLog = {ps_literal(invocation_log)}
function ssh {{
    [IO.File]::AppendAllText($taskInvocationLog, (ConvertTo-Json -InputObject @($args) -Compress) + "`n")
    if (@($args) -contains 'BatchMode=yes') {{ Write-Output 'TEST_HOSTNAME' }}
    else {{ Write-Output 'PUBLIC_KEY_FINGERPRINT_VERIFIED' }}
    $global:LASTEXITCODE = 0
}}
& {ps_literal(SCRIPT)} -PublicKeyPath {ps_literal(public_path)} -PrivateKeyPath {ps_literal(private_path)}
"""
    encoded = base64.b64encode(source.encode("utf-16-le")).decode()
    result = subprocess.run([powershell, "-NoProfile", "-EncodedCommand", encoded],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    if not valid_public_key:
        assert result.returncode != 0 and not invocation_log.exists()
        assert "ssh-keygen could not validate the local public key" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    assert "KEY_LOGIN_VERIFIED" in result.stdout
    invocations = [json.loads(line) for line in invocation_log.read_text().splitlines()]
    assert len(invocations) == 2
    for args in invocations:
        assert args[args.index("-i") + 1] == str(private_path)
        assert args[args.index("-p") + 1] == "6081"
        assert "IdentitiesOnly=yes" in args and "Developer@106.13.186.155" in args
    assert "BatchMode=yes" in invocations[1]
    assert "PreferredAuthentications=publickey" in invocations[1]
    assert invocations[1][-1] == "hostname"
