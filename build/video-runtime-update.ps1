param([Parameter(Mandatory)][ValidateSet('Prepare', 'Restore', 'Verify', 'Recover')][string]$Mode)

$ErrorActionPreference = 'Stop'

function Assert-PlainPath([string]$Path) {
    for ($cursor = $Path; $cursor; $cursor = [IO.Path]::GetDirectoryName($cursor)) {
        if ([IO.Directory]::Exists($cursor) -or [IO.File]::Exists($cursor)) {
            if ([IO.File]::GetAttributes($cursor) -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Video runtime path contains a filesystem link.'
            }
        }
    }
}

function Test-VideoRuntime([string]$Directory) {
    if (-not [IO.Directory]::Exists($Directory)) { return $false }
    $expected = ConvertFrom-Json ([IO.File]::ReadAllText($env:SIGNALDESK_RUNTIME_MANIFEST))
    if ($expected.schema -ne 2 -or $expected.kind -ne 'maiyi-bundled-video-runtime') {
        throw 'Invalid packaged video runtime manifest.'
    }
    $files = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Directory)
    while ($pending.Count) {
        foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($pending.Pop())) {
            $attributes = [IO.File]::GetAttributes($entry)
            if ($attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Video runtime contains a filesystem link.'
            }
            if ($attributes -band [IO.FileAttributes]::Directory) {
                $pending.Push($entry)
            } else {
                $relative = $entry.Substring($Directory.Length + 1).Replace('\', '/')
                $files.Add($relative, $entry)
            }
        }
    }
    if (-not $files.ContainsKey('manifest.json') -or $files.Count -ne $expected.files.Count + 1) { return $false }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        foreach ($member in $expected.files) {
            $file = ''
            if (-not $files.TryGetValue([string]$member.path, [ref]$file) -or
                [IO.FileInfo]::new($file).Length -ne $member.bytes) { return $false }
            $stream = [IO.File]::OpenRead($file)
            try { $actual = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
            finally { $stream.Dispose() }
            if ($actual -ne $member.sha256) { return $false }
        }
        return $true
    } finally {
        $sha.Dispose()
    }
}

try {
    $install = [IO.Path]::GetFullPath($env:SIGNALDESK_RUNTIME_INSTALL).TrimEnd('\')
    if (-not [IO.Path]::GetDirectoryName($install)) { throw 'Invalid application install directory.' }
    $resources = [IO.Path]::Combine($install, 'resources')
    $runtime = [IO.Path]::Combine($resources, 'video-runtime')
    # A sibling remains on the same volume and survives the previous NSIS uninstaller.
    $staged = $install + '.signaldesk-video-runtime'
    Assert-PlainPath $runtime
    Assert-PlainPath $staged
    if ($Mode -eq 'Prepare' -or $Mode -eq 'Recover' -or $Mode -eq 'Restore') {
        if ([IO.Directory]::Exists($staged)) {
            if ([IO.Directory]::Exists($runtime)) { throw 'An earlier video runtime update is still staged.' }
            [IO.Directory]::CreateDirectory($resources) | Out-Null
            [IO.Directory]::Move($staged, $runtime)
        } elseif ($Mode -eq 'Restore') {
            throw 'The staged video runtime is missing.'
        }
    }
    if ($Mode -eq 'Prepare') {
        if (-not (Test-VideoRuntime $runtime)) { exit 2 }
        [IO.Directory]::Move($runtime, $staged)
    }
    if ($Mode -eq 'Verify' -and -not (Test-VideoRuntime $runtime)) { throw 'Video runtime extraction is incomplete.' }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
