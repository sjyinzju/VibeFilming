# Run locally in PowerShell. Password entry stays in SSH's interactive prompt.
# This installs only the existing public key; it never reads or copies the private key.
param(
    [string]$PublicKeyPath = 'C:\Users\shiju\.ssh\id_ed25519.pub',
    [string]$PrivateKeyPath = 'C:\Users\shiju\.ssh\id_ed25519'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PrivateKeyPath -PathType Leaf)) {
    throw 'The specified private key file does not exist.'
}
$sparkPublicKey = (Get-Content -Raw -LiteralPath $PublicKeyPath).Trim()
if ($sparkPublicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: .*)?$') {
    throw 'The existing public key is not a valid single-line Ed25519 public key.'
}
$sparkFingerprintOutput = & ssh-keygen -lf $PublicKeyPath -E sha256
if ($LASTEXITCODE -ne 0) {
    throw 'ssh-keygen could not validate the local public key. Nothing was sent.'
}
$sparkExpectedFingerprint = [regex]::Match(
    ($sparkFingerprintOutput -join "`n"), '(?m)^256 (SHA256:[A-Za-z0-9+/]{43}) '
).Groups[1].Value
if (-not $sparkExpectedFingerprint) {
    throw 'Could not read the local Ed25519 SHA256 fingerprint. Nothing was sent.'
}

# Absolute remote paths avoid tilde expansion / Markdown-copy escaping issues.
# POSIX single quotes survive Windows PowerShell 5.1 native argument passing.
# Do not replace them with embedded double quotes: that would make tr delete 'r'.
$sparkInstallCommand = @'
set -eu
umask 077
sparkKeyFile=/home/Developer/.ssh/authorized_keys
mkdir -p /home/Developer/.ssh
if [ -e $sparkKeyFile ]; then
    sparkBackup=$(mktemp /home/Developer/.ssh/authorized_keys.backup.XXXXXXXX)
    cp -p -- $sparkKeyFile $sparkBackup
    printf 'AUTHORIZED_KEYS_BACKUP=%s\n' $sparkBackup
fi
# Preserve every existing byte, including a malformed last line without a newline.
printf '\n' >> $sparkKeyFile
tr -d '\r' >> $sparkKeyFile
chmod 700 /home/Developer/.ssh
chmod 600 $sparkKeyFile
ssh-keygen -lf $sparkKeyFile -E sha256 | awk '{print $2}' | grep -Fx -- '__SPARK_FINGERPRINT__'
echo PUBLIC_KEY_FINGERPRINT_VERIFIED
'@.Replace('__SPARK_FINGERPRINT__', $sparkExpectedFingerprint).Replace("`r", '')

$sparkSshArguments = @('-p', '6081', '-i', $PrivateKeyPath, '-o', 'IdentitiesOnly=yes')
Write-Host 'Enter the Spark password only when SSH asks. Nothing is saved in this script.'
$sparkInstallOutput = $sparkPublicKey | & ssh @sparkSshArguments Developer@106.13.186.155 $sparkInstallCommand
$sparkInstallExitCode = $LASTEXITCODE
$sparkInstallOutput | ForEach-Object { Write-Host $_ }
if ($sparkInstallExitCode -ne 0 -or $sparkInstallOutput -notcontains 'PUBLIC_KEY_FINGERPRINT_VERIFIED') {
    throw 'Remote public-key fingerprint verification failed. Existing keys were not overwritten; inspect the printed backup path.'
}

& ssh @sparkSshArguments -o BatchMode=yes -o PreferredAuthentications=publickey -o ConnectTimeout=10 Developer@106.13.186.155 hostname
if ($LASTEXITCODE -ne 0) {
    throw 'The remote fingerprint matched, but key-only login with the specified private key still failed. Please report this output without a password.'
}
Write-Host 'KEY_LOGIN_VERIFIED - Codex can now continue Layer 1 deployment.'
