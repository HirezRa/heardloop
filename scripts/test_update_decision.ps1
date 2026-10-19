# Unit-style checks for update-script decision matrix (no Docker required).
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_update_decision.ps1
$ErrorActionPreference = 'Stop'

function Get-UpdateDecision([string]$Running, [string]$Files, [string]$Target) {
    # Running empty => stack down / unknown.
    $stackDown = [string]::IsNullOrEmpty($Running)
    $needCheckout = ($Files -ne $Target)
    $needRebuild = $stackDown -or ($Running -ne $Target)
    if (-not $needRebuild) { return 'up_to_date' }
    if (-not $needCheckout) { return 'rebuild_only' }
    return 'checkout_and_rebuild'
}

$cases = @(
    @{ Name = 'running older + files current'; Running = 'v0.9.0'; Files = 'v0.9.1'; Target = 'v0.9.1'; Expect = 'rebuild_only' },
    @{ Name = 'all equal'; Running = 'v0.9.1'; Files = 'v0.9.1'; Target = 'v0.9.1'; Expect = 'up_to_date' },
    @{ Name = 'stack down'; Running = ''; Files = 'v0.9.1'; Target = 'v0.9.1'; Expect = 'rebuild_only' },
    @{ Name = 'files behind'; Running = 'v0.9.0'; Files = 'v0.9.0'; Target = 'v0.9.1'; Expect = 'checkout_and_rebuild' }
)

$failed = 0
foreach ($c in $cases) {
    $got = Get-UpdateDecision -Running $c.Running -Files $c.Files -Target $c.Target
    if ($got -ne $c.Expect) {
        Write-Host "FAIL $($c.Name): got=$got expect=$($c.Expect)"
        $failed++
    } else {
        Write-Host "ok   $($c.Name) -> $got"
    }
}
if ($failed -gt 0) { exit 1 }
Write-Host "All $($cases.Count) decision cases passed."
