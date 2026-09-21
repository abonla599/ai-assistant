# Creates (once) the signing key that pins every released APK to the same identity.
#
# WHY THIS EXISTS: Android decides whether an update can be installed by comparing the
# SIGNATURE, not the version number. The release workflow used to run `assembleDebug`,
# and each GitHub runner fabricates its own debug keystore, so v0.15 and v0.16 shipped
# with different certificates. Result: "check for update" could never install over an
# existing copy - users had to uninstall first. Debug builds are also marked
# android:debuggable, which lets anyone with adb read the tokens the shell stores.
#
# RUN IT ONCE, ON THIS MACHINE, AND BACK THE FOLDER UP.
# Losing this keystore means every future release is a fresh signature again, and
# nobody who has the current app installed can update in place - ever. There is no
# recovery path and no way to rotate it without that same one-time breakage.
#
# After it finishes, paste the four printed values into the repository at:
#   Settings -> Secrets and variables -> Actions -> New repository secret
#   APK_KEYSTORE_BASE64   APK_KEYSTORE_PASSWORD   APK_KEY_ALIAS   APK_KEY_PASSWORD
param(
    [string] $Dir = (Join-Path $env:USERPROFILE "ai-assistant-keystore"),
    [string] $Alias = "ai-assistant"
)

$ErrorActionPreference = "Stop"

function Find-Keytool {
    $candidates = @()
    if ($env:JAVA_HOME) { $candidates += (Join-Path $env:JAVA_HOME "bin\keytool.exe") }
    $where = Get-Command keytool.exe -ErrorAction SilentlyContinue
    if ($where) { $candidates += $where.Source }
    $candidates += Get-ChildItem "C:\Program Files\Java" -Filter "keytool.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    throw "keytool.exe not found. Install a JDK (any 17+) and re-run this script."
}

$keytool = Find-Keytool
$keystore = Join-Path $Dir "release.jks"

if (Test-Path $keystore) {
    Write-Output "REFUSING TO OVERWRITE: $keystore already exists."
    Write-Output "That file is the identity of every APK you have ever shipped. Regenerating it"
    Write-Output "would make all currently installed copies un-upgradable (uninstall required)."
    Write-Output "If you are sure you lost it, move the old file away first, then re-run."
    exit 2
}

New-Item -ItemType Directory -Path $Dir -Force | Out-Null

# Cryptographically random, 32 chars from a 65-symbol alphabet (~195 bits).
#
# NOT Get-Random: that is System.Random, a seeded PRNG whose state an attacker can
# enumerate. These two passwords are the only thing standing between whoever ends up
# holding release.jks and the ability to publish updates to every installed phone -
# and that particular failure cannot be undone. The entropy has to come from the OS
# CSPRNG, not from a shuffle of a character range.
function New-Secret {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789!@#%^*_-+="
    -join ($bytes | ForEach-Object { $alphabet[[int]$_ % $alphabet.Length] })
}
$storePass = New-Secret
# Same password for the key and the store, on purpose: keytool writes PKCS12 by
# default, and PKCS12 has no separate key password - it prints a warning and quietly
# uses the store password instead. Two different values here would hand back a
# keystore that Gradle cannot sign with, which is exactly the "it built, it just
# can't be installed" class of problem this script exists to end.
$keyPass = $storePass

# 3072-bit RSA: 2048 is only the floor Android accepts, and this key has to stay
# trustworthy for the whole lifetime of every installed copy.
#
# 10000 days: Android refuses to install an APK whose signing certificate expires
# before 2033, so a short validity would silently brick future releases.
& $keytool -genkeypair -v `
    -keystore $keystore -storetype PKCS12 -storepass $storePass `
    -keypass $keyPass -alias $Alias -keyalg RSA -keysize 3072 -validity 10000 `
    -dname "CN=ai-assistant, OU=app, O=fenever, L=NA, ST=NA, C=CN"
if ($LASTEXITCODE -ne 0) { throw "keytool failed with exit code $LASTEXITCODE" }

# The .jks is worthless without the passwords, but a readable copy of both on a
# shared machine turns "someone got my disk" into "someone can ship updates to my
# users". Strip inherited ACLs and keep only this account.
icacls $keystore /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null

$fingerprint = (& $keytool -list -keystore $keystore -storepass $storePass -alias $Alias -v |
    Select-String "SHA256:").Line.Trim()

$bytes = [System.IO.File]::ReadAllBytes($keystore)
$b64 = [System.Convert]::ToBase64String($bytes)

# The four values go into a file next to the keystore, not onto stdout. Printing
# them means they live in terminal scrollback, in any session recording, and the
# moment someone pastes them wrong there is no copy to re-read - while the file can
# be locked to this account with the same ACL as the keystore itself.
$secretsFile = Join-Path $Dir "secrets-to-paste.txt"
@(
    "Paste these into GitHub: repo -> Settings -> Secrets and variables -> Actions -> New repository secret"
    "Then delete this file. The keystore works without it once the secrets are in."
    ""
    "APK_KEYSTORE_BASE64=$b64"
    "APK_KEYSTORE_PASSWORD=$storePass"
    "APK_KEY_ALIAS=$Alias"
    "APK_KEY_PASSWORD=$keyPass"
    ""
    "Certificate: $fingerprint"
) | Set-Content -Path $secretsFile -Encoding ASCII

icacls $secretsFile /inheritance:r /grant:r "${env:USERNAME}:F" | Out-Null

Write-Output ""
Write-Output "Keystore:   $keystore"
Write-Output "Secrets in: $secretsFile   (only this Windows account can read it)"
Write-Output "Certificate: $fingerprint"
Write-Output ""
Write-Output "Next: open that file, paste the four values as repository secrets, then BACK UP"
Write-Output "      $Dir to something that survives this machine, then delete the secrets file."
Write-Output "      Losing the keystore means every installed copy must be uninstalled - there"
Write-Output "      is no way to rotate a signing key without that one-time break."
