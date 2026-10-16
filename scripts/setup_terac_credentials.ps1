[CmdletBinding()]
param([switch]$Replace)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$secret = $null
try {
    Import-Module -Name (Join-Path $PSScriptRoot 'TeracAccess.psm1') -Force -ErrorAction Stop
    Assert-TeracPlatform
    Assert-TeracCredentialSetupReady -Replace:$Replace | Out-Null
    $secret = Read-Host -Prompt 'Enter the Terac API key (input is hidden)' -AsSecureString
    Save-TeracCredential -Secret $secret -Replace:$Replace
    Write-Output 'The key was encrypted for this Windows account and saved outside the checkout.'
} catch {
    if ($_.Exception.Message -ceq 'A key is already saved. Rerun setup with -Replace to replace it.' -or
        $_.Exception.Message -ceq 'Credential storage is unsafe or unavailable; no key was requested.' -or
        $_.Exception.Message -ceq 'Atomic credential replacement failed; the previous credential was restored.' -or
        $_.Exception.Message -ceq 'Atomic credential replacement failed before the saved file moved.' -or
        $_.Exception.Message -ceq 'Credential recovery is required; protected recovery data was retained where available.') {
        [Console]::Error.WriteLine($_.Exception.Message)
    } else {
        [Console]::Error.WriteLine('Credential setup failed. No key value was displayed or replaced.')
    }
    throw 'Credential setup failed.'
} finally {
    if ($null -ne $secret) { $secret.Dispose() }
}
