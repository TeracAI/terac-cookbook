Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http -ErrorAction Stop

$script:CredentialFileName = 'terac-api-key.dpapi'
$script:AtomicReplaceForTest = $null
$script:MaxResponseBytes = 1048576
$script:AllowedMcpVersions = @('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
$script:RestContextUri = [Uri]'https://terac.com/api/external/v2/organizations/current/context'
$script:McpUri = [Uri]'https://terac.com/api/mcp'

function Assert-TeracPlatform {
    [CmdletBinding()]
    param([string]$Platform = [Environment]::OSVersion.Platform.ToString())

    if ($Platform -ne 'Win32NT') {
        throw 'This operation requires Windows DPAPI.'
    }
}

function Get-TeracUserSid {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($null -eq $sid) { throw 'Current Windows identity is unavailable.' }
    return $sid
}

function Get-TeracStoreDirectory {
    param([string]$StoreDirectory)

    Assert-TeracPlatform
    if ([string]::IsNullOrWhiteSpace($StoreDirectory)) {
        $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
        if ([string]::IsNullOrWhiteSpace($localAppData) -or -not [IO.Path]::IsPathRooted($localAppData)) {
            throw 'The expected local application-data directory is unavailable.'
        }
        $environmentPath = $env:LOCALAPPDATA
        if ([string]::IsNullOrWhiteSpace($environmentPath) -or
            -not [string]::Equals([IO.Path]::GetFullPath($environmentPath).TrimEnd('\'),
                [IO.Path]::GetFullPath($localAppData).TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The local application-data path does not match the Windows user profile.'
        }
        $StoreDirectory = Join-Path $localAppData 'TeracCookbook'
    }

    if (-not [IO.Path]::IsPathRooted($StoreDirectory) -or $StoreDirectory -match '(^|[\\/])\.\.([\\/]|$)') {
        throw 'Credential storage path is not an accepted absolute path.'
    }
    $fullPath = [IO.Path]::GetFullPath($StoreDirectory).TrimEnd('\')
    if ([string]::IsNullOrWhiteSpace($fullPath) -or $fullPath -eq [IO.Path]::GetPathRoot($fullPath).TrimEnd('\')) {
        throw 'Credential storage path is not an accepted directory.'
    }
    Assert-NoTeracReparsePoint -Path $fullPath
    return $fullPath
}

function Assert-NoTeracReparsePoint {
    param([Parameter(Mandatory)][string]$Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetPathRoot($fullPath)
    $current = $root
    $relative = $fullPath.Substring($root.Length)
    foreach ($segment in $relative.Split([char[]]@('\', '/'), [StringSplitOptions]::RemoveEmptyEntries)) {
        $current = Join-Path $current $segment
        $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) { continue }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Credential storage path contains a reparse point.'
        }
    }
}

function New-TeracDirectorySecurity {
    $sid = Get-TeracUserSid
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit),
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow)
    $security.SetAccessRule($rule)
    return $security
}

function New-TeracFileSecurity {
    $sid = Get-TeracUserSid
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($sid)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow)
    $security.SetAccessRule($rule)
    return $security
}

function Assert-TeracAcl {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][ValidateSet('Directory', 'File')][string]$Kind
    )

    $sid = Get-TeracUserSid
    $acl = Get-Acl -LiteralPath $Path -ErrorAction Stop
    if (-not $acl.AreAccessRulesProtected -or
        $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value) {
        throw 'Credential storage ACL is not restricted to the current user.'
    }

    $rules = @($acl.GetAccessRules($true, $false, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 1) { throw 'Credential storage ACL has unexpected access rules.' }
    $rule = $rules[0]
    if ($rule.IdentityReference.Value -ne $sid.Value -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
        throw 'Credential storage ACL is not restricted to the current user.'
    }

    if ($Kind -eq 'Directory') {
        $expectedInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
        if ($rule.InheritanceFlags -ne $expectedInheritance -or
            $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
            throw 'Credential directory ACL has unexpected inheritance.'
        }
    } elseif ($rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
        throw 'Credential file ACL has unexpected inheritance.'
    }
}

function Ensure-TeracStoreDirectory {
    param([Parameter(Mandatory)][string]$Directory)

    $item = Get-Item -LiteralPath $Directory -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        $parent = Split-Path -Parent $Directory
        $parentItem = Get-Item -LiteralPath $parent -Force -ErrorAction Stop
        if (-not $parentItem.PSIsContainer -or
            ($parentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Credential storage parent is not an accepted directory.'
        }
        $security = New-TeracDirectorySecurity
        [void][IO.Directory]::CreateDirectory($Directory, $security)
        Assert-NoTeracReparsePoint -Path $Directory
    } elseif (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Credential storage path is not a regular directory.'
    }

    Assert-TeracAcl -Path $Directory -Kind Directory
}

function Get-TeracCredentialPath {
    param([string]$StoreDirectory)
    $directory = Get-TeracStoreDirectory -StoreDirectory $StoreDirectory
    return Join-Path $directory $script:CredentialFileName
}

function Assert-TeracCredentialSetupReady {
    [CmdletBinding()]
    param(
        [string]$StoreDirectory,
        [switch]$Replace
    )

    $path = $null
    $existing = $null
    try {
        $path = Get-TeracCredentialPath -StoreDirectory $StoreDirectory
        $directory = Split-Path -Parent $path
        Ensure-TeracStoreDirectory -Directory $directory
        Assert-NoTeracReparsePoint -Path $path
        $existing = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($null -ne $existing) {
            if ($existing.PSIsContainer -or
                ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Credential path is not a regular file.'
            }
            Assert-TeracAcl -Path $path -Kind File
        }
    } catch {
        throw 'Credential storage is unsafe or unavailable; no key was requested.'
    }

    if ($null -ne $existing -and -not $Replace) {
        throw 'A key is already saved. Rerun setup with -Replace to replace it.'
    }
    return [pscustomobject]@{ ExistingCredential = ($null -ne $existing); ReplacementRequested = [bool]$Replace }
}

function Invoke-TeracAtomicFileReplace {
    param(
        [Parameter(Mandatory)][string]$SourcePath,
        [Parameter(Mandatory)][string]$TargetPath,
        [Parameter(Mandatory)][string]$BackupPath
    )

    if ($null -ne $script:AtomicReplaceForTest) {
        & $script:AtomicReplaceForTest $SourcePath $TargetPath $BackupPath
        return
    }
    [IO.File]::Replace($SourcePath, $TargetPath, $BackupPath)
}

function Test-TeracSafeCredentialFile {
    param([Parameter(Mandatory)][string]$Path)

    try {
        Assert-NoTeracReparsePoint -Path $Path
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ($item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return $false }
        Assert-TeracAcl -Path $Path -Kind File
        return $true
    } catch {
        return $false
    }
}

function Restore-TeracCredentialAfterFailedReplace {
    param(
        [Parameter(Mandatory)][string]$TargetPath,
        [Parameter(Mandatory)][string]$BackupPath
    )

    $directory = Split-Path -Parent $TargetPath
    if (-not [string]::Equals($directory, (Split-Path -Parent $BackupPath),
            [StringComparison]::OrdinalIgnoreCase)) { return 'recovery_required' }
    try {
        Assert-NoTeracReparsePoint -Path $directory
        Assert-TeracAcl -Path $directory -Kind Directory
    } catch { return 'recovery_required' }

    $backup = Get-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
    $target = Get-Item -LiteralPath $TargetPath -Force -ErrorAction SilentlyContinue
    if ($null -ne $backup) {
        if (-not (Test-TeracSafeCredentialFile -Path $BackupPath)) { return 'recovery_required' }
        if ($null -eq $target) {
            try { [IO.File]::Move($BackupPath, $TargetPath) } catch { return 'recovery_required' }
            if (Test-TeracSafeCredentialFile -Path $TargetPath) { return 'restored' }
        }
        return 'recovery_required'
    }

    if ($null -ne $target -and (Test-TeracSafeCredentialFile -Path $TargetPath)) {
        return 'unchanged'
    }
    return 'recovery_required'
}

function Save-TeracCredential {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Security.SecureString]$Secret,
        [string]$StoreDirectory,
        [switch]$Replace
    )

    Assert-TeracPlatform
    if ($null -eq $Secret -or $Secret.Length -eq 0) { throw 'An empty credential cannot be stored.' }
    $directory = Get-TeracStoreDirectory -StoreDirectory $StoreDirectory
    Ensure-TeracStoreDirectory -Directory $directory
    $path = Join-Path $directory $script:CredentialFileName
    $existing = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if ($existing.PSIsContainer -or
            ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Credential storage path is not a regular file.'
        }
        Assert-NoTeracReparsePoint -Path $path
        Assert-TeracAcl -Path $path -Kind File
        if (-not $Replace) { throw 'A credential already exists. Use -Replace to replace it.' }
    }

    $ciphertext = $null
    $stream = $null
    $createdPath = $null
    $atomicReplacementAttempted = $false
    $writePath = $path
    $backupPath = $null
    if ($null -ne $existing) {
        $operationId = [guid]::NewGuid().ToString('N')
        $writePath = Join-Path $directory ($script:CredentialFileName + '.' + $operationId + '.tmp')
        $backupPath = Join-Path $directory ($script:CredentialFileName + '.' + $operationId + '.bak')
    }
    try {
        $ciphertext = ConvertFrom-SecureString -SecureString $Secret -ErrorAction Stop
        if ([string]::IsNullOrWhiteSpace($ciphertext) -or $ciphertext.Length -gt 65536 -or
            $ciphertext -notmatch '^[0-9A-Fa-f]+$') {
            throw 'Credential serialization returned an invalid DPAPI value.'
        }

        $stream = [IO.File]::Open($writePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $createdPath = $writePath
        [IO.File]::SetAccessControl($writePath, (New-TeracFileSecurity))
        Assert-NoTeracReparsePoint -Path $writePath
        Assert-TeracAcl -Path $writePath -Kind File
        $bytes = [Text.Encoding]::ASCII.GetBytes($ciphertext)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        Assert-TeracAcl -Path $writePath -Kind File
        if ($null -ne $existing) {
            Assert-NoTeracReparsePoint -Path $path
            $currentTarget = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if ($currentTarget.PSIsContainer -or
                ($currentTarget.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Credential storage target changed during replacement.'
            }
            Assert-TeracAcl -Path $path -Kind File
            $atomicReplacementAttempted = $true
            Invoke-TeracAtomicFileReplace -SourcePath $writePath -TargetPath $path -BackupPath $backupPath
            $createdPath = $null
            try { [IO.File]::Delete($backupPath) } catch { }
        } else {
            Assert-TeracAcl -Path $path -Kind File
            $createdPath = $null
        }
    } catch {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $createdPath) {
            Remove-Item -LiteralPath $createdPath -Force -ErrorAction SilentlyContinue
        }
        if ($atomicReplacementAttempted) {
            $recoveryState = 'recovery_required'
            try { $recoveryState = Restore-TeracCredentialAfterFailedReplace -TargetPath $path -BackupPath $backupPath } catch { }
            if ($recoveryState -eq 'restored') {
                throw 'Atomic credential replacement failed; the previous credential was restored.'
            }
            if ($recoveryState -eq 'unchanged') {
                throw 'Atomic credential replacement failed before the saved file moved.'
            }
            throw 'Credential recovery is required; protected recovery data was retained where available.'
        }
        throw 'Credential storage failed closed. No existing credential was replaced.'
    } finally {
        $ciphertext = $null
        $bytes = $null
    }
}

function Read-TeracCredential {
    [CmdletBinding()]
    param([string]$StoreDirectory)

    Assert-TeracPlatform
    $directory = Get-TeracStoreDirectory -StoreDirectory $StoreDirectory
    Ensure-TeracStoreDirectory -Directory $directory
    $path = Join-Path $directory $script:CredentialFileName
    $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'No regular encrypted credential file is available.'
    }
    Assert-TeracAcl -Path $path -Kind File

    try {
        $info = Get-Item -LiteralPath $path -Force -ErrorAction Stop
        if ($info.Length -le 0 -or $info.Length -gt 65536) { throw 'Invalid serialized length.' }
        $ciphertext = [IO.File]::ReadAllText($path, [Text.Encoding]::ASCII)
        if ($ciphertext -notmatch '^[0-9A-Fa-f]+$') { throw 'Invalid serialized value.' }
        $secret = ConvertTo-SecureString -String $ciphertext -ErrorAction Stop
        if ($secret.Length -eq 0) { $secret.Dispose(); throw 'Empty serialized value.' }
        return $secret
    } catch {
        throw 'The encrypted credential is invalid or belongs to another Windows user.'
    } finally {
        $ciphertext = $null
    }
}

function New-TeracProbeTransport {
    [CmdletBinding()]
    param()

    Assert-TeracPlatform
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $handler.UseCookies = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(20)
    $client.MaxResponseContentBufferSize = $script:MaxResponseBytes
    return [pscustomobject]@{ Client = $client; Handler = $handler }
}

function Get-TeracResponseBody {
    param([Parameter(Mandatory)][System.Net.Http.HttpResponseMessage]$Response)

    $length = $Response.Content.Headers.ContentLength
    if ($null -ne $length -and $length -gt $script:MaxResponseBytes) {
        throw 'Provider response exceeded the configured limit.'
    }
    return $Response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
}

function Send-TeracRequest {
    param(
        [Parameter(Mandatory)][System.Net.Http.HttpClient]$HttpClient,
        [Parameter(Mandatory)][Uri]$Uri,
        [Parameter(Mandatory)][string]$PlainKey,
        [Parameter(Mandatory)][ValidateSet('REST', 'MCP')][string]$Kind,
        [Parameter(Mandatory)][string]$Method,
        [string]$Body,
        [string]$ProtocolVersion,
        [string]$SessionId
    )

    if ($Uri.Scheme -ne 'https' -or $Uri.Host -ne 'terac.com' -or
        $Uri.AbsolutePath -notin @('/api/external/v2/organizations/current/context', '/api/mcp')) {
        throw 'Probe destination is outside the fixed Terac read-only allowlist.'
    }

    $request = New-Object System.Net.Http.HttpRequestMessage(
        ([System.Net.Http.HttpMethod]::$Method), $Uri)
    $response = $null
    try {
        if ($Kind -eq 'REST') {
            $request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $PlainKey)
        } else {
            [void]$request.Headers.TryAddWithoutValidation('x-api-key', $PlainKey)
            [void]$request.Headers.Accept.Add(
                [System.Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new('application/json'))
            [void]$request.Headers.Accept.Add(
                [System.Net.Http.Headers.MediaTypeWithQualityHeaderValue]::new('text/event-stream'))
            if (-not [string]::IsNullOrWhiteSpace($ProtocolVersion)) {
                [void]$request.Headers.TryAddWithoutValidation('MCP-Protocol-Version', $ProtocolVersion)
            }
            if (-not [string]::IsNullOrWhiteSpace($SessionId)) {
                [void]$request.Headers.TryAddWithoutValidation('Mcp-Session-Id', $SessionId)
            }
        }
        if (-not [string]::IsNullOrEmpty($Body)) {
            $request.Content = New-Object System.Net.Http.StringContent(
                $Body, [Text.Encoding]::UTF8, 'application/json')
        }
        $response = $HttpClient.SendAsync($request).GetAwaiter().GetResult()
        $bodyText = ''
        $contentType = ''
        if ($null -ne $response.Content) {
            $bodyText = Get-TeracResponseBody -Response $response
            if ($null -ne $response.Content.Headers.ContentType) {
                $contentType = [string]$response.Content.Headers.ContentType.MediaType
            }
        }
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            ContentType = $contentType
            Body = $bodyText
            SessionId = if ($Kind -eq 'MCP' -and $response.Headers.Contains('Mcp-Session-Id')) {
                [string]($response.Headers.GetValues('Mcp-Session-Id') | Select-Object -First 1)
            } else { $null }
        }
    } finally {
        if ($null -ne $response) { $response.Dispose() }
        $request.Dispose()
    }
}

function Test-TeracJsonObject {
    param([AllowNull()][object]$Value)

    return $null -ne $Value -and
        $Value.GetType() -eq [System.Management.Automation.PSCustomObject]
}

function Get-TeracExactJsonProperty {
    param(
        [AllowNull()][object]$Value,
        [Parameter(Mandatory)][string]$Name
    )

    if (-not (Test-TeracJsonObject -Value $Value)) { return $null }
    foreach ($property in $Value.PSObject.Properties) {
        if ([string]::Equals($property.Name, $Name, [StringComparison]::Ordinal)) {
            return $property
        }
    }
    return $null
}

function Test-TeracIntegerNumber {
    param([AllowNull()][object]$Value)

    if ($null -eq $Value -or $Value -is [bool]) { return $false }
    $integerTypes = @(
        [System.Byte], [System.SByte], [System.Int16], [System.UInt16],
        [System.Int32], [System.UInt32], [System.Int64], [System.UInt64]
    )
    return $integerTypes -contains $Value.GetType()
}

function Assert-TeracJsonRpcResponse {
    param([Parameter(Mandatory)]$Message, [Parameter(Mandatory)][int]$ExpectedId)

    if (-not (Test-TeracJsonObject -Value $Message)) { throw 'MCP returned an invalid JSON-RPC envelope.' }
    $propertyNames = @($Message.PSObject.Properties | ForEach-Object { $_.Name })
    foreach ($requiredName in @('jsonrpc', 'id')) {
        if ($propertyNames -cnotcontains $requiredName) {
            throw 'MCP returned an invalid JSON-RPC envelope.'
        }
    }
    if ($Message.jsonrpc -isnot [string] -or $Message.jsonrpc -cne '2.0' -or
        -not (Test-TeracIntegerNumber -Value $Message.id) -or $Message.id -ne $ExpectedId) {
        throw 'MCP response did not match the request.'
    }

    $hasResult = $propertyNames -ccontains 'result'
    $hasError = $propertyNames -ccontains 'error'
    if ($hasResult -eq $hasError) { throw 'MCP returned an invalid JSON-RPC envelope.' }
    if ($hasResult) {
        if (-not (Test-TeracJsonObject -Value $Message.result)) {
            throw 'MCP returned an invalid JSON-RPC result.'
        }
    } else {
        $errorObject = $Message.error
        if (-not (Test-TeracJsonObject -Value $errorObject)) {
            throw 'MCP returned an invalid JSON-RPC error.'
        }
        $errorNames = @($errorObject.PSObject.Properties | ForEach-Object { $_.Name })
        if ($errorNames -cnotcontains 'code' -or $errorNames -cnotcontains 'message' -or
            -not (Test-TeracIntegerNumber -Value $errorObject.code) -or
            $errorObject.message -isnot [string]) {
            throw 'MCP returned an invalid JSON-RPC error.'
        }
    }
    return $Message
}

function Assert-TeracJsonRpcNotification {
    param([Parameter(Mandatory)]$Message)

    if (-not (Test-TeracJsonObject -Value $Message)) { throw 'MCP event stream contained an invalid message.' }
    $propertyNames = @($Message.PSObject.Properties | ForEach-Object { $_.Name })
    if ($propertyNames -cnotcontains 'jsonrpc' -or $propertyNames -cnotcontains 'method' -or
        $propertyNames -ccontains 'id' -or $propertyNames -ccontains 'result' -or
        $propertyNames -ccontains 'error' -or $Message.jsonrpc -isnot [string] -or
        $Message.jsonrpc -cne '2.0' -or $Message.method -isnot [string] -or
        [string]::IsNullOrWhiteSpace($Message.method)) {
        throw 'MCP event stream contained an invalid message.'
    }
}

function ConvertFrom-TeracMcpResponse {
    param([Parameter(Mandatory)]$Response, [Parameter(Mandatory)][int]$ExpectedId)

    if (-not [string]::Equals($Response.ContentType, 'application/json', [StringComparison]::OrdinalIgnoreCase) -and
        -not [string]::Equals($Response.ContentType, 'text/event-stream', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'MCP returned an unsupported content type.'
    }
    if ($Response.ContentType -eq 'application/json') {
        $message = ConvertFrom-Json -InputObject $Response.Body -ErrorAction Stop
        return Assert-TeracJsonRpcResponse -Message $message -ExpectedId $ExpectedId
    }

    foreach ($event in ($Response.Body -split "\r?\n\r?\n")) {
        $dataLines = @($event -split "\r?\n" | Where-Object { $_.StartsWith('data:') } |
            ForEach-Object { $_.Substring(5).TrimStart() })
        if ($dataLines.Count -eq 0) { continue }
        $candidate = ConvertFrom-Json -InputObject ($dataLines -join "`n") -ErrorAction Stop
        if (-not (Test-TeracJsonObject -Value $candidate)) { throw 'MCP event stream contained an invalid message.' }
        $candidateNames = @($candidate.PSObject.Properties | ForEach-Object { $_.Name })
        if ($candidateNames -ccontains 'id' -or $candidateNames -ccontains 'result' -or
            $candidateNames -ccontains 'error') {
            return Assert-TeracJsonRpcResponse -Message $candidate -ExpectedId $ExpectedId
        }
        Assert-TeracJsonRpcNotification -Message $candidate
    }
    throw 'MCP event stream had no response for the request.'
}

function ConvertFrom-TeracRestContext {
    param([Parameter(Mandatory)][string]$Body)

    $context = ConvertFrom-Json -InputObject $Body -ErrorAction Stop
    if (-not (Test-TeracJsonObject -Value $context)) {
        throw 'REST context response had an unexpected shape.'
    }
    $rolePresent = $false
    foreach ($rolePath in @('role', 'memberRole', 'organizationRole')) {
        if ($null -ne $context.PSObject.Properties[$rolePath]) {
            $rolePresent = $true
            break
        }
    }
    return [pscustomobject]@{
        ShapeValid = $true
        MarkdownPresent = ($null -ne $context.PSObject.Properties['markdown'])
        OrganizationIdPresent = ($null -ne $context.PSObject.Properties['organizationId'])
        OrganizationNamePresent = ($null -ne $context.PSObject.Properties['organizationName'])
        BalancePresent = ($null -ne $context.PSObject.Properties['balanceDollars'])
        DashboardPresent = ($null -ne $context.PSObject.Properties['dashboard'])
        RoleMetadataPresent = $rolePresent
    }
}

function Invoke-TeracReadOnlyProbe {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][Security.SecureString]$Secret,
        [Parameter(Mandatory)][System.Net.Http.HttpClient]$HttpClient
    )

    Assert-TeracPlatform
    if ($null -eq $Secret -or $Secret.Length -eq 0) { throw 'A nonempty local credential is required.' }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secret)
    $plainKey = $null
    try {
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $rest = [ordered]@{
            attempted = $true
            http_status = $null
            context_received = $false
            response_shape_valid = $false
            markdown_present = $false
            organization_id_present = $false
            organization_name_present = $false
            balance_present = $false
            dashboard_present = $false
            role_metadata_present = $false
            error = $null
        }
        try {
            $restResponse = Send-TeracRequest -HttpClient $HttpClient -Uri $script:RestContextUri `
                -PlainKey $plainKey -Kind REST -Method GET
            $rest.http_status = $restResponse.StatusCode
            if ($restResponse.StatusCode -eq 200) {
                try {
                    $restFacts = ConvertFrom-TeracRestContext -Body $restResponse.Body
                    $rest.context_received = $true
                    $rest.response_shape_valid = $restFacts.ShapeValid
                    $rest.markdown_present = $restFacts.MarkdownPresent
                    $rest.organization_id_present = $restFacts.OrganizationIdPresent
                    $rest.organization_name_present = $restFacts.OrganizationNamePresent
                    $rest.balance_present = $restFacts.BalancePresent
                    $rest.dashboard_present = $restFacts.DashboardPresent
                    $rest.role_metadata_present = $restFacts.RoleMetadataPresent
                } catch { $rest.error = 'invalid_context_response' }
            }
        } catch { $rest.error = 'request_failed' }

        $mcp = [ordered]@{
            attempted = $true
            initialized = $false
            http_status = $null
            protocol_version = $null
            tools = @()
            catalog_complete = $false
            context_schema = $null
            context_read = [ordered]@{ attempted = $false; http_status = $null; is_error = $null; content_count = $null; structured_content_present = $null }
            error = $null
        }
        $sessionId = $null
        $mcpStage = 'initialize'
        try {
            $initBody = [ordered]@{
                jsonrpc = '2.0'
                id = 1
                method = 'initialize'
                params = [ordered]@{
                    protocolVersion = '2025-11-25'
                    capabilities = @{}
                    clientInfo = [ordered]@{ name = 'terac-cookbook-access-probe'; version = '0.1.0' }
                }
            } | ConvertTo-Json -Depth 8 -Compress
            $initResponse = Send-TeracRequest -HttpClient $HttpClient -Uri $script:McpUri `
                -PlainKey $plainKey -Kind MCP -Method POST -Body $initBody
            $mcp.http_status = $initResponse.StatusCode
            if ($initResponse.StatusCode -ne 200) { throw 'mcp_initialize_status' }
            $sessionId = $initResponse.SessionId
            if (-not [string]::IsNullOrWhiteSpace($sessionId) -and
                $sessionId -notmatch '^[\x21-\x7E]{1,256}$') { throw 'mcp_session_invalid' }
            $mcpStage = 'initialize_response'
            $initMessage = ConvertFrom-TeracMcpResponse -Response $initResponse -ExpectedId 1
            if ($null -eq $initMessage.PSObject.Properties['result'] -or
                $null -ne $initMessage.PSObject.Properties['error']) { throw 'mcp_initialize_invalid' }
            $initializeResult = $initMessage.result
            $protocolVersionProperty = Get-TeracExactJsonProperty -Value $initializeResult -Name 'protocolVersion'
            $capabilitiesProperty = Get-TeracExactJsonProperty -Value $initializeResult -Name 'capabilities'
            $serverInfoProperty = Get-TeracExactJsonProperty -Value $initializeResult -Name 'serverInfo'
            if ($null -eq $protocolVersionProperty -or $protocolVersionProperty.Value -isnot [string] -or
                -not (Test-TeracJsonObject -Value $(if ($null -eq $capabilitiesProperty) { $null } else { $capabilitiesProperty.Value })) -or
                -not (Test-TeracJsonObject -Value $(if ($null -eq $serverInfoProperty) { $null } else { $serverInfoProperty.Value }))) {
                throw 'mcp_initialize_result_invalid'
            }
            $toolCapabilityProperty = Get-TeracExactJsonProperty -Value $capabilitiesProperty.Value -Name 'tools'
            if ($null -eq $toolCapabilityProperty -or
                -not (Test-TeracJsonObject -Value $toolCapabilityProperty.Value)) {
                throw 'mcp_initialize_tools_capability_invalid'
            }
            $listChangedProperty = Get-TeracExactJsonProperty -Value $toolCapabilityProperty.Value -Name 'listChanged'
            if ($null -ne $listChangedProperty -and $listChangedProperty.Value -isnot [bool]) {
                throw 'mcp_initialize_tools_capability_invalid'
            }
            $serverInfo = $serverInfoProperty.Value
            $serverNameProperty = Get-TeracExactJsonProperty -Value $serverInfo -Name 'name'
            $serverVersionProperty = Get-TeracExactJsonProperty -Value $serverInfo -Name 'version'
            if ($null -eq $serverNameProperty -or $serverNameProperty.Value -isnot [string] -or
                [string]::IsNullOrWhiteSpace($serverNameProperty.Value) -or
                $null -eq $serverVersionProperty -or $serverVersionProperty.Value -isnot [string] -or
                [string]::IsNullOrWhiteSpace($serverVersionProperty.Value)) {
                throw 'mcp_initialize_server_info_invalid'
            }
            $protocolVersion = $protocolVersionProperty.Value
            if ($protocolVersion -notin $script:AllowedMcpVersions) { throw 'mcp_protocol_unsupported' }
            $mcp.initialized = $true
            $mcp.protocol_version = $protocolVersion

            $mcpStage = 'initialized_notification'
            $notificationBody = [ordered]@{ jsonrpc = '2.0'; method = 'notifications/initialized' } |
                ConvertTo-Json -Depth 4 -Compress
            $notificationResponse = Send-TeracRequest -HttpClient $HttpClient -Uri $script:McpUri `
                -PlainKey $plainKey -Kind MCP -Method POST -Body $notificationBody `
                -ProtocolVersion $protocolVersion -SessionId $sessionId
            if ($notificationResponse.StatusCode -ne 202) { throw 'mcp_initialized_notification_status' }

            $tools = [Collections.Generic.List[object]]::new()
            $nextCursor = $null
            $seenCursors = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            for ($page = 0; $page -lt 20; $page++) {
                $mcpStage = 'tools_list'
                $listParams = [ordered]@{}
                if (-not [string]::IsNullOrWhiteSpace($nextCursor)) { $listParams.cursor = $nextCursor }
                $requestId = 2 + $page
                $listBody = [ordered]@{ jsonrpc = '2.0'; id = $requestId; method = 'tools/list'; params = $listParams } |
                    ConvertTo-Json -Depth 8 -Compress
                $listResponse = Send-TeracRequest -HttpClient $HttpClient -Uri $script:McpUri `
                    -PlainKey $plainKey -Kind MCP -Method POST -Body $listBody `
                    -ProtocolVersion $protocolVersion -SessionId $sessionId
                $mcp.http_status = $listResponse.StatusCode
                if ($listResponse.StatusCode -ne 200) { throw 'mcp_tools_list_status' }
                $listMessage = ConvertFrom-TeracMcpResponse -Response $listResponse -ExpectedId $requestId
                $toolsProperty = Get-TeracExactJsonProperty -Value $listMessage.result -Name 'tools'
                $nextCursorProperty = Get-TeracExactJsonProperty -Value $listMessage.result -Name 'nextCursor'
                if ($null -eq $listMessage.PSObject.Properties['result'] -or
                    $null -ne $listMessage.PSObject.Properties['error'] -or
                    $null -eq $toolsProperty -or $toolsProperty.Value -isnot [array] -or
                    ($null -ne $nextCursorProperty -and $nextCursorProperty.Value -isnot [string])) {
                    throw 'mcp_tools_list_invalid'
                }
                if ($null -ne $nextCursorProperty -and [string]::IsNullOrWhiteSpace($nextCursorProperty.Value)) {
                    throw 'mcp_tools_list_invalid'
                }
                foreach ($tool in $toolsProperty.Value) {
                    if (-not (Test-TeracJsonObject -Value $tool)) { throw 'mcp_tool_invalid' }
                    $toolNameProperty = Get-TeracExactJsonProperty -Value $tool -Name 'name'
                    $inputSchemaProperty = Get-TeracExactJsonProperty -Value $tool -Name 'inputSchema'
                    $inputSchemaTypeProperty = if ($null -eq $inputSchemaProperty) { $null } else {
                        Get-TeracExactJsonProperty -Value $inputSchemaProperty.Value -Name 'type'
                    }
                    if ($null -eq $toolNameProperty -or $toolNameProperty.Value -isnot [string] -or
                        [string]::IsNullOrWhiteSpace($toolNameProperty.Value) -or
                        -not (Test-TeracJsonObject -Value $(if ($null -eq $inputSchemaProperty) { $null } else { $inputSchemaProperty.Value })) -or
                        $null -eq $inputSchemaTypeProperty -or $inputSchemaTypeProperty.Value -isnot [string] -or
                        $inputSchemaTypeProperty.Value -cne 'object') {
                        throw 'mcp_tool_invalid'
                    }
                    foreach ($optionalName in @('title', 'description')) {
                        $optionalProperty = Get-TeracExactJsonProperty -Value $tool -Name $optionalName
                        if ($null -ne $optionalProperty -and $optionalProperty.Value -isnot [string]) {
                            throw 'mcp_tool_invalid'
                        }
                    }
                    $outputSchemaProperty = Get-TeracExactJsonProperty -Value $tool -Name 'outputSchema'
                    if ($null -ne $outputSchemaProperty -and -not (Test-TeracJsonObject -Value $outputSchemaProperty.Value)) {
                        throw 'mcp_tool_invalid'
                    }
                    $annotationsProperty = Get-TeracExactJsonProperty -Value $tool -Name 'annotations'
                    if ($null -ne $annotationsProperty) {
                        if (-not (Test-TeracJsonObject -Value $annotationsProperty.Value)) {
                            throw 'mcp_tool_invalid'
                        }
                        $annotationTitle = Get-TeracExactJsonProperty -Value $annotationsProperty.Value -Name 'title'
                        if ($null -ne $annotationTitle -and $annotationTitle.Value -isnot [string]) {
                            throw 'mcp_tool_invalid'
                        }
                        foreach ($hintName in @('readOnlyHint', 'destructiveHint', 'idempotentHint', 'openWorldHint')) {
                            $hintProperty = Get-TeracExactJsonProperty -Value $annotationsProperty.Value -Name $hintName
                            if ($null -ne $hintProperty -and $hintProperty.Value -isnot [bool]) {
                                throw 'mcp_tool_invalid'
                            }
                        }
                    }
                    $iconsProperty = Get-TeracExactJsonProperty -Value $tool -Name 'icons'
                    if ($null -ne $iconsProperty -and $iconsProperty.Value -isnot [array]) {
                        throw 'mcp_tool_invalid'
                    }
                    $toolName = $toolNameProperty.Value
                    if ($toolName -match '^[A-Za-z0-9_.-]{1,128}$') { $tools.Add($tool) }
                }
                $nextCursor = if ($null -eq $nextCursorProperty) { $null } else { $nextCursorProperty.Value }
                if ($null -eq $nextCursorProperty) {
                    $mcp.catalog_complete = $true
                    break
                }
                if (-not $seenCursors.Add($nextCursor)) { throw 'mcp_cursor_repeated' }
            }

            $safeTools = @($tools | ForEach-Object {
                $toolName = [string]$_.name
                if ($toolName -match '^[A-Za-z0-9_.-]{1,128}$' -and
                    $toolName.IndexOf($plainKey, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
                    $toolName
                }
            })
            $mcp.tools = $safeTools
            $contextTool = $tools | Where-Object { [string]$_.name -ceq 'terac_get_context' } | Select-Object -First 1
            if ($null -ne $contextTool) {
                $mcpStage = 'context_schema'
                $schemaProperty = Get-TeracExactJsonProperty -Value $contextTool -Name 'inputSchema'
                $schema = if ($null -eq $schemaProperty) { $null } else { $schemaProperty.Value }
                $schemaTypeProperty = Get-TeracExactJsonProperty -Value $schema -Name 'type'
                if (-not (Test-TeracJsonObject -Value $schema) -or
                    $null -eq $schemaTypeProperty -or [string]$schemaTypeProperty.Value -cne 'object') {
                    throw 'mcp_context_schema_invalid'
                }
                $allowedSchemaKeywords = @('type', 'properties', 'required', 'additionalProperties', 'title', 'description')
                $schemaKeywords = @($schema.PSObject.Properties | ForEach-Object { $_.Name })
                if (@($schemaKeywords | Where-Object { $_ -cnotin $allowedSchemaKeywords }).Count -gt 0) {
                    throw 'mcp_context_schema_invalid'
                }
                $properties = @()
                $propertiesProperty = Get-TeracExactJsonProperty -Value $schema -Name 'properties'
                if ($null -ne $propertiesProperty -and $null -ne $propertiesProperty.Value) {
                    if (-not (Test-TeracJsonObject -Value $propertiesProperty.Value)) {
                        throw 'mcp_context_schema_invalid'
                    }
                    $properties = @($propertiesProperty.Value.PSObject.Properties)
                } elseif ($null -ne $propertiesProperty) {
                    throw 'mcp_context_schema_invalid'
                }
                $required = @()
                $requiredProperty = Get-TeracExactJsonProperty -Value $schema -Name 'required'
                if ($null -ne $requiredProperty) {
                    if ($requiredProperty.Value -isnot [array]) { throw 'mcp_context_schema_invalid' }
                    $required = @($requiredProperty.Value)
                    if (@($required | Where-Object { $_ -isnot [string] }).Count -gt 0) {
                        throw 'mcp_context_schema_invalid'
                    }
                }
                $additional = $null
                $additionalPropertiesProperty = Get-TeracExactJsonProperty -Value $schema -Name 'additionalProperties'
                if ($null -ne $additionalPropertiesProperty) {
                    if ($additionalPropertiesProperty.Value -isnot [bool]) { throw 'mcp_context_schema_invalid' }
                    $additional = [bool]$additionalPropertiesProperty.Value
                }
                $mcp.context_schema = [ordered]@{
                    type = [string]$schema.type
                    property_count = $properties.Count
                    required_count = $required.Count
                    additional_properties = $additional
                }
                if ($required.Count -eq 0) {
                    $mcpStage = 'context_read'
                    $callId = 3 + [Math]::Min(19, $page)
                    $callBody = [ordered]@{
                        jsonrpc = '2.0'
                        id = $callId
                        method = 'tools/call'
                        params = [ordered]@{ name = 'terac_get_context'; arguments = @{} }
                    } | ConvertTo-Json -Depth 8 -Compress
                    $mcp.context_read.attempted = $true
                    $callResponse = Send-TeracRequest -HttpClient $HttpClient -Uri $script:McpUri `
                        -PlainKey $plainKey -Kind MCP -Method POST -Body $callBody `
                        -ProtocolVersion $protocolVersion -SessionId $sessionId
                    $mcp.context_read.http_status = $callResponse.StatusCode
                    if ($callResponse.StatusCode -eq 200) {
                        $callMessage = ConvertFrom-TeracMcpResponse -Response $callResponse -ExpectedId $callId
                        if ($null -eq $callMessage.PSObject.Properties['result'] -or
                            $null -ne $callMessage.PSObject.Properties['error']) {
                            throw 'mcp_context_call_invalid'
                        }
                        $callResult = $callMessage.result
                        $contentProperty = Get-TeracExactJsonProperty -Value $callResult -Name 'content'
                        $isErrorProperty = Get-TeracExactJsonProperty -Value $callResult -Name 'isError'
                        $structuredContentProperty = Get-TeracExactJsonProperty -Value $callResult -Name 'structuredContent'
                        if ($null -eq $contentProperty -or $contentProperty.Value -isnot [array] -or
                            ($null -ne $isErrorProperty -and $isErrorProperty.Value -isnot [bool]) -or
                            ($null -ne $structuredContentProperty -and
                                -not (Test-TeracJsonObject -Value $structuredContentProperty.Value))) {
                            throw 'mcp_context_call_invalid'
                        }
                        foreach ($contentBlock in $contentProperty.Value) {
                            if (-not (Test-TeracJsonObject -Value $contentBlock)) {
                                throw 'mcp_context_content_invalid'
                            }
                            $typeProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'type'
                            if ($null -eq $typeProperty -or $typeProperty.Value -isnot [string]) {
                                throw 'mcp_context_content_invalid'
                            }
                            switch -CaseSensitive ($typeProperty.Value) {
                                'text' {
                                    $textProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'text'
                                    if ($null -eq $textProperty -or $textProperty.Value -isnot [string]) {
                                        throw 'mcp_context_content_invalid'
                                    }
                                }
                                { $_ -cin @('image', 'audio') } {
                                    $dataProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'data'
                                    $mimeTypeProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'mimeType'
                                    if ($null -eq $dataProperty -or $dataProperty.Value -isnot [string] -or
                                        $null -eq $mimeTypeProperty -or $mimeTypeProperty.Value -isnot [string]) {
                                        throw 'mcp_context_content_invalid'
                                    }
                                }
                                'resource_link' {
                                    $uriProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'uri'
                                    $nameProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'name'
                                    if ($null -eq $uriProperty -or $uriProperty.Value -isnot [string] -or
                                        $null -eq $nameProperty -or $nameProperty.Value -isnot [string]) {
                                        throw 'mcp_context_content_invalid'
                                    }
                                }
                                'resource' {
                                    $resourceProperty = Get-TeracExactJsonProperty -Value $contentBlock -Name 'resource'
                                    if ($null -eq $resourceProperty -or
                                        -not (Test-TeracJsonObject -Value $resourceProperty.Value)) {
                                        throw 'mcp_context_content_invalid'
                                    }
                                    $resource = $resourceProperty.Value
                                    $resourceUriProperty = Get-TeracExactJsonProperty -Value $resource -Name 'uri'
                                    $resourceTextProperty = Get-TeracExactJsonProperty -Value $resource -Name 'text'
                                    $resourceBlobProperty = Get-TeracExactJsonProperty -Value $resource -Name 'blob'
                                    if ($null -eq $resourceUriProperty -or $resourceUriProperty.Value -isnot [string] -or
                                        (($null -eq $resourceTextProperty -or $resourceTextProperty.Value -isnot [string]) -and
                                            ($null -eq $resourceBlobProperty -or $resourceBlobProperty.Value -isnot [string]))) {
                                        throw 'mcp_context_content_invalid'
                                    }
                                }
                                default { throw 'mcp_context_content_invalid' }
                            }
                        }
                        $mcp.context_read.is_error = if ($null -eq $isErrorProperty) { $false } else { $isErrorProperty.Value }
                        $mcp.context_read.content_count = $contentProperty.Value.Count
                        $mcp.context_read.structured_content_present = ($null -ne $structuredContentProperty)
                    }
                }
            }
        } catch {
            $mcp.error = $mcpStage
        }

        return [pscustomobject]@{ rest = [pscustomobject]$rest; mcp = [pscustomobject]$mcp }
    } finally {
        if ($null -ne $bstr -and $bstr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $plainKey = $null
        $bodyText = $null
    }
}

Export-ModuleMember -Function Assert-TeracPlatform, Assert-TeracCredentialSetupReady,
    Save-TeracCredential, Read-TeracCredential,
    New-TeracProbeTransport, Invoke-TeracReadOnlyProbe
