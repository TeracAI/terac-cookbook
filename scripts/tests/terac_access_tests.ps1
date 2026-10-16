$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$modulePath = Join-Path (Split-Path $PSScriptRoot -Parent) 'TeracAccess.psm1'
Import-Module -Name $modulePath -Force
Add-Type -AssemblyName System.Net.Http

function Assert-True {
    param([bool]$Condition, [string]$Name)
    if (-not $Condition) { throw "Assertion failed: $Name" }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Name)
    try {
        & $Action | Out-Null
    } catch {
        return
    }
    throw "Expected rejection: $Name"
}

function Assert-ThrowsWithMessage {
    param([scriptblock]$Action, [string]$ExpectedMessage, [string]$Name)
    $caught = $null
    try { & $Action | Out-Null } catch { $caught = $_.Exception }
    Assert-True ($null -ne $caught -and $caught.Message -ceq $ExpectedMessage) $Name
}

$fixtureHandlerSource = @'
using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

public sealed class TeracFixtureHandler : HttpMessageHandler
{
    public readonly List<string> Uris = new List<string>();
    public readonly List<string> Bodies = new List<string>();
    public readonly List<string> Methods = new List<string>();
    public readonly List<bool> KeyHeadersMatched = new List<bool>();
    public readonly List<bool> SessionHeadersPresent = new List<bool>();
    public readonly List<string> RpcMethods = new List<string>();
    public readonly List<string> ToolCallNames = new List<string>();
    public int ToolsListCalls = 0;
    public readonly string Scenario;
    public readonly string ExpectedKey;

    public TeracFixtureHandler(string scenario, string expectedKey)
    {
        Scenario = scenario;
        ExpectedKey = expectedKey;
    }

    private static HttpResponseMessage Json(HttpStatusCode status, string body)
    {
        var response = new HttpResponseMessage(status);
        response.Content = new StringContent(body, Encoding.UTF8, "application/json");
        return response;
    }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        Uris.Add(request.RequestUri.AbsoluteUri);
        Methods.Add(request.Method.Method);
        var body = request.Content == null ? "" : request.Content.ReadAsStringAsync().GetAwaiter().GetResult();
        Bodies.Add(body);
        var apiKeyMatched = false;
        IEnumerable<string> keyValues;
        if (request.Headers.TryGetValues("x-api-key", out keyValues))
            apiKeyMatched = String.Join("", keyValues) == ExpectedKey;
        if (request.Headers.Authorization != null && request.Headers.Authorization.Scheme == "Bearer")
            apiKeyMatched = request.Headers.Authorization.Parameter == ExpectedKey;
        KeyHeadersMatched.Add(apiKeyMatched);
        IEnumerable<string> sessionValues;
        SessionHeadersPresent.Add(request.Headers.TryGetValues("Mcp-Session-Id", out sessionValues));

        if (request.RequestUri.Scheme != "https" || request.RequestUri.Host != "terac.com")
            throw new InvalidOperationException("Unexpected test destination.");

        if (request.Method == HttpMethod.Get)
        {
            if (Scenario == "redirect-rest")
            {
                var redirect = new HttpResponseMessage(HttpStatusCode.Redirect);
                redirect.Headers.Location = new Uri("https://evil.example/private");
                redirect.Content = new StringContent("sensitive-provider-value");
                return Task.FromResult(redirect);
            }
            if (Scenario == "rest-error")
                return Task.FromResult(Json(HttpStatusCode.ServiceUnavailable, "sensitive-provider-value"));
            if (Scenario == "rest-malformed")
                return Task.FromResult(Json(HttpStatusCode.OK, "sensitive-provider-value"));
            if (Scenario == "rest-oversized")
                return Task.FromResult(Json(HttpStatusCode.OK, new string('x', 1048577)));
            if (Scenario == "rest-scalar-number")
                return Task.FromResult(Json(HttpStatusCode.OK, "42"));
            if (Scenario == "rest-scalar-boolean")
                return Task.FromResult(Json(HttpStatusCode.OK, "true"));

            return Task.FromResult(Json(HttpStatusCode.OK,
                "{\"markdown\":\"sensitive-provider-value\",\"organizationId\":\"private-id\",\"organizationName\":\"private-name\",\"organizationSlug\":\"private-slug\",\"balanceDollars\":12345,\"dashboard\":{\"home\":\"https://private.example\"},\"role\":\"Admin\"}"));
        }

        var rpc = NewtonsoftJsonRead(body);
        var method = rpc;
        RpcMethods.Add(method);
        if (method == "initialize")
        {
            if (Scenario == "mcp-error")
                return Task.FromResult(Json(HttpStatusCode.Unauthorized, "sensitive-provider-value"));
            var version = Scenario == "unsupported-version" ? "2026-07-28" : "2025-11-25";
            var rpcVersion = Scenario == "mcp-wrong-rpc-version" ? "1.0" : "2.0";
            var id = Scenario == "mcp-id-boolean" ? "true" :
                Scenario == "mcp-id-fraction" ? "1.1" :
                Scenario == "mcp-id-string" || Scenario == "mcp-sse-id-string" ? "\"1\"" : "1";
            var initializeBody = "{\"jsonrpc\":\"" + rpcVersion + "\",\"id\":" + id +
                ",\"result\":{\"protocolVersion\":\"" + version + "\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"private-server-name\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-missing-capabilities")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-null-capabilities")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":null,\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-missing-tools-capability")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"prompts\":{}},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-invalid-tools-capability")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":\"invalid\"},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-cased-tools")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"Tools\":{}},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-cased-protocol-version")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ProtocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":{}},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-tools-false")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":false},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-invalid-list-changed")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{\"tools\":{\"listChanged\":\"invalid-boolean\"}},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-init-invalid-server-info")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{},\"serverInfo\":{\"name\":\"fixture\"}}}";
            if (Scenario == "mcp-init-numeric-version")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"protocolVersion\":0,\"capabilities\":{},\"serverInfo\":{\"name\":\"fixture\",\"version\":\"1\"}}}";
            if (Scenario == "mcp-missing-result")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1}";
            if (Scenario == "mcp-both-result-and-error")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{},\"error\":{\"code\":-32000,\"message\":\"fixture\"}}";
            if (Scenario == "mcp-invalid-result")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":null}";
            if (Scenario == "mcp-malformed-error" || Scenario == "mcp-sse-malformed-error")
                initializeBody = "{\"jsonrpc\":\"2.0\",\"id\":1,\"error\":{\"message\":\"fixture\"}}";
            var initialize = Json(HttpStatusCode.OK, initializeBody);
            if (Scenario.StartsWith("mcp-sse-", StringComparison.Ordinal))
                initialize.Content = new StringContent("data: " + initializeBody + "\n\n", Encoding.UTF8, "text/event-stream");
            initialize.Headers.Add("Mcp-Session-Id", "private-session-id");
            return Task.FromResult(initialize);
        }
        if (method == "notifications/initialized")
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.Accepted));
        if (method == "tools/list")
        {
            ToolsListCalls++;
            if (Scenario == "mcp-list-cursor-empty")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"terac_get_context\",\"inputSchema\":{\"type\":\"object\",\"required\":[]}}],\"nextCursor\":\"\"}}"));
            if (Scenario == "mcp-list-cursor-whitespace")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"terac_get_context\",\"inputSchema\":{\"type\":\"object\",\"required\":[]}}],\"nextCursor\":\" \\t\"}}"));
            if (Scenario == "mcp-list-cursor-opaque")
            {
                if (ToolsListCalls == 1)
                    return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[],\"nextCursor\":\" cursor:/+?=value \"}}"));
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"tools\":[]}}"));
            }
            if (Scenario == "mcp-list-tools-null")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":null}}"));
            if (Scenario == "mcp-list-tools-zero")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":0}}"));
            if (Scenario == "mcp-list-tools-object")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":{\"name\":\"fixture\"}}}"));
            if (Scenario == "mcp-list-invalid-tool")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{}]}}"));
            if (Scenario == "mcp-list-missing-input-schema")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"fixture\"}]}}"));
            if (Scenario == "mcp-list-cased-tools")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"Tools\":[]}}"));
            if (Scenario == "mcp-list-invalid-annotation")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"terac_get_context\",\"inputSchema\":{\"type\":\"object\"},\"annotations\":{\"readOnlyHint\":\"invalid-boolean\"}}]}}"));
            if (Scenario == "mcp-list-cursor-zero")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[],\"nextCursor\":0}}"));
            if (Scenario == "mcp-list-empty")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[]}}"));
            if (Scenario == "mcp-list-singleton")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"fixture_tool\",\"inputSchema\":{\"type\":\"object\"}}]}}"));
            if (Scenario == "mcp-malformed-list")
                return Task.FromResult(Json(HttpStatusCode.OK, "sensitive-provider-value"));
            if (Scenario == "missing-context")
                return Task.FromResult(Json(HttpStatusCode.OK,
                    "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"terac_create_opportunity\",\"inputSchema\":{\"type\":\"object\"}}]}}"));
            if (Scenario == "composed-schema")
                return Task.FromResult(Json(HttpStatusCode.OK,
                    "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"terac_get_context\",\"inputSchema\":{\"type\":\"object\",\"allOf\":[{\"required\":[\"organization\"]}]}}]}}"));
            if (Scenario == "secret-tool-names")
                return Task.FromResult(Json(HttpStatusCode.OK,
                    "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[" +
                    "{\"name\":\"terac_get_context\",\"inputSchema\":{\"type\":\"object\",\"required\":[]}}," +
                    "{\"name\":\"" + ExpectedKey + "\",\"inputSchema\":{\"type\":\"object\"}}," +
                    "{\"name\":\"prefix-" + ExpectedKey + "-suffix\",\"inputSchema\":{\"type\":\"object\"}}," +
                    "{\"name\":\"PREFIX-" + ExpectedKey.ToUpperInvariant() + "-SUFFIX\",\"inputSchema\":{\"type\":\"object\"}}," +
                    "{\"name\":\"terac_create_opportunity\",\"inputSchema\":{\"type\":\"object\"}}]}}"));
            var required = Scenario == "unsafe-schema" ? "[\"brief\"]" : "[]";
            return Task.FromResult(Json(HttpStatusCode.OK,
                "{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"terac_get_context\",\"inputSchema\":{\"type\":\"object\",\"properties\":{},\"required\":" + required + "}},{\"name\":\"terac_create_opportunity\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"brief\":{\"type\":\"string\"}},\"required\":[\"brief\"]}}]}}"));
        }
        if (method == "tools/call")
        {
            var name = ReadToolName(body);
            ToolCallNames.Add(name);
            if (Scenario == "mcp-call-content-null")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":null}}"));
            if (Scenario == "mcp-call-content-zero")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":0}}"));
            if (Scenario == "mcp-call-content-object")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":{\"type\":\"text\",\"text\":\"fixture\"}}}"));
            if (Scenario == "mcp-call-cased-content")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"Content\":[]}}"));
            if (Scenario == "mcp-call-uppercase-image")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{\"type\":\"IMAGE\",\"data\":\"Zml4dHVyZQ==\",\"mimeType\":\"image/png\"}]}}"));
            if (Scenario == "mcp-call-uppercase-audio")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{\"type\":\"AUDIO\",\"data\":\"Zml4dHVyZQ==\",\"mimeType\":\"audio/wav\"}]}}"));
            if (Scenario == "mcp-call-invalid-block")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{}]}}"));
            if (Scenario == "mcp-call-text-missing")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{\"type\":\"text\"}]}}"));
            if (Scenario == "mcp-call-iserror-zero")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[],\"isError\":0}}"));
            if (Scenario == "mcp-call-structured-scalar")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[],\"structuredContent\":0}}"));
            if (Scenario == "mcp-call-empty")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[]}}"));
            if (Scenario == "mcp-call-singleton")
                return Task.FromResult(Json(HttpStatusCode.OK, "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"fixture\"}]}}"));
            return Task.FromResult(Json(HttpStatusCode.OK,
                "{\"jsonrpc\":\"2.0\",\"id\":3,\"result\":{\"content\":[{\"type\":\"text\",\"text\":\"sensitive-provider-value fixture-only-key\"}],\"isError\":false}}"));
        }
        return Task.FromResult(Json(HttpStatusCode.BadRequest, "sensitive-provider-value"));
    }

    private static string NewtonsoftJsonRead(string body)
    {
        var marker = "\"method\":\"";
        var start = body.IndexOf(marker, StringComparison.Ordinal);
        if (start < 0) return "";
        start += marker.Length;
        var end = body.IndexOf('"', start);
        return end < 0 ? "" : body.Substring(start, end - start);
    }

    private static string ReadToolName(string body)
    {
        var marker = "\"name\":\"";
        var start = body.IndexOf(marker, StringComparison.Ordinal);
        if (start < 0) return "";
        start += marker.Length;
        var end = body.IndexOf('"', start);
        return end < 0 ? "" : body.Substring(start, end - start);
    }
}
'@

$fixtureReferences = @([System.Net.Http.HttpClient].Assembly.Location)
Add-Type -TypeDefinition $fixtureHandlerSource -Language CSharp `
    -ReferencedAssemblies $fixtureReferences

foreach ($scriptName in @('setup_terac_credentials.ps1', 'probe_terac_access.ps1')) {
    $scriptPath = Join-Path (Split-Path $PSScriptRoot -Parent) $scriptName
    $tokens = $null
    $parseErrors = $null
    $scriptAst = [Management.Automation.Language.Parser]::ParseFile(
        $scriptPath, [ref]$tokens, [ref]$parseErrors)
    Assert-True ($parseErrors.Count -eq 0) ($scriptName + ' parses')
    if ($scriptName -eq 'setup_terac_credentials.ps1') {
        Assert-True ($scriptAst.ParamBlock.Parameters.Count -le 1 -and
            ($scriptAst.ParamBlock.Parameters.Count -eq 0 -or
                $scriptAst.ParamBlock.Parameters[0].Name.VariablePath.UserPath -ceq 'Replace')) 'setup exposes no secret or endpoint arguments'
    } else {
        Assert-True ($scriptAst.ParamBlock.Parameters.Count -eq 0) ($scriptName + ' accepts no secret or endpoint arguments')
    }
}

$setupSource = [IO.File]::ReadAllText((Join-Path (Split-Path $PSScriptRoot -Parent) 'setup_terac_credentials.ps1'))
Assert-True ($setupSource -match 'Read-Host[^\r\n]*-AsSecureString') 'credential setup uses a hidden terminal prompt'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('terac-access-tests-' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($tempRoot)
$fixtureKey = 'fixture-only-key'
$secret = ConvertTo-SecureString -String $fixtureKey -AsPlainText -Force
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $identity.User.Value

try {
    $platformError = $null
    try { Assert-TeracPlatform -Platform 'Unix' } catch { $platformError = $_ }
    Assert-True ($null -ne $platformError -and
        $platformError.Exception.Message -match 'requires Windows DPAPI') 'non-Windows platform is rejected for the expected reason'

    $credentialDirectory = Join-Path $tempRoot 'credential-store'
    Assert-Throws { Save-TeracCredential -Secret $secret -StoreDirectory '.\relative-store' } 'relative storage path'
    $defaultStore = & (Get-Module -Name TeracAccess) { Get-TeracStoreDirectory -StoreDirectory $null }
    $expectedStore = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'TeracCookbook'
    Assert-True ([IO.Path]::GetFullPath($defaultStore) -eq [IO.Path]::GetFullPath($expectedStore)) 'default store is fixed beneath local application data'
    Assert-True (-not $defaultStore.StartsWith((Get-Location).Path, [StringComparison]::OrdinalIgnoreCase)) 'default store is outside checkout'
    Assert-Throws {
        $empty = New-Object Security.SecureString
        try { Save-TeracCredential -Secret $empty -StoreDirectory $credentialDirectory }
        finally { $empty.Dispose() }
    } 'empty secure input'

    Save-TeracCredential -Secret $secret -StoreDirectory $credentialDirectory
    $credentialFile = Join-Path $credentialDirectory 'terac-api-key.dpapi'
    Assert-True (Test-Path -LiteralPath $credentialFile -PathType Leaf) 'encrypted credential exists'
    $ciphertext = [IO.File]::ReadAllText($credentialFile)
    Assert-True ($ciphertext -notmatch [regex]::Escape($fixtureKey)) 'credential file does not contain fixture key'
    $directoryAcl = Get-Acl -LiteralPath $credentialDirectory
    Assert-True ($directoryAcl.AreAccessRulesProtected) 'store ACL inheritance is disabled'
    Assert-True ($directoryAcl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $userSid) 'store is owned by current user'
    $stored = Read-TeracCredential -StoreDirectory $credentialDirectory
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($stored)
    try {
        $roundTrip = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        Assert-True ($roundTrip -ceq $fixtureKey) 'DPAPI user-scope round trip'
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $stored.Dispose()
        $roundTrip = $null
    }

    $beforeNoOverwrite = [Convert]::ToBase64String([IO.File]::ReadAllBytes($credentialFile))
    Assert-Throws { Save-TeracCredential -Secret $secret -StoreDirectory $credentialDirectory } 'existing credential is not overwritten'
    $afterNoOverwrite = [Convert]::ToBase64String([IO.File]::ReadAllBytes($credentialFile))
    Assert-True ($beforeNoOverwrite -ceq $afterNoOverwrite) 'existing credential bytes are unchanged'
    Assert-ThrowsWithMessage {
        Assert-TeracCredentialSetupReady -StoreDirectory $credentialDirectory
    } 'A key is already saved. Rerun setup with -Replace to replace it.' 'existing-key preflight explains explicit replacement'
    Assert-TeracCredentialSetupReady -StoreDirectory $credentialDirectory -Replace | Out-Null

    $replacementText = 'replacement-fixture-key'
    $replacementSecret = ConvertTo-SecureString -String $replacementText -AsPlainText -Force
    $emptyReplacement = New-Object Security.SecureString
    try {
        Assert-Throws {
            Save-TeracCredential -Secret $emptyReplacement -StoreDirectory $credentialDirectory -Replace
        } 'empty replacement input'
    } finally {
        $emptyReplacement.Dispose()
    }
    Assert-True ($beforeNoOverwrite -ceq [Convert]::ToBase64String([IO.File]::ReadAllBytes($credentialFile))) 'empty replacement preserves existing encrypted bytes'
    $lockedFile = [IO.File]::Open($credentialFile, [IO.FileMode]::Open,
        [IO.FileAccess]::Read, [IO.FileShare]::None)
    try {
        Assert-ThrowsWithMessage {
            Save-TeracCredential -Secret $replacementSecret -StoreDirectory $credentialDirectory -Replace
        } 'Atomic credential replacement failed before the saved file moved.' 'locked destination leaves the saved file in place'
    } finally {
        $lockedFile.Dispose()
    }
    $afterFailedReplacement = [Convert]::ToBase64String([IO.File]::ReadAllBytes($credentialFile))
    Assert-True ($beforeNoOverwrite -ceq $afterFailedReplacement) 'failed atomic replacement preserves existing encrypted bytes'
    $storedAfterFailure = Read-TeracCredential -StoreDirectory $credentialDirectory
    $failedReplacementBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($storedAfterFailure)
    try {
        Assert-True ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($failedReplacementBstr) -ceq $fixtureKey) 'failed replacement retains the prior key'
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($failedReplacementBstr)
        $storedAfterFailure.Dispose()
    }

    Save-TeracCredential -Secret $replacementSecret -StoreDirectory $credentialDirectory -Replace
    $replacedCiphertext = [IO.File]::ReadAllText($credentialFile)
    Assert-True ($replacedCiphertext -cne $ciphertext) 'successful replacement changes encrypted bytes'
    $storedReplacement = Read-TeracCredential -StoreDirectory $credentialDirectory
    $replacementBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($storedReplacement)
    try {
        Assert-True ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($replacementBstr) -ceq $replacementText) 'explicit replacement stores the new key'
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($replacementBstr)
        $storedReplacement.Dispose()
        $replacementSecret.Dispose()
    }
    Assert-True (@(Get-ChildItem -LiteralPath $credentialDirectory -Force).Count -eq 1) 'replacement leaves no staging or backup files'

    $module = Get-Module -Name TeracAccess
    $previousAtomicOperation = & $module { $script:AtomicReplaceForTest }
    $restoreState = [pscustomobject]@{ BackupPath = $null }
    $simulateBackupMoved = {
        param($SourcePath, $TargetPath, $BackupPath)
        $restoreState.BackupPath = $BackupPath
        [IO.File]::Move($TargetPath, $BackupPath)
        throw 'synthetic post-backup-move state'
    }.GetNewClosure()
    & $module { param($operation) $script:AtomicReplaceForTest = $operation } $simulateBackupMoved
    try {
        Assert-ThrowsWithMessage {
            Save-TeracCredential -Secret $secret -StoreDirectory $credentialDirectory -Replace
        } 'Atomic credential replacement failed; the previous credential was restored.' 'post-backup-move state restores the previous file'
    } finally {
        & $module { param($operation) $script:AtomicReplaceForTest = $operation } $previousAtomicOperation
    }
    Assert-True (-not (Test-Path -LiteralPath $restoreState.BackupPath)) 'restored backup no longer occupies its temporary path'
    $restoredSecret = Read-TeracCredential -StoreDirectory $credentialDirectory
    $restoredBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($restoredSecret)
    try {
        Assert-True ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($restoredBstr) -ceq $replacementText) 'restored file still decrypts to the prior key'
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($restoredBstr)
        $restoredSecret.Dispose()
    }

    $recoveryState = [pscustomobject]@{ BackupPath = $null }
    $simulateRestoreBlocked = {
        param($SourcePath, $TargetPath, $BackupPath)
        $recoveryState.BackupPath = $BackupPath
        [IO.File]::Move($TargetPath, $BackupPath)
        [void][IO.Directory]::CreateDirectory($TargetPath)
        throw 'synthetic post-backup-move state with blocked restoration'
    }.GetNewClosure()
    & $module { param($operation) $script:AtomicReplaceForTest = $operation } $simulateRestoreBlocked
    try {
        Assert-ThrowsWithMessage {
            Save-TeracCredential -Secret $secret -StoreDirectory $credentialDirectory -Replace
        } 'Credential recovery is required; protected recovery data was retained where available.' 'failed restore reports recovery required'
    } finally {
        & $module { param($operation) $script:AtomicReplaceForTest = $operation } $previousAtomicOperation
    }
    Assert-True (Test-Path -LiteralPath $recoveryState.BackupPath -PathType Leaf) 'failed restore retains prior encrypted backup'
    & $module { param($backupPath) Assert-TeracAcl -Path $backupPath -Kind File } $recoveryState.BackupPath
    $backupSecure = ConvertTo-SecureString -String ([IO.File]::ReadAllText($recoveryState.BackupPath))
    $backupBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($backupSecure)
    try {
        Assert-True ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($backupBstr) -ceq $replacementText) 'retained backup remains decryptable to the prior key'
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($backupBstr)
        $backupSecure.Dispose()
    }

    $setupAst = [Management.Automation.Language.Parser]::ParseFile(
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'setup_terac_credentials.ps1'),
        [ref]$tokens, [ref]$parseErrors)
    Assert-True ($setupAst.ParamBlock.Parameters.Count -eq 1 -and
        $setupAst.ParamBlock.Parameters[0].Name.VariablePath.UserPath -ceq 'Replace' -and
        $setupAst.ParamBlock.Parameters[0].StaticType -eq [System.Management.Automation.SwitchParameter]) 'setup exposes only the explicit Replace switch'
    Assert-True ($setupSource.IndexOf('Assert-TeracCredentialSetupReady') -ge 0 -and
        $setupSource.IndexOf('Assert-TeracCredentialSetupReady') -lt $setupSource.IndexOf('Read-Host')) 'storage preflight occurs before hidden input'
    Assert-True ($setupSource -match 'Assert-TeracCredentialSetupReady[^\r\n]*-Replace:\$Replace' -and
        $setupSource -match 'Save-TeracCredential[^\r\n]*-Replace:\$Replace') 'explicit replacement switch reaches preflight and save'

    $unsafeDirectory = Join-Path $tempRoot 'unsafe-store'
    Save-TeracCredential -Secret $secret -StoreDirectory $unsafeDirectory
    $everyoneSid = New-Object Security.Principal.SecurityIdentifier('S-1-1-0')
    $everyoneInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $everyoneRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $everyoneSid, [Security.AccessControl.FileSystemRights]::ReadAndExecute,
        $everyoneInheritance, [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow)
    $unsafeAcl = New-Object Security.AccessControl.DirectorySecurity
    $unsafeAcl.SetAccessRuleProtection($true, $false)
    $unsafeAcl.SetOwner($identity.User)
    $currentUserRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $identity.User, [Security.AccessControl.FileSystemRights]::FullControl,
        ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit),
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow)
    $unsafeAcl.SetAccessRule($currentUserRule)
    $unsafeAcl.AddAccessRule($everyoneRule)
    [IO.Directory]::SetAccessControl($unsafeDirectory, $unsafeAcl)
    Assert-Throws { Read-TeracCredential -StoreDirectory $unsafeDirectory } 'unexpected directory ACL'
    Assert-ThrowsWithMessage {
        Assert-TeracCredentialSetupReady -StoreDirectory $unsafeDirectory -Replace
    } 'Credential storage is unsafe or unavailable; no key was requested.' 'unsafe storage is rejected before setup input'

    $unsafeFileDirectory = Join-Path $tempRoot 'unsafe-file-store'
    Save-TeracCredential -Secret $secret -StoreDirectory $unsafeFileDirectory
    $unsafeCredentialFile = Join-Path $unsafeFileDirectory 'terac-api-key.dpapi'
    $unsafeFileAcl = New-Object Security.AccessControl.FileSecurity
    $unsafeFileAcl.SetAccessRuleProtection($true, $false)
    $unsafeFileAcl.SetOwner($identity.User)
    $currentFileRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $identity.User, [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow)
    $unsafeFileAcl.SetAccessRule($currentFileRule)
    $unsafeReadRule = [Security.AccessControl.FileSystemAccessRule]::new(
        $everyoneSid, [Security.AccessControl.FileSystemRights]::Read,
        [Security.AccessControl.AccessControlType]::Allow)
    $unsafeFileAcl.AddAccessRule($unsafeReadRule)
    [IO.File]::SetAccessControl($unsafeCredentialFile, $unsafeFileAcl)
    Assert-Throws { Read-TeracCredential -StoreDirectory $unsafeFileDirectory } 'unexpected credential-file ACL'

    $junctionTarget = Join-Path $tempRoot 'junction-target'
    $junctionPath = Join-Path $tempRoot 'junction-store'
    [void][IO.Directory]::CreateDirectory($junctionTarget)
    $junction = New-Item -ItemType Junction -Path $junctionPath -Target $junctionTarget
    try {
        Assert-ThrowsWithMessage {
            Assert-TeracCredentialSetupReady -StoreDirectory $junctionPath -Replace
        } 'Credential storage is unsafe or unavailable; no key was requested.' 'reparse storage is rejected before setup input'
        Assert-Throws { Save-TeracCredential -Secret $secret -StoreDirectory $junctionPath } 'reparse-point storage path'
    } finally {
        Remove-Item -LiteralPath $junctionPath -Force
    }

    $corruptDirectory = Join-Path $tempRoot 'corrupt-store'
    Save-TeracCredential -Secret $secret -StoreDirectory $corruptDirectory
    $corruptFile = Join-Path $corruptDirectory 'terac-api-key.dpapi'
    [IO.File]::WriteAllText($corruptFile, 'not-a-dpapi-blob')
    Assert-Throws { Read-TeracCredential -StoreDirectory $corruptDirectory } 'corrupt credential blob'

    $transport = New-TeracProbeTransport
    try {
        Assert-True (-not $transport.Handler.AllowAutoRedirect) 'transport disables redirects'
        Assert-True (-not $transport.Handler.UseCookies) 'transport does not use cookies'
    } finally {
        $transport.Client.Dispose()
        $transport.Handler.Dispose()
    }

    $successHandler = New-Object TeracFixtureHandler('success', $fixtureKey)
    $successClient = New-Object System.Net.Http.HttpClient($successHandler)
    try {
        $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $successClient
        $safeOutput = $probe | ConvertTo-Json -Depth 8 -Compress
        Assert-True ($successHandler.Uris.Count -eq 5) ("read-only probe request count (actual=" + $successHandler.Uris.Count + ", MCP error=" + $probe.mcp.error + ")")
        Assert-True (@($successHandler.Uris | Where-Object { $_ -notmatch '^https://terac\.com/api/(external/v2/organizations/current/context|mcp)$' }).Count -eq 0) 'fixed first-party endpoints only'
        Assert-True (@($successHandler.KeyHeadersMatched | Where-Object { -not $_ }).Count -eq 0) 'API key sent only in expected auth headers'
        Assert-True ($successHandler.SessionHeadersPresent[0] -eq $false) 'no MCP session before initialize'
        Assert-True ($successHandler.SessionHeadersPresent[2] -and $successHandler.SessionHeadersPresent[3] -and $successHandler.SessionHeadersPresent[4]) 'MCP session reused privately'
        Assert-True ($successHandler.ToolCallNames.Count -eq 1 -and $successHandler.ToolCallNames[0] -ceq 'terac_get_context') 'only allowlisted context tool called'
        Assert-True ($successHandler.Bodies[-1] -match '"arguments":\{\}') 'allowlisted context call has empty arguments only'
        Assert-True ($probe.mcp.context_read.attempted -and -not $probe.mcp.context_read.is_error) 'MCP context read result is captured safely'
        Assert-True ($safeOutput -notmatch 'fixture-only-key|sensitive-provider-value|private-id|private-name|private-slug|12345|private-session-id|private-server-name') 'provider values and secrets are omitted'
        Assert-True ($safeOutput -match 'terac_get_context' -and $safeOutput -match 'terac_create_opportunity') 'advertised tool names are reported'
    } finally {
        $successClient.Dispose()
        $successHandler.Dispose()
    }

    foreach ($scenario in @('unsafe-schema', 'composed-schema', 'missing-context')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            Assert-True ($handler.ToolCallNames.Count -eq 0) ($scenario + ' does not call a tool')
            Assert-True ($probe.mcp.context_read.attempted -eq $false) ($scenario + ' reports no context invocation')
            if ($scenario -eq 'composed-schema') {
                Assert-True ($probe.mcp.error -eq 'context_schema') 'composed context schema is rejected fail closed'
            }
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    foreach ($scenario in @('mcp-init-missing-capabilities', 'mcp-init-null-capabilities', 'mcp-init-missing-tools-capability', 'mcp-init-invalid-tools-capability', 'mcp-init-cased-tools', 'mcp-init-cased-protocol-version', 'mcp-init-tools-false', 'mcp-init-invalid-list-changed', 'mcp-init-invalid-server-info', 'mcp-init-numeric-version')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            Assert-True (-not $probe.mcp.initialized -and $null -eq $probe.mcp.protocol_version) ($scenario + ' leaves initialization unknown')
            Assert-True ($probe.mcp.error -eq 'initialize_response') ($scenario + ' reports a sanitized initialization-shape error')
            Assert-True ($handler.RpcMethods.Count -eq 1 -and $handler.ToolCallNames.Count -eq 0) ($scenario + ' stops before notification or later requests')
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    foreach ($scenario in @('mcp-list-tools-null', 'mcp-list-tools-zero', 'mcp-list-tools-object', 'mcp-list-invalid-tool', 'mcp-list-missing-input-schema', 'mcp-list-cased-tools', 'mcp-list-invalid-annotation', 'mcp-list-cursor-zero', 'mcp-list-cursor-empty', 'mcp-list-cursor-whitespace')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            Assert-True (-not $probe.mcp.catalog_complete -and @($probe.mcp.tools).Count -eq 0) ($scenario + ' leaves the catalog incomplete and empty')
            Assert-True ($probe.mcp.error -eq 'tools_list') ($scenario + ' reports a sanitized catalog-shape error')
            Assert-True (-not $probe.mcp.context_read.attempted -and $handler.ToolCallNames.Count -eq 0) ($scenario + ' stops before context invocation')
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    foreach ($scenario in @('mcp-call-content-null', 'mcp-call-content-zero', 'mcp-call-content-object', 'mcp-call-cased-content', 'mcp-call-uppercase-image', 'mcp-call-uppercase-audio', 'mcp-call-invalid-block', 'mcp-call-text-missing', 'mcp-call-iserror-zero', 'mcp-call-structured-scalar')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            Assert-True ($probe.mcp.context_read.attempted -and $probe.mcp.error -eq 'context_read') ($scenario + ' records the attempted request and sanitized shape error')
            Assert-True ($null -eq $probe.mcp.context_read.is_error -and $null -eq $probe.mcp.context_read.content_count -and $null -eq $probe.mcp.context_read.structured_content_present) ($scenario + ' leaves malformed result facts unknown')
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    foreach ($scenario in @('mcp-list-empty', 'mcp-list-singleton')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            Assert-True ($probe.mcp.catalog_complete -and $null -eq $probe.mcp.error) ($scenario + ' accepts a valid array response')
            Assert-True (@($probe.mcp.tools).Count -eq $(if ($scenario -eq 'mcp-list-empty') { 0 } else { 1 })) ($scenario + ' reports the valid array cardinality')
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    $opaqueCursorHandler = New-Object TeracFixtureHandler('mcp-list-cursor-opaque', $fixtureKey)
    $opaqueCursorClient = New-Object System.Net.Http.HttpClient($opaqueCursorHandler)
    try {
        $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $opaqueCursorClient
        $listBodies = @($opaqueCursorHandler.Bodies | Where-Object { $_ -match '"method"\s*:\s*"tools/list"' })
        $secondListRequest = $listBodies[1] | ConvertFrom-Json -ErrorAction Stop
        Assert-True ($probe.mcp.catalog_complete -and $null -eq $probe.mcp.error) 'opaque cursor page completes only after the cursor is omitted'
        Assert-True ($opaqueCursorHandler.ToolsListCalls -eq 2 -and $listBodies.Count -eq 2) 'opaque cursor is followed by exactly one page'
        Assert-True ($secondListRequest.params.cursor -ceq ' cursor:/+?=value ') 'nonblank opaque cursor is preserved exactly'
        Assert-True (($probe | ConvertTo-Json -Depth 8 -Compress) -notmatch 'cursor:/\+\?=value') 'opaque cursor is not included in diagnostic output'
        Assert-True ($opaqueCursorHandler.ToolCallNames.Count -eq 0) 'empty cursor fixture never invokes a context tool'
    } finally {
        $opaqueCursorClient.Dispose()
        $opaqueCursorHandler.Dispose()
    }

    foreach ($scenario in @('mcp-call-empty', 'mcp-call-singleton')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            Assert-True ($null -eq $probe.mcp.error) ($scenario + ' accepts a valid tool result')
            Assert-True (-not $probe.mcp.context_read.is_error) ($scenario + ' defaults omitted isError only after valid result shape')
            Assert-True ($probe.mcp.context_read.content_count -eq $(if ($scenario -eq 'mcp-call-empty') { 0 } else { 1 })) ($scenario + ' reports valid content cardinality')
            Assert-True ($probe.mcp.context_read.structured_content_present -eq $false) ($scenario + ' reports absent structured content after validation')
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    $secretNameHandler = New-Object TeracFixtureHandler('secret-tool-names', $fixtureKey)
    $secretNameClient = New-Object System.Net.Http.HttpClient($secretNameHandler)
    try {
        $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $secretNameClient
        $safeOutput = $probe | ConvertTo-Json -Depth 8 -Compress
        Assert-True ($safeOutput.IndexOf($fixtureKey, [StringComparison]::OrdinalIgnoreCase) -lt 0) 'tool names cannot echo the credential exactly or as a substring in another case'
        Assert-True (@($probe.mcp.tools | Where-Object { $_ -eq 'terac_get_context' -or $_ -eq 'terac_create_opportunity' }).Count -eq 2) 'safe advertised tool names remain visible'
        Assert-True ($secretNameHandler.ToolCallNames.Count -eq 1 -and $secretNameHandler.ToolCallNames[0] -ceq 'terac_get_context') 'diagnostic redaction does not change allowlisted tool selection'
    } finally {
        $secretNameClient.Dispose()
        $secretNameHandler.Dispose()
    }

    foreach ($scenario in @('redirect-rest', 'rest-error', 'rest-malformed', 'rest-oversized', 'rest-scalar-number', 'rest-scalar-boolean', 'mcp-error', 'mcp-malformed-list', 'unsupported-version', 'mcp-id-boolean', 'mcp-id-fraction', 'mcp-id-string', 'mcp-wrong-rpc-version', 'mcp-missing-result', 'mcp-both-result-and-error', 'mcp-invalid-result', 'mcp-malformed-error', 'mcp-sse-id-string', 'mcp-sse-malformed-error')) {
        $handler = New-Object TeracFixtureHandler($scenario, $fixtureKey)
        $client = New-Object System.Net.Http.HttpClient($handler)
        try {
            $probe = Invoke-TeracReadOnlyProbe -Secret $secret -HttpClient $client
            $safeOutput = $probe | ConvertTo-Json -Depth 8 -Compress
            Assert-True ($safeOutput -notmatch 'sensitive-provider-value|fixture-only-key|evil\.example') ($scenario + ' response is sanitized')
            Assert-True (@($handler.Uris | Where-Object { $_ -match 'evil\.example' }).Count -eq 0) ($scenario + ' does not follow redirects')
            if ($scenario -eq 'redirect-rest') {
                Assert-True (@($handler.Uris | Where-Object { $_ -match '/organizations/current/context' }).Count -eq 1) 'redirect is not retried'
            }
            if ($scenario -in @('rest-scalar-number', 'rest-scalar-boolean')) {
                Assert-True (-not $probe.rest.context_received -and -not $probe.rest.response_shape_valid) ($scenario + ' rejects scalar REST context')
                Assert-True ($probe.rest.error -eq 'invalid_context_response') ($scenario + ' reports sanitized invalid context')
            }
            if ($scenario -in @('mcp-error', 'unsupported-version', 'mcp-id-boolean', 'mcp-id-fraction', 'mcp-id-string', 'mcp-wrong-rpc-version', 'mcp-missing-result', 'mcp-both-result-and-error', 'mcp-invalid-result', 'mcp-malformed-error', 'mcp-sse-id-string', 'mcp-sse-malformed-error')) {
                Assert-True ($handler.RpcMethods.Count -eq 1 -and $handler.ToolCallNames.Count -eq 0) ($scenario + ' stops without fallback or tool invocation')
            }
        } finally {
            $client.Dispose()
            $handler.Dispose()
        }
    }

    Write-Output 'PowerShell access seam tests passed.'
} finally {
    $secret.Dispose()
    if (Test-Path -LiteralPath $tempRoot) {
        $canonicalTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        $canonicalTarget = [IO.Path]::GetFullPath($tempRoot)
        if (-not $canonicalTarget.StartsWith($canonicalTemp, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Refusing to remove test data outside the temporary root.'
        }
        Remove-Item -LiteralPath $canonicalTarget -Recurse -Force
    }
}
