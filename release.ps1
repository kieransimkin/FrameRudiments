$ErrorActionPreference = "Stop"
$version = "0.4.0"
$tag = "v$version"

function Check {
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE)"
    }
}

function Wait-CI([string]$sha) {
    Write-Host "Waiting for CI: $sha"

    while ($true) {
        $runs = @(gh run list --workflow ci.yml `
            --commit $sha --limit 20 `
            --json status,conclusion,headSha,event |
            ConvertFrom-Json)
        Check

        $runs = @($runs | Where-Object {
            $_.headSha -eq $sha -and $_.event -eq "push"
        })

        if ($runs.Count -gt 0) {
            if (@($runs | Where-Object {
                $_.status -eq "completed" -and
                $_.conclusion -ne "success"
            }).Count -gt 0) {
                throw "CI failed"
            }

            if (@($runs | Where-Object {
                $_.status -ne "completed"
            }).Count -eq 0) {
                Write-Host "CI passed"
                return
            }
        }

        Start-Sleep -Seconds 30
    }
}

git switch main
Check
git pull --ff-only origin main
Check

# Ignore this script when checking for a clean tree
$dirty = @(git status --porcelain |
    Where-Object { $_ -notmatch 'release\.ps1$' })

if ($dirty.Count -gt 0) {
    throw "Working tree contains uncommitted files"
}

$path = "pyproject.toml"
$content = [IO.File]::ReadAllText($path)
$updated = $content.Replace(
    'version = "0.3.0"',
    'version = "0.4.0"'
)
if ($updated -eq $content) {
    throw "Expected version 0.3.0 not found"
}

[IO.File]::WriteAllText($path, $updated)

git add pyproject.toml
git commit -m "Bump version to v0.4.0"
Check

git push origin main
Check

$sha = (git rev-parse HEAD).Trim()
Wait-CI $sha

git tag -a $tag -m "FrameRudiments v0.4.0"
Check
git push origin $tag
Check

Write-Host "Release tag published: $tag"
Write-Host "View: https://github.com/kieransimkin/FrameRudiments/actions"
