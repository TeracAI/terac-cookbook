[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$secret = $null
$transport = $null
try {
    Import-Module -Name (Join-Path $PSScriptRoot 'TeracAccess.psm1') -Force -ErrorAction Stop
    Assert-TeracPlatform
    $secret = Read-TeracCredential
    $transport = New-TeracProbeTransport
    $result = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $transport.Client
    $result | ConvertTo-Json -Depth 8
} catch {
    [Console]::Error.WriteLine('The bounded read-only probe failed. Provider content and credentials were not displayed.')
    throw 'Terac access probe failed.'
} finally {
    if ($null -ne $secret) { $secret.Dispose() }
    if ($null -ne $transport) {
        $transport.Client.Dispose()
        $transport.Handler.Dispose()
    }
}
