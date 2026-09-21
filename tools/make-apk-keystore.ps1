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

# Random, machine-generated: nobody types these into a phone, and a weak one here
# only has to survive a public repository plus a bot that tries to reuse the key.
function New-Secret { (-join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })) }
$storePass = New-Secret
$keyPass = New-Secret

# 10000 days: Android refuses to install an APK whose signing certificate expires
# before 2033, so a short validity would silently brick future releases.
& $keytool -genkeypair -v `
    -keystore $keystore -storepass $storePass `
    -keypass $keyPass -alias $Alias -keyalg RSA -keysize 2048 -validity 10000 `
    -dname "CN=ai-assistant, OU=app, O=fenever, L=NA, ST=NA, C=CN"
if ($LASTEXITCODE -ne 0) { throw "keytool failed with exit code $LASTEXITCODE" }

$fingerprint = (& $keytool -list -keystore $keystore -storepass $storePass -alias $Alias -v |
    Select-String "SHA256:").Line.Trim()

$bytes = [System.IO.File]::ReadAllBytes($keystore)
$b64 = [System.Convert]::ToBase64String($bytes)

Write-Output ""
Write-Output "Keystore created: $keystore"
Write-Output "Certificate:      $fingerprint"
Write-Output ""
Write-Output "Copy these FOUR secrets into GitHub (Settings -> Secrets and variables -> Actions):"
Write-Output "  APK_KEYSTORE_BASE64  = $b64"
Write-Output "  APK_KEYSTORE_PASSWORD = $storePass"
Write-Output "  APK_KEY_ALIAS         = $Alias"
Write-Output "  APK_KEY_PASSWORD      = $keyPass"
Write-Output ""
Write-Output "Then BACK UP this folder to somewhere that survives the machine:"
Write-Output "  $Dir"
Write-Output "Passwords are shown once and never stored by this script. If the terminal"
Write-Output "scrolled past them, read them back with:"
Write-Output "  keytool -list -keystore `"$keystore`" -alias $Alias -v"
Write-Output "(that prints the passwords only if you typed them in; they live in GitHub now.)"
